# Deployment

Redline runs as one container: FastAPI serves the API and the built React app, and an in-process worker processes the queue. In production it uses PostgreSQL for metadata and the job queue, and Azure Blob Storage for files.

## Configuration

| Variable | Purpose |
|---|---|
| `APP_PASSWORD` | The shared sign-in password (secret) |
| `SESSION_SECRET` | Signs session cookies; at least 32 random characters (secret) |
| `ANTHROPIC_API_KEY` | Enables the AI writing review; optional (secret) |
| `COOKIE_SECURE=1` | Marks the session cookie Secure behind an HTTPS proxy |
| `REDLINE_DURABLE_JOBS=1` | Use the durable queue and shared state |
| `DATABASE_URL` | PostgreSQL connection string, TLS `verify-full` (secret) |
| `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_STORAGE_CONTAINER` | Private Blob container for files, reached through the app's managed identity |
| `REDLINE_WORKSPACE` | Optional workspace name. Uses a separate set of tables in the same database, so a deployment can start a clean review store while earlier reviews stay untouched (pair it with its own Blob container) |
| `MAX_CONCURRENT_JOBS`, `MAX_DB_CONNECTIONS`, `MAX_AI_SLIDES` | Capacity settings; defaults suit one small container |

Secrets are read from the environment only, are never baked into the image, and are never written to the repository.

## Azure

1. **Shared state.** `infra/durable-state.bicep` creates the PostgreSQL flexible server (TLS only, firewall limited to the app's outbound addresses, 7-day backups). Create a private Blob container and grant the Container App's system-assigned identity *Storage Blob Data Contributor* on that container only.
2. **Image.** Build for `linux/amd64` in Azure Container Registry:
   ```bash
   az acr build --registry <registry> --image redline:<git-sha> --platform linux/amd64 .
   ```
3. **App.** For a first deployment, `scripts/deploy_azure.sh` creates the Container Apps environment and app from environment variables. To switch an existing app onto shared state, `scripts/deploy_durable_azure.py` updates it from an immutable image digest. It sets the durable variables and the database secret reference, preserves the existing secrets and identity, and leaves ingress internal until checks pass.
   ```bash
   DATABASE_URL=... python scripts/deploy_durable_azure.py --image <registry>/redline@sha256:<digest> \
     --migration-receipt receipt.json --storage-url https://<account>.blob.core.windows.net
   ```
4. **Updates.** Each release is staged before it takes traffic:
   ```bash
   git archive <sha> | tar -x -C /tmp/release          # build from the committed tree only
   az acr build -r <registry> -t redline:v3-<sha> /tmp/release
   az containerapp update -n <app> -g <resource-group> --image <registry>/redline@<digest> --revision-suffix v3-<sha>
   az containerapp revision label add -n <app> -g <resource-group> --label stage --revision <app>--v3-<sha>
   # smoke test https://<app>---stage.<env-domain>: /api/health 200, /api/decks 401, bundle matches the build
   az containerapp ingress traffic set -n <app> -g <resource-group> --revision-weight <app>--v3-<sha>=100 <previous>=0
   az containerapp revision label remove -n <app> -g <resource-group> --label stage
   ```
   The previous revision stays active at 0% as the rollback: `az containerapp ingress traffic set ... --revision-weight <previous>=100 <app>--v3-<sha>=0`.

Reviews created by the earlier single-container version can be imported once with `python -m app.migration --source <folder> --engine-source <label>`. It copies and checksums every file before writing metadata, never re-analyses a deck, and refuses to run against a store that is already live.

## Removing reviews

To clear test or demo reviews from a workspace, keep the ones you want by ID:

```bash
az containerapp exec -n <app> -g <resource-group> --command "python -m app.maintenance keep <review-id> <review-id>"
```

`python -m app.maintenance drop-guideline <guideline-id>` removes one saved custom guideline the same way. Every removed row is first saved to Blob Storage under `backups/`, and review files stay in storage, so a removal can be undone. Nothing is removed if a named item doesn't exist.

## Checks after a release

- `GET /api/health` returns `{"ok": true}`.
- Anonymous API, image and download requests return 401; a wrong password is rejected.
- Upload a sample deck; it renders all slides, completes analysis and shows the writing-review status.
- Accept a fixable flag, apply fixes, compare Before/After and download the corrected deck.
- History lists earlier reviews and they open with their decisions intact.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every pull request: the back-end suite with PostgreSQL, the Azurite emulator and LibreOffice enabled, then the front-end tests and production build.

## Testing against real services

The default test run uses SQLite and local files. Integration tests are opt-in:

```bash
REDLINE_TEST_POSTGRES=1 \
REDLINE_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/redline \
PGPASSFILE=<path to a pgpass file for that local database> \
REDLINE_TEST_AZURITE_PORT=10000 \
REDLINE_RENDER_INTEGRATION=1 \
PYTHONPATH=backend .venv/bin/pytest backend/tests scripts/tests -q
```

Use a dedicated local PostgreSQL and an Azurite emulator started with `--skipApiVersionCheck`. Never point tests at a production database or storage account. With all services enabled, the opt-in tests run too instead of being skipped.
