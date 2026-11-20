"""Offline shell integration tests. The az executable is replaced with a recorder.

These verify command sequencing and the generated configuration, not Azure's
server-side merge semantics, image build success or hosted authentication.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy_azure.sh"
PASSWORD = "test-password spaces ' quotes $() ; not-real"
SESSION = "test-session-secret-not-real-" + "s" * 32
API_KEY = "test-anthropic-key-not-real\nsecond-line"
REGISTRY_SECRET = "test-registry-secret-not-real"
FQDN = "redline.test.invalid"
MOCK_AZ = r'''#!/usr/bin/env python3
import json, os, pathlib, stat, sys
args = sys.argv[1:]
with open(os.environ['MOCK_LOG'], 'a') as handle:
    handle.write(json.dumps(args) + '\n')
operation = ' '.join(args[:2])
if args[:1] == ['account']:
    operation = 'account'
elif args[:3] == ['containerapp', 'secret', 'list']:
    operation = 'secrets'
elif args[:3] == ['containerapp', 'ingress', 'enable']:
    operation = 'ingress'
elif args[:2] == ['containerapp', 'show'] and '--query' in args:
    operation = 'fqdn'
if os.environ.get('FAIL_AT') == operation:
    raise SystemExit(23)
if operation == 'containerapp up':
    # Mirrors the installed extension's _validate_up_args contract. Generic
    # CLI help lists these flags, but this particular command rejects them.
    if any(flag in args for flag in ['--only-show-errors', '--output', '-o']):
        raise SystemExit('containerapp up rejects explicit global output flags')
    if os.environ.get('AZURE_CORE_OUTPUT') != 'none':
        raise SystemExit('Source deployment must suppress formatted output safely')
if operation == 'secrets':
    print(pathlib.Path(os.environ['MOCK_SECRETS']).read_text())
elif operation == 'containerapp show':
    print(pathlib.Path(os.environ['MOCK_CURRENT']).read_text())
elif operation == 'containerapp update':
    target = pathlib.Path(args[args.index('--yaml') + 1])
    payload = {'configuration': json.loads(target.read_text()),
               'file_mode': stat.S_IMODE(target.stat().st_mode),
               'directory_mode': stat.S_IMODE(target.parent.stat().st_mode),
               'path': str(target)}
    pathlib.Path(os.environ['MOCK_UPDATE']).write_text(json.dumps(payload))
elif operation == 'fqdn':
    print('' if os.environ.get('EMPTY_FQDN') else 'redline.test.invalid')
elif operation == 'account' and '--query' in args:
    print('test-subscription')
elif args[:3] == ['storage', 'account', 'show']:
    print('/subscriptions/test/storageAccounts/redlinetest')
elif args[:3] == ['containerapp', 'identity', 'assign']:
    print('test-principal')
'''


def current_app():
    return {
        "name": "redline", "location": "uksouth",
        "identity": {"type": "SystemAssigned", "principalId": "test-principal"},
        "properties": {
            "environmentId": "/subscriptions/test/environment",
            "workloadProfileName": "Consumption",
            "configuration": {
                "secrets": [{"name": "registry-password", "value": None},
                            {"name": "vault-secret", "keyVaultUrl": "https://vault.invalid/secrets/x", "identity": "system"},
                            {"name": "app-password"}],
                "registries": [{"server": "registry.invalid", "passwordSecretRef": "registry-password"}],
                "ingress": {"external": True, "allowInsecure": True, "targetPort": 8000},
            },
            "template": {
                "containers": [{"name": "redline", "image": "registry.invalid/redline:build",
                                "env": [{"name": "APP_PASSWORD", "value": "old-secret"},
                                        {"name": "DATA_DIR", "value": "/old"},
                                        {"name": "OTHER_CONFIG", "value": "keep-me"}]}],
                "scale": {"minReplicas": 0, "maxReplicas": 10},
            },
        },
    }


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path.resolve() / "fixture"
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts/deploy_azure.sh"
    shutil.copy2(SCRIPT, script)
    (root / "backend/app").mkdir(parents=True)
    (root / "backend/app/main.py").write_text("# C1 integration fixture\n")
    (root / "frontend").mkdir()
    (root / "frontend/package-lock.json").write_text("{}")
    (root / "frontend/package.json").write_text("{}")
    (root / "backend/requirements.txt").write_text("fastapi\n")
    shutil.copy2(SCRIPT.parents[1] / "Dockerfile", root / "Dockerfile")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "az").write_text(MOCK_AZ)
    (bin_dir / "az").chmod(0o700)
    app = root / "current.json"
    app.write_text(json.dumps(current_app()))
    secrets = root / "secrets.json"
    secrets.write_text(json.dumps([{ "name": "registry-password", "value": REGISTRY_SECRET}]))
    temp_dir = root / "temp"
    temp_dir.mkdir()
    env = os.environ.copy()
    # Do not inherit any actual cloud credentials or app settings from the host.
    for name in list(env):
        if name.startswith(("AZURE_", "ARM_", "REDLINE_", "ANTHROPIC_")) or name in {"APP_PASSWORD", "SESSION_SECRET"}:
            del env[name]
    env.update(PATH=str(bin_dir) + os.pathsep + env["PATH"], APP_PASSWORD=PASSWORD,
               SESSION_SECRET=SESSION, ANTHROPIC_API_KEY=API_KEY, TMPDIR=str(temp_dir),
               MOCK_LOG=str(root / "commands.jsonl"), MOCK_CURRENT=str(app), MOCK_SECRETS=str(secrets),
               MOCK_UPDATE=str(root / "update.json"))
    return root, script, env


def run(deployment, *, trace=False):
    _, script, env = deployment
    return subprocess.run(["bash", *( ["-x"] if trace else []), str(script)], env=env,
                          text=True, capture_output=True, timeout=10)


def commands(root):
    path = root / "commands.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("missing", ["APP_PASSWORD", "ANTHROPIC_API_KEY", "SESSION_SECRET"])
def test_missing_secret_fails_before_any_cloud_command(deployment, missing):
    root, _, env = deployment
    env.pop(missing)
    result = run(deployment)
    assert result.returncode != 0 and missing in result.stderr
    assert commands(root) == []


def test_short_session_secret_fails_before_cloud_command(deployment):
    root, _, env = deployment
    env["SESSION_SECRET"] = "too-short"
    assert run(deployment).returncode != 0
    assert commands(root) == []


def test_prebuilt_image_uses_same_guarded_configuration_flow(deployment):
    root, _, env = deployment
    env.update(REDLINE_IMAGE='registry.invalid/redline:release',
               REDLINE_REGISTRY_SERVER='registry.invalid')
    result = run(deployment)
    assert result.returncode == 0, result.stderr
    up = next(c for c in commands(root) if c[:2] == ["containerapp", "up"])
    assert '--source' not in up
    assert up[up.index('--image') + 1] == 'registry.invalid/redline:release'
    assert up[up.index('--registry-server') + 1] == 'registry.invalid'
    assert up[up.index('--ingress') + 1] == 'internal'
    assert commands(root)[-2][:3] == ['containerapp', 'ingress', 'enable']


def test_registry_server_without_image_fails_before_cloud_command(deployment):
    root, _, env = deployment
    env['REDLINE_REGISTRY_SERVER'] = 'registry.invalid'
    result = run(deployment)
    assert result.returncode != 0 and 'requires REDLINE_IMAGE' in result.stderr
    assert commands(root) == []


def test_fresh_managed_identity_app_accepts_null_optional_collections(deployment):
    root, _, _ = deployment
    app = current_app()
    app['properties']['configuration']['secrets'] = None
    app['properties']['configuration']['registries'] = [{'server': 'registry.invalid', 'identity': 'system'}]
    app['properties']['template']['containers'][0]['env'] = None
    app['properties']['template']['scale'] = None
    (root / 'current.json').write_text(json.dumps(app))
    (root / 'secrets.json').write_text('[]')
    result = run(deployment)
    assert result.returncode == 0, result.stderr
    update = json.loads((root / 'update.json').read_text())['configuration']['properties']
    assert {s['name'] for s in update['configuration']['secrets']} == {'app-password', 'session-secret', 'anthropic-api-key'}
    assert update['configuration']['registries'] == [{'server': 'registry.invalid', 'identity': 'system'}]
    assert update['template']['scale'] == {'minReplicas': 1, 'maxReplicas': 1}
    env = {v['name']: v for v in update['template']['containers'][0]['env']}
    assert env['ANTHROPIC_API_KEY']['secretRef'] == 'anthropic-api-key'


@pytest.mark.parametrize("missing", ["backend/app/main.py", "frontend/package-lock.json", "frontend/package.json", "backend/requirements.txt", "Dockerfile"])
def test_missing_application_integration_fails_before_cloud_commands(deployment, missing):
    root, _, _ = deployment
    (root / missing).unlink()
    assert run(deployment).returncode != 0
    assert commands(root) == []


def test_success_secret_references_https_scale_registry_and_cleanup(deployment):
    root, _, _ = deployment
    result = run(deployment, trace=True)
    assert result.returncode == 0, result.stderr
    # Storage provisioning has its own test; this one checks the app configuration sequence.
    storage_steps = lambda c: c[0] in ("storage", "role") or c[:2] == ["containerapp", "identity"] or (c[:2] == ["account", "show"] and "--query" in c)
    args = [c for c in commands(root) if not storage_steps(c)]
    assert args[0][:2] == ["account", "show"]
    assert args[1][:2] == ["containerapp", "show"]  # read-only durable-mode guard
    args.pop(1)
    up = args[1]
    assert up[:2] == ["containerapp", "up"]
    assert up[up.index("--ingress") + 1] == "internal"
    assert up[up.index("--target-port") + 1] == "8000"
    assert up[up.index("--source") + 1] == str(root)
    assert args[2][:2] == ["containerapp", "show"]
    assert args[3][:3] == ["containerapp", "secret", "list"] and "--show-values" in args[3]
    assert args[4][:2] == ["containerapp", "update"]
    assert args[5][:3] == ["containerapp", "ingress", "enable"]
    assert args[5][args[5].index("--type") + 1] == "external"
    update = json.loads((root / "update.json").read_text())
    cfg = update["configuration"]
    props = cfg["properties"]
    ingress = props["configuration"]["ingress"]
    assert ingress["external"] is False and ingress["allowInsecure"] is False
    assert props["template"]["scale"] == {"minReplicas": 1, "maxReplicas": 1}
    secrets = {s["name"]: s for s in props["configuration"]["secrets"]}
    assert secrets["app-password"]["value"] == PASSWORD
    assert secrets["session-secret"]["value"] == SESSION
    assert secrets["anthropic-api-key"]["value"] == API_KEY
    assert secrets["registry-password"] == {"name": "registry-password", "value": REGISTRY_SECRET}
    assert secrets["vault-secret"]["keyVaultUrl"] == "https://vault.invalid/secrets/x"
    assert props["configuration"]["registries"] == current_app()["properties"]["configuration"]["registries"]
    env = {e["name"]: e for e in props["template"]["containers"][0]["env"]}
    assert env["APP_PASSWORD"] == {"name": "APP_PASSWORD", "secretRef": "app-password"}
    assert env["SESSION_SECRET"]["secretRef"] == "session-secret"
    assert env["ANTHROPIC_API_KEY"]["secretRef"] == "anthropic-api-key"
    assert env["DATA_DIR"]["value"] == "/data"
    assert env["COOKIE_SECURE"]["value"] == "true"
    assert env["OTHER_CONFIG"]["value"] == "keep-me"
    assert props["environmentId"] == "/subscriptions/test/environment"
    assert cfg["identity"] == current_app()["identity"]
    assert update["file_mode"] == 0o600 and update["directory_mode"] == 0o700
    assert not Path(update["path"]).exists()
    assert list((root / "temp").iterdir()) == []
    observable = result.stdout + result.stderr + json.dumps(args)
    assert all(secret not in observable for secret in [PASSWORD, SESSION, API_KEY, REGISTRY_SECRET])
    assert f"https://{FQDN}" in result.stdout


@pytest.mark.parametrize("failure, last_operation", [
    ("account", ["account", "show"]),
    ("containerapp up", ["containerapp", "up"]),
    ("containerapp show", ["containerapp", "show"]),
    ("secrets", ["containerapp", "secret", "list"]),
    ("containerapp update", ["containerapp", "update"]),
    ("ingress", ["containerapp", "ingress", "enable"]),
])
def test_failure_stops_at_first_unsuccessful_command(deployment, failure, last_operation):
    root, _, env = deployment
    env["FAIL_AT"] = failure
    result = run(deployment)
    assert result.returncode != 0
    assert commands(root)[-1][:len(last_operation)] == last_operation
    assert "Redline URL" not in result.stdout
    assert list((root / "temp").iterdir()) == []


def test_refuses_unexpected_multi_container_before_enabling_public_ingress(deployment):
    root, _, _ = deployment
    app = current_app()
    app["properties"]["template"]["containers"].append({"name": "sidecar"})
    (root / "current.json").write_text(json.dumps(app))
    result = run(deployment)
    assert result.returncode != 0 and "multi-container" in result.stderr
    assert not any(c[:3] == ["containerapp", "ingress", "enable"] for c in commands(root))
    assert not any(c[:2] == ["containerapp", "update"] for c in commands(root))
    assert list((root / "temp").iterdir()) == []


def test_missing_fqdn_does_not_report_success(deployment):
    _, _, env = deployment
    env["EMPTY_FQDN"] = "1"
    result = run(deployment)
    assert result.returncode != 0 and "did not return" in result.stderr
    assert "Redline URL" not in result.stdout


def test_container_is_non_root_and_has_real_integration_inputs():
    root = SCRIPT.parents[1]
    dockerfile = (root / "Dockerfile").read_text()
    assert "USER redline" in dockerfile
    assert "RUN chown -R redline:redline /home/redline" in dockerfile
    assert "libreoffice-impress" in dockerfile and "fonts-crosextra-carlito" in dockerfile
    assert "COPY frontend/package.json frontend/package-lock.json" in dockerfile
    assert "RUN npm ci" in dockerfile and "RUN npm run build" in dockerfile
    assert '"app.main:app"' in dockerfile and '"8000"' in dockerfile
    ignored = (root / ".dockerignore").read_text().splitlines()
    assert all(pattern in ignored for pattern in [".git", ".env", ".env.*", "**/.env", "**/.env.*", "samples", "backend/tests"])


def test_missing_existing_secret_values_refuses_update(deployment):
    root, _, _ = deployment
    (root / "secrets.json").write_text('[{"name": "registry-password"}]')
    result = run(deployment)
    assert result.returncode != 0 and "without their values" in result.stderr
    assert not any(c[:2] == ["containerapp", "update"] for c in commands(root))
    assert list((root / "temp").iterdir()) == []


def test_secret_list_preserves_newly_discovered_secrets(deployment):
    root, _, _ = deployment
    (root / "secrets.json").write_text(json.dumps([
        {"name": "registry-password", "value": REGISTRY_SECRET},
        {"name": "concurrent-secret", "value": "preserve-new-value"},
    ]))
    assert run(deployment).returncode == 0
    update = json.loads((root / "update.json").read_text())["configuration"]
    secrets = update["properties"]["configuration"]["secrets"]
    assert {"name": "concurrent-secret", "value": "preserve-new-value"} in secrets


def test_reviews_are_stored_durably_through_the_app_identity(deployment):
    """Blob storage is provisioned and reached with the managed identity; no storage keys are fetched."""
    root, *_ = deployment
    result = run(deployment)
    assert result.returncode == 0, result.stderr
    calls = commands(root)
    assert any(c[:3] == ["storage", "account", "create"] and "--allow-blob-public-access" in c for c in calls)
    assert any(c[:3] == ["containerapp", "identity", "assign"] for c in calls)
    assert any(c[:3] == ["role", "assignment", "create"] and "Storage Blob Data Contributor" in c for c in calls)
    assert not any("keys" in c or "connection-string" in c for c in calls)
    update = json.loads((root / "update.json").read_text())["configuration"]
    env = {e["name"]: e for e in update["properties"]["template"]["containers"][0]["env"]}
    url = env["AZURE_STORAGE_ACCOUNT_URL"]["value"]
    assert url.startswith("https://redline") and url.endswith(".blob.core.windows.net")


def test_legacy_deployment_cannot_replace_an_activated_durable_app(deployment):
    root, _, _ = deployment
    app = current_app()
    app["properties"]["template"]["containers"][0]["env"].append({"name": "DATABASE_URL", "secretRef": "database-url"})
    (root / "current.json").write_text(json.dumps(app))
    result = run(deployment)
    assert result.returncode != 0 and "Refusing legacy deployment" in result.stderr
    assert not any(c[:2] == ["containerapp", "up"] for c in commands(root))
    assert not any(c[:3] == ["role", "assignment", "create"] for c in commands(root))
