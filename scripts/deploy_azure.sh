#!/usr/bin/env bash
# Manual deployment: run after the test suite passes.
# Required: APP_PASSWORD, SESSION_SECRET, ANTHROPIC_API_KEY (environment only).
# Prerequisites: az login, a selected Azure subscription, Python 3.
# Image compilation is delegated to az containerapp up source-build support.
# Optional: REDLINE_RESOURCE_GROUP=rg-redline, REDLINE_LOCATION=uksouth,
# REDLINE_APP_NAME=redline, REDLINE_ENVIRONMENT=redline-env.
# Optional prebuilt-image fallback: REDLINE_IMAGE and REDLINE_REGISTRY_SERVER.
# Build with `az acr build --platform linux/amd64` when the installed `up`
# source-build helper is incompatible with its registry SDK.
# Durable storage: every review is stored in an Azure Blob container; the app
# reaches it with its managed identity (no storage keys). DATA_DIR is only a
# working copy. Optional: REDLINE_STORAGE_ACCOUNT (default derived, globally unique).
set -euo pipefail
# Never trace secret-bearing commands, even when invoked through bash -x.
set +x
umask 077

fail() { printf '%s\n' "$1" >&2; exit 1; }
for tool in az python3; do command -v "$tool" >/dev/null || fail "Required tool missing: $tool"; done
[[ -n "${APP_PASSWORD:-}" ]] || fail 'APP_PASSWORD must be set'
[[ -n "${ANTHROPIC_API_KEY:-}" ]] || fail 'ANTHROPIC_API_KEY must be set'
session_secret="${SESSION_SECRET:-}"
[[ ${#session_secret} -ge 32 ]] || fail 'SESSION_SECRET must contain at least 32 characters'

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$repo_root/backend/app/main.py" ]] || fail 'backend/app/main.py missing'
[[ -f "$repo_root/Dockerfile" ]] || fail 'Dockerfile is missing'
[[ -f "$repo_root/backend/requirements.txt" ]] || fail 'Backend dependency manifest is missing'
[[ -f "$repo_root/frontend/package.json" ]] || fail 'frontend/package.json missing'
[[ -f "$repo_root/frontend/package-lock.json" ]] || fail 'frontend/package-lock.json missing'
resource_group="${REDLINE_RESOURCE_GROUP:-rg-redline}"
location="${REDLINE_LOCATION:-uksouth}"
app_name="${REDLINE_APP_NAME:-redline}"
environment="${REDLINE_ENVIRONMENT:-redline-env}"
deployment_source=(--source "$repo_root")
if [[ -n "${REDLINE_IMAGE:-}" ]]; then
    deployment_source=(--image "$REDLINE_IMAGE")
    if [[ -n "${REDLINE_REGISTRY_SERVER:-}" ]]; then
        deployment_source+=(--registry-server "$REDLINE_REGISTRY_SERVER")
    fi
elif [[ -n "${REDLINE_REGISTRY_SERVER:-}" ]]; then
    fail 'REDLINE_REGISTRY_SERVER requires REDLINE_IMAGE'
fi

az account show --only-show-errors --output none
# The legacy Blob mirror is not a transactional multi-replica store. Never run
# this path over an app that has activated PostgreSQL/leased jobs.
preflight_file="$(mktemp "${TMPDIR:-/tmp}/redline-preflight.XXXXXXXX")"
trap 'rm -f -- "$preflight_file"' EXIT
if az containerapp show --name "$app_name" --resource-group "$resource_group" --output json > "$preflight_file" 2>/dev/null; then
    python3 - "$preflight_file" <<'PYGUARD'
import json,sys
app=json.load(open(sys.argv[1]))
for c in (app['properties']['template'].get('containers') or []):
    if any(e['name'] in {'DATABASE_URL','REDLINE_DURABLE_JOBS'} for e in (c.get('env') or [])):
        raise SystemExit('Durable deployment is active; use scripts/deploy_durable_azure.py. Refusing legacy deployment.')
PYGUARD
fi
rm -f -- "$preflight_file"
trap - EXIT
# Internal ingress prevents public access during first-time secret configuration.
# Repeating up updates the same named app/environment rather than creating copies.
# The containerapp extension rejects explicit --output/--only-show-errors on
# `up`; use Azure's output environment setting instead of those global flags.
AZURE_CORE_OUTPUT=none az containerapp up --name "$app_name" --resource-group "$resource_group" \
    --environment "$environment" --location "$location" "${deployment_source[@]}" \
    --ingress internal --target-port 8000

# Durable review storage: a private account, reached only through the app's identity.
subscription_id="$(az account show --query id --output tsv --only-show-errors)"
storage_account="${REDLINE_STORAGE_ACCOUNT:-redline$(printf '%s' "$subscription_id$resource_group" | python3 -c 'import hashlib,sys;print(hashlib.sha1(sys.stdin.read().encode()).hexdigest()[:12])')}"
[[ "$storage_account" =~ ^[a-z0-9]{3,24}$ ]] || fail 'REDLINE_STORAGE_ACCOUNT must be 3-24 lowercase letters or digits'
az storage account create --name "$storage_account" --resource-group "$resource_group" --location "$location" \
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false \
    --only-show-errors --output none
storage_id="$(az storage account show --name "$storage_account" --resource-group "$resource_group" \
    --query id --output tsv --only-show-errors)"
principal_id="$(az containerapp identity assign --name "$app_name" --resource-group "$resource_group" \
    --system-assigned --query principalId --output tsv --only-show-errors)"
[[ -n "$principal_id" ]] || fail 'Azure did not return the app identity'
az storage container create --account-name "$storage_account" --name redline --auth-mode login --public-access off --only-show-errors --output none
container_scope="$storage_id/blobServices/default/containers/redline"
if [[ -z "$(az role assignment list --assignee "$principal_id" --scope "$container_scope" \
        --role 'Storage Blob Data Contributor' --query '[0].id' --output tsv --only-show-errors)" ]]; then
    az role assignment create --assignee-object-id "$principal_id" --assignee-principal-type ServicePrincipal \
        --role 'Storage Blob Data Contributor' --scope "$container_scope" --only-show-errors --output none
fi
export REDLINE_STORAGE_URL="https://$storage_account.blob.core.windows.net"

config_dir="$(mktemp -d "${TMPDIR:-/tmp}/redline-deploy.XXXXXXXX")"
trap 'rm -rf -- "$config_dir"' EXIT
az containerapp show --name "$app_name" --resource-group "$resource_group" \
    --only-show-errors --output json > "$config_dir/current.json"
az containerapp secret list --name "$app_name" --resource-group "$resource_group" \
    --show-values --only-show-errors --output json > "$config_dir/secrets.json"

# JSON is valid YAML. Secret values go directly from the environment to a private
# temporary configuration file; they never enter command arguments or stdout.
python3 - "$config_dir/current.json" "$config_dir/secrets.json" "$config_dir/update.yaml" <<'PY'
import json
import os
from pathlib import Path
import sys

current = json.loads(Path(sys.argv[1]).read_text())
props = current['properties']
configuration = props['configuration']
owned = {'app-password': 'APP_PASSWORD', 'session-secret': 'SESSION_SECRET',
         'anthropic-api-key': 'ANTHROPIC_API_KEY'}
# Full YAML secret updates replace the secret collection; name-only placeholders
# from containerapp show are not sufficient when changing any value.
# https://learn.microsoft.com/azure/container-apps/storage-mounts-azure-files
# Recover existing values only into this private file, preserving registry auth.
listed = json.loads(Path(sys.argv[2]).read_text())
if not isinstance(listed, list):
    raise SystemExit('Unable to recover existing secrets safely')
existing = {s['name']: dict(s) for s in (configuration.get('secrets') or [])}
for secret in listed:
    existing.setdefault(secret['name'], {}).update(secret)
secrets = []
for name, secret in existing.items():
    if name in owned:
        continue
    if secret.get('keyVaultUrl'):
        secret.pop('value', None)
    elif not isinstance(secret.get('value'), str):
        raise SystemExit('Refusing to overwrite existing secrets without their values')
    secrets.append(secret)
secrets.extend({'name': name, 'value': os.environ[var]} for name, var in owned.items())
configuration['secrets'] = secrets
configuration['ingress']['external'] = False
configuration['ingress']['allowInsecure'] = False
template = props['template']
if len(template['containers']) != 1:
    raise SystemExit('Refusing to modify an unexpected multi-container app')
container = template['containers'][0]
managed_env = set(owned.values()) | {'DATA_DIR', 'COOKIE_SECURE', 'AZURE_STORAGE_ACCOUNT_URL', 'REDLINE_BLOB_CONTAINER'}
env = [e for e in (container.get('env') or []) if e['name'] not in managed_env]
env.extend({'name': var, 'secretRef': name} for name, var in owned.items())
env.append({'name': 'DATA_DIR', 'value': '/data'})
env.append({'name': 'COOKIE_SECURE', 'value': 'true'})
env.append({'name': 'AZURE_STORAGE_ACCOUNT_URL', 'value': os.environ['REDLINE_STORAGE_URL']})
env.append({'name': 'REDLINE_BLOB_CONTAINER', 'value': 'redline'})
container['env'] = env
template['scale'] = template.get('scale') or {}
template['scale'].update(minReplicas=1, maxReplicas=1)
payload = {'name': current['name'], 'location': current['location'],
           'properties': {'configuration': configuration, 'template': template}}
for key in ('environmentId', 'managedEnvironmentId', 'workloadProfileName'):
    if props.get(key):
        payload['properties'][key] = props[key]
if current.get('identity'):
    payload['identity'] = current['identity']
Path(sys.argv[3]).write_text(json.dumps(payload))
PY
az containerapp update --name "$app_name" --resource-group "$resource_group" \
    --yaml "$config_dir/update.yaml" --only-show-errors --output none
az containerapp ingress enable --name "$app_name" --resource-group "$resource_group" \
    --type external --target-port 8000 --transport auto --only-show-errors --output none
fqdn="$(az containerapp show --name "$app_name" --resource-group "$resource_group" \
    --query properties.configuration.ingress.fqdn --output tsv --only-show-errors)"
[[ -n "$fqdn" ]] || fail 'Azure did not return an app URL'
printf 'Redline URL: https://%s\n' "$fqdn"
printf '%s\n' 'Run the authenticated upload/review smoke test before sharing the URL.'
