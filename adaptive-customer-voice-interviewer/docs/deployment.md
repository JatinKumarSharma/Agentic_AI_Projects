# CVI deployment design

**Primary target:** hosted Docker Compose with PostgreSQL + Nginx/HTTPS  
**Secondary target:** public server/VM with host Nginx + HTTPS  
**Status:** Production is online-first and runs entirely on the host. PostgreSQL is the production persistence adapter; SQLite is retained only for offline tests. The LangGraph engine, governed evidence retrieval, durable checkpoints, tenant/RBAC/audit, structured logs, health checks, versioned migrations, backups, self-hosted LLM/speech services, server-side VAD, a durable repository-backed background worker, and shared session state are implemented. Multiple API and worker replicas may run against one database.

---

## Portfolio demo launch profile: Render + Neon

Use this profile when there is no custom domain and no paid hosting:

- Render free web service with a public `onrender.com` HTTPS URL.
- External Neon free PostgreSQL for authoritative state.
- Groq for LLM/STT, browser TTS, and lightweight server-side energy VAD.
- Migrations, worker, speech bridge, and API in one container.

See [`render.md`](render.md) for the complete setup, secrets, demo pool, and
smoke test. The VPS section below remains the strict full-production profile.

---

## Production-grade VPS profile: VPS + domain + rate-safe Groq

The long design notes below remain as implementation history.

Prerequisites:

- Docker and Docker Compose on the VPS.
- DNS for the chosen domain pointing at the VPS.
- TLS certificate/key in `deploy/certs/` or the proxy's secret store.
- A rate-safe Groq key in the host secret store.
- Long random PostgreSQL, researcher, and named-account credentials.

Set the launch environment outside Git:

```dotenv
CVI_PERSISTENCE_BACKEND=postgresql
CVI_MIGRATION_DATABASE_URL=postgresql://...
CVI_RUNTIME_DATABASE_URL=postgresql://...
CVI_BACKUP_DATABASE_URL=postgresql://...
POSTGRES_PASSWORD=<generated owner password>
CVI_RUNTIME_DB_PASSWORD=<generated runtime password>
CVI_BACKUP_DB_PASSWORD=<generated backup password>
CVI_LLM_PROVIDER=groq
CVI_LLM_STRUCTURED_METHOD=function_calling
GROQ_API_KEY=<rate-safe key>
GROQ_MODEL=qwen/qwen3.8-27b
CVI_EVALUATOR_MODEL=
RESEARCHER_ACCESS_CODE=<generated researcher code>
CVI_TENANT_ID=<production tenant>
CVI_DEMO_LINK_TTL_MINUTES=15
CVI_RATE_LIMIT_DEMO_PER_MINUTE=6
```

Deploy:

```powershell
docker compose -f compose.yaml -f compose.prod.yaml config --quiet
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

Public demo links are tokenless:

```text
https://<your-domain>/?demo=<slug>
```

Create a small pool of sessions with authenticated `POST /sessions`, each with
the same `demo_slug`. The public broker `POST /demo/invites/{slug}` rotates one
unclaimed session capability, sets a short deadline, records an event/audit
entry, and returns the raw token only once in the response body. The frontend
stores that capability in session storage and removes any raw token from the
address bar. Never publish the `participant_url` returned by session creation.

The participant welcome panel now requires explicit consent before
**Begin interview**. The study's retention period and deletion contact must be
published outside the repository before public traffic.

See [`publication_checklist.md`](publication_checklist.md) for the complete
go-live smoke test.

---

## Phase 1 + migrations/backups + observability + tenant/audit + offline delivered

Files added or changed:

- `requirements.txt` — added `langchain-ollama`.
- `requirements-dev.txt` — pytest/httpx for the test image.
- `agent/llm.py` — added `CVI_OLLAMA_BASE_URL` support.
- `tests/test_llm_provider_config.py` — regression tests for configured and default Ollama URLs.
- `.env.example` — documented `CVI_OLLAMA_BASE_URL` and the container DB path override.
- `.dockerignore`, `Dockerfile` — non-root `python:3.13-slim` image with `runtime` and `test` targets.
- `compose.yaml`, `compose.dev.yaml`, `compose.prod.yaml` — base, dev, and production overlays.
- `deploy/nginx.conf` — Compose-facing TLS/static/proxy config.
- `scripts/migrate.py`, `scripts/backup.py`, `scripts/__init__.py` — schema versioning and online SQLite backups.
- `tests/test_migrations_backups.py` — migration idempotency and backup/retention tests.
- `agent/observability.py` — JSON/text logging, request-ID context, and log configuration from env.
- `api.py` — request-context middleware (`X-Request-ID`), structured exception logs, and `/health/live` + `/health/ready` endpoints.
- `tests/test_observability.py` — formatter, request-ID propagation, and readiness checks.
- `agent/tenancy.py`, `agent/auth.py` — tenant validation and researcher auth dependency.
- `agent/read_model.py` — tenant columns and filters on every durable table, `audit_log`, and audit helpers.
- `scripts/migrate.py` — `002_tenant_audit_fields` migration.
- `api.py` — auth dependencies on researcher/debug/create endpoints, tenant-scoped queries, audit events for session/start/report/export, and `/researcher/audit`.
- `tests/test_tenancy_audit.py` — isolation, audit, migration, and authorization tests.
- `compose.local.yaml` — offline overlay for Ollama, local speech, and one-shot model pull.
- `scripts/pull_local_models.py` — one-command Ollama/Piper/faster-whisper bootstrap.
- `deploy/speech/` — local STT/TTS service implementing the existing HTTP contracts.
- `docs/offline.md` — offline startup and verification checklist.
- `tests/test_local_model_pull.py`, `tests/test_local_speech_service.py` — mocked offline-boundary tests.
- `deploy/speech/vad.py`, `deploy/speech/segmenter.py` — provider-neutral VAD boundary and deterministic turn segmentation.
- `deploy/speech/stt.py`, `deploy/speech/stream.py` — local STT boundary and streaming VAD/WebSocket endpoint.
- `api.py` — `/sessions/{session_id}/voice/local` bridge with immediate acknowledgement and asynchronous submission.
- `frontend/app.js` — single-microphone streaming path for local STT; browser energy VAD retained for batch fallback.
- `tests/test_speech_vad.py`, `tests/test_speech_stream.py`, `tests/test_api_local_voice_stream.py` — mocked VAD/STT/websocket tests.

Verification:

- `docker compose -f compose.yaml -f compose.dev.yaml config --quiet` — OK
- `docker compose -f compose.yaml -f compose.prod.yaml config --quiet` — OK (with the owner/runtime/backup URLs and role passwords set)
- `docker compose --profile offline -f compose.yaml -f compose.local.yaml config --quiet` — OK
- `pytest -q` — offline suite passes; current milestone result is recorded in `docs/security_remediation_plan.md`.
- Image build was not run here because no Docker daemon is attached to this environment.

`compose.yaml` now includes the one-shot `migrate` service, the hosted
`ollama`/`speech`/`pull-models` services, and the profile-gated `backup`
service.

### Hosted production startup

Set a long random `POSTGRES_PASSWORD`, then set the three role-aware database
URLs and the two non-owner role passwords in `.env`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```dotenv
POSTGRES_DB=cvi_research
POSTGRES_USER=cvi
POSTGRES_PASSWORD=<generated owner password>
CVI_MIGRATION_DATABASE_URL=postgresql://cvi:<generated owner password>@db:5432/cvi_research
CVI_RUNTIME_DATABASE_URL=postgresql://cvi_runtime:<generated runtime password>@db:5432/cvi_research
CVI_BACKUP_DATABASE_URL=postgresql://cvi_backup:<generated backup password>@db:5432/cvi_research
CVI_RUNTIME_DB_USER=cvi_runtime
CVI_RUNTIME_DB_PASSWORD=<generated runtime password>
CVI_BACKUP_DB_USER=cvi_backup
CVI_BACKUP_DB_PASSWORD=<generated backup password>
RESEARCHER_ACCESS_CODE=<generated researcher code>
```

Then run:

```powershell
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

`db-bootstrap` creates the runtime and backup roles with least-privilege grants.
`migrate` uses the DDL-capable owner URL, creates the full schema, records schema
versions, and verifies security constraints before reporting success. `api` and
`worker` use the DML-only runtime URL; `backup` uses the read-only URL. `api`
waits for migration, database health, model availability, and speech readiness.
Nginx publishes only ports `80`/`443`; the API port is internal. Client
replacements are limited to:

- **Storage:** change `CVI_PERSISTENCE_BACKEND` / `CVI_DATABASE_URL`.
- **LLM:** change `CVI_LLM_PROVIDER`, `CVI_OLLAMA_BASE_URL`, and model names.
- **Speech:** change `CVI_VOICE_STT_PROVIDER`, `CVI_VOICE_TTS_PROVIDER`, and
  their endpoint URLs.

No other deployment flexibility is intended.

Logging and health notes:

- `CVI_LOG_FORMAT=json` (default) emits one JSON object per log line with `timestamp`, `level`, `logger`, `message`, and `request_id`; set `CVI_LOG_FORMAT=text` for local readability.
- Every HTTP response carries `X-Request-ID`; pass your own `X-Request-ID` header to correlate across services.
- `/health/live` is a process-only check; `/health/ready` verifies SQLite connectivity and model-provider configuration and returns 503 when not ready (for example, missing `GROQ_API_KEY`). Uvicorn's default access logs still appear in text form; the application logs are structured.

Tenant and audit notes:

- `CVI_TENANT_ID` (default `default`) is stamped on every durable row and passed to every researcher query. Named researcher accounts may carry their own `tenant_id`; session creation writes that context tenant into the session, cache key, snapshot, event, and audit records.
- Researcher endpoints, `/debug/*`, `POST /sessions`, and the researcher static assets require authentication. Hosted session creation fails closed when neither `RESEARCHER_ACCESS_CODE` nor `CVI_RESEARCHER_ACCOUNTS` is configured. `CVI_ALLOW_UNAUTHENTICATED_LOCAL=1` is the only opt-out and is intended for local, non-hosted processes. The old path-prefix middleware was removed.
- Audit events are recorded for session creation, interview start, report generation, and CSV export, and are readable via `GET /researcher/audit`.

Database roles and migration verification:

- `db-bootstrap` runs before migrations. It creates/updates `cvi_runtime` (SELECT/INSERT/UPDATE/DELETE plus sequence usage) and `cvi_backup` (SELECT plus sequence usage), grants schema usage, revokes schema CREATE, and installs default privileges for objects created by the migration owner.
- `migrate` fails closed if required tables, tenant columns, primary keys, or security indexes are missing or altered. In particular it verifies the partial unique index `idx_background_jobs_active_unique`, tenant-prefixed LangGraph checkpoint keys, audit indexes, and the voice outbox drain index. Verification never repairs drift by itself.
- Runtime processes cannot apply schema because their DSN is not DDL-capable; backups cannot mutate application data because their DSN is read-only.

Abuse-limit notes:

- Participant endpoints use per-session and per-IP fixed-window budgets (`CVI_RATE_LIMIT_SESSION_PER_MINUTE`, `CVI_RATE_LIMIT_IP_PER_MINUTE`); excess HTTP requests return 429 and excess voice websockets close with 4429. These counters are per API process, so use the reverse proxy for cluster-wide quotas.
- Transcript, metadata, audio, TTS, and body-size caps live in `agent/limits.py`. Oversized declared bodies return 413 before parsing; invalid fields return 422. Voice streams are capped by frame size, total bytes, duration, and two concurrent streams per session.
- Offline tests disable the in-process rate limiter through `tests/conftest.py`; hosted mode keeps it enabled by default.

Test-mode notes:

- `compose.local.yaml` plus the `offline` profile selects SQLite and test model paths. It is not a production target.
- The hosted speech service uses the installed Amy voice model for local TTS.
- All model downloads happen once in `ollama-models` and `speech-models` volumes.
- `local` STT uses server-side Silero VAD over `WS /sessions/{id}/voice/local`: one browser microphone stream, immediate acknowledgement at speech end, and asynchronous transcription/answer decomposition.
- See `docs/offline.md` for the complete test checklist.

---

## 1. What actually exists today

| Component | Reality in the repository | Deployment consequence |
|---|---|---|
| HTTP API | `api.py` — FastAPI app with `/health`, `/sessions*`, `/sessions/{id}/voice*`, `/researcher*`, `/debug/*` | Stateless replicas are supported; start with `uvicorn api:app`. No ASGI factory or `uvicorn.run()` block. |
| Frontend | Static files in `frontend/`, mounted at `/` by `api.py:2065-2072`. No npm, bundler, or build step. | Can be served by the API in dev; can be split to Nginx in prod without rebuilding assets. |
| Research engine | `agent/graph.py` LangGraph graph; model calls go through `agent/llm.py` (`groq` or `ollama`). | AI work runs in the API process for offline tests and in the durable worker for hosted mode. Provider choice is an env var. |
| Background jobs | Durable `background_jobs`, `voice_output_events`, `session_pending_actions`, and `session_leases` tables in both adapters; `agent/jobs/service.py` worker loop and `python -m scripts.worker` entry point. API enqueues only when `CVI_WORKER_ENABLED=1`; `ThreadPoolExecutor`s remain for offline tests. | Hosted production runs the `worker` service. Jobs, outbox events, pending interrupts, and per-session processing leases survive process restarts; multiple API and worker replicas are safe. No Redis/Celery/broker is required. |
| Checkpointer | `RepositoryCheckpointer` in `agent/persistence/checkpointer.py`, built into the graph at `api.py`. | Active interview checkpoints survive process restart in the configured repository. Pending interrupts are durable in worker mode. |
| Persistence | Repository boundary in `agent/persistence/` with env-selected backend. PostgreSQL is implemented in `agent/persistence/postgres.py` and selected by `CVI_PERSISTENCE_BACKEND=postgresql`. SQLite is the offline-test adapter. Both backends hold authoritative memory, reports, audit entries, and LangGraph checkpoints. | Production uses `pg-data`; tests use a SQLite file. |
| Auth | HTTP Basic centralized in `agent/auth.py`. Legacy `researcher` / `RESEARCHER_ACCESS_CODE` remains an admin account; `CVI_RESEARCHER_ACCOUNTS` adds named researchers with roles and optional tenant assignment. Constant-time password compare. Participant sessions use a high-entropy capability delivered only in the invite link; only its SHA-256 digest is stored. | Researcher, debug, create, and static-asset routes use one dependency; tenant, role, owner, and audit actor are attached to the request context. |
| Config | `.env` + `.env.example`, `load_dotenv()` in `api.py` and several `agent/*` modules. LLM sampling is purpose-scoped: default `0`, proposals `0.3`, engagement `0.4`. | Env-first already works; no Windows path is hardcoded in application code. |
| Logs | `agent/observability.py` configures JSON/text logging with request IDs; middleware applies `X-Request-ID`. | Production uses JSON stdout logs with request correlation. |
| Health | `GET /health` returns a static JSON object; `/health/live` and `/health/ready` are implemented. Readiness delegates database checks to the configured repository and reports `persistence_backend`. | Compose healthcheck uses `/health/ready`; Nginx waits for API readiness. |
| Tests | The offline suite mocks model boundaries; 4 live-provider tests are skipped unless `RUN_LIVE_LLM_TESTS=1`. `pytest` is not in `requirements.txt`. | A dev/test overlay keeps live tests out of default Compose runs. |

### Containerization gaps

1. **Fixed in Phase 1:** `langchain-ollama` is now in `requirements.txt`; a clean image can use the Ollama provider.
2. **Fixed in Phase 1:** `agent/llm.py` now reads `CVI_OLLAMA_BASE_URL` (default `http://localhost:11434`), so Ollama can run as a separate container.
3. **Fixed in Phase 1:** `.dockerignore` excludes `.venv/`, `.env`, `data/`, caches, the empty `main.py/` directory, and the 0-byte `python` file.
4. API replicas share sessions through the repository; pending interrupts, jobs, voice outbox events, and per-session worker leases are durable in worker mode. Multiple API and worker replicas are supported.

Worker ownership is fenced end to end. Each process derives a unique owner
identity from `CVI_WORKER_ID` (a label, not a shared identity). Claims carry a
fencing token, handlers refresh a heartbeat while running, and completion,
retry, or failure only applies while the worker still owns the `processing`
row. Long handlers therefore cannot be finished by a stale replica after their
claim is recovered. Job payload identity fields are checked against the claimed
row before dispatch. `CVI_WORKER_HEARTBEAT_SECONDS` and
`CVI_WORKER_STALE_TIMEOUT_SECONDS` tune the liveness window; `CVI_SESSION_LEASE_TTL_SECONDS` continues to govern per-session interview leases.

---

## 2. How the design maps to the stated decisions

| Decision | Design response |
|---|---|
| No hardcoded local paths | All paths are container POSIX paths (`/app`, `/data`, `/backups`). Host paths appear only in Compose volume mounts and are relative (`./frontend`, `./deploy/certs`). |
| Separate dev/prod config | `compose.yaml` (base) + `compose.dev.yaml` (source mount, reload, published port, test service) + `compose.prod.yaml` (Nginx, no published API port, restart policies). |
| Replaceable LLM and speech boundaries | Default hosted deployment uses self-hosted Ollama and the local speech service. Clients may replace only these boundaries with their own LLM/speech endpoints; no other flexibility is intended. |
| Persistent database outside containers | Named volume `cvi-data` mounted at `/data`; `CVI_READ_MODEL_DB=/data/research_read_model.sqlite3`. |
| Separate API, frontend, AI, background jobs | API, frontend, LLM, speech, and a separate durable `worker` service are physically separated in Compose. Jobs, voice outbox, and pending interrupts live in the configured repository; in-process executors are test-only. Frontend is a separate Nginx-served static surface in `compose.prod.yaml`. |
| Health checks, logs, migrations, backups | Compose healthchecks, JSON stdout logging with request IDs, one-shot `migrate` service, one-shot `backup` service using stdlib `sqlite3`. |
| Auth/authz/tenancy/audit in the application | Extend existing Basic auth into role-aware auth, add `tenant_id` to session-scoped tables, enforce tenant filters in API queries, and record actor/action/request_id in `session_events`. |
| Production has no local PC dependency | `compose.prod.yaml` runs PostgreSQL, Ollama, speech, API, and Nginx on the host. Only storage, speech, and LLM boundaries may be replaced by a client; no other deployment flexibility is intended. |

---

## 3. Service topology

### Base (`compose.yaml`) — used by every environment

| Service | Image/build | Role | State |
|---|---|---|---|
| `migrate` | local build | One-shot schema migration/version check. Runs before `api`. | `cvi-data` |
| `api` | local build | FastAPI + LangGraph; enqueues durable jobs when `CVI_WORKER_ENABLED=1`, otherwise uses in-process executors for tests. | `cvi-data` |
| `worker` | local build | Claims durable jobs, dispatches response processing and contextual engagement, writes voice outbox events. | `cvi-data` |
| `backup` | local build, profile `backup` | One-shot SQLite online backup with retention. | `cvi-data`, `cvi-backups` |
| `ollama` | `ollama/ollama` | Hosted self-hosted LLM provider. Replaceable by client endpoint. | `ollama-models` |
| `speech` | local build | Hosted self-hosted VAD/STT/TTS service. Replaceable by client endpoint. | `speech-models` |
| `pull-models` | local build, one-shot | Downloads configured models before API start. | `ollama-models`, `speech-models` |

### Production overlay (`compose.prod.yaml`)

| Service | Role |
|---|---|
| `db` | `postgres:16-alpine` — production authoritative store on `pg-data`. |
| `web` | `nginx:alpine` — TLS termination, static frontend assets, reverse proxy to `api`. Also usable on a VM as host Nginx. |
| `api` | Same image, no host port, backed by PostgreSQL, with `CVI_WORKER_ENABLED=1`. Only `expose: 8000` for the proxy network. |
| `worker` | Same image, backed by PostgreSQL, with `CVI_WORKER_ENABLED=1`. Runs separately from the API. |

### Test-only overlay (`compose.local.yaml`, profile `offline`)

Overrides the base hosted services with test model paths and the SQLite backend.
It is not a production deployment target.

### Deliberately **not** added yet

- Redis, Celery, RabbitMQ — the durable queue uses PostgreSQL/SQLite directly; no external broker exists.
- A frontend Node build — there is nothing to build.
- Multiple API replicas — supported; sessions, pending interrupts, jobs, voice outbox events, and per-session leases are externalized. Multiple worker replicas are supported.
- Arbitrary client integrations — only storage, speech, and LLM boundaries are replaceable.

---

## 4. Proposed file set

```text
Dockerfile                 # python:3.13-slim, non-root, /app, /data, /backups
.dockerignore              # .env, .venv, data, __pycache__, main.py, python, tests (runtime)
requirements.txt           # add langchain-ollama; pin versions
requirements-dev.txt       # pytest, httpx (TestClient), ruff (optional)
compose.yaml               # api, worker, migrate, backup, ollama, speech
compose.local.yaml         # offline profile, local speech, model pull
compose.dev.yaml           # bind mount source, --reload, port 8000, test service
compose.prod.yaml          # nginx web service, restart policies, no API port
deploy/nginx.conf          # Compose upstream http://api:8000
deploy/nginx-vm.conf       # VM upstream http://127.0.0.1:8000
deploy/speech/             # local faster-whisper, Silero VAD, Piper HTTP + streaming service
scripts/migrate.py         # schema version table + idempotent migrations + invariant check
scripts/bootstrap_postgres_roles.py # DDL/DML/read-only role separation
scripts/backup.py          # sqlite3 Connection.backup() + pg_dump + retention
scripts/worker.py          # durable job handler registry + worker entry point
agent/persistence/invariants.py # declarative security constraints
scripts/pull_local_models.py # one-command model bootstrap
agent/jobs/service.py      # durable job claim/dispatch/retry loop
agent/persistence/postgres.py # production PostgreSQL repository adapter
docs/deployment.md         # this document
docs/offline.md            # offline startup and verification checklist
.env.example               # container-friendly CVI_READ_MODEL_DB and Ollama settings
```

### `requirements.txt` changes

- `langchain-ollama` is required by `agent/llm.py` when `CVI_LLM_PROVIDER=ollama`.
- `psycopg[binary]` is required by the production PostgreSQL repository.
- `httpx` is used by FastAPI `TestClient` in dev requirements.
- Pin all versions in a follow-up once the image is built, to make builds reproducible.

---

## 5. Compose sketch

### `compose.yaml` (base)

```yaml
name: cvi

x-cvi-env: &cvi-env
  CVI_READ_MODEL_DB: /data/research_read_model.sqlite3
  CVI_LOG_FORMAT: json
  CVI_LOG_LEVEL: info

services:
  migrate:
    build: .
    command: ["python", "-m", "scripts.migrate"]
    env_file: .env
    environment: *cvi-env
    volumes:
      - cvi-data:/data
    restart: "no"

  api:
    build: .
    command: ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
    env_file: .env
    environment:
      <<: *cvi-env
    volumes:
      - cvi-data:/data
    depends_on:
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 20s
    restart: unless-stopped

  worker:
    build: .
    command: ["python", "-m", "scripts.worker"]
    env_file: .env
    environment:
      <<: *cvi-env
      CVI_WORKER_ENABLED: "1"
    volumes:
      - cvi-data:/data
    depends_on:
      migrate:
        condition: service_completed_successfully
    restart: unless-stopped

  backup:
    build: .
    profiles: ["backup"]
    command: ["python", "-m", "scripts.backup"]
    env_file: .env
    environment:
      <<: *cvi-env
      CVI_BACKUP_RETENTION: "7"
    volumes:
      - cvi-data:/data
      - cvi-backups:/backups
    restart: "no"

  ollama:
    image: ollama/ollama:latest
    profiles: ["local-models"]
    environment:
      OLLAMA_HOST: 0.0.0.0
    volumes:
      - ollama-models:/root/.ollama
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped

volumes:
  cvi-data:
  cvi-backups:
  ollama-models:
```

### `compose.dev.yaml`

- Overrides `api.command` with `uvicorn ... --reload`.
- Bind-mounts `./agent`, `./frontend`, `./api.py`, `./scripts` into `/app`.
- Publishes `8000:8000`.
- Adds a one-shot `test` service: `pytest -q -rs`, with `GROQ_API_KEY=dummy-for-offline-tests`.

### `compose.prod.yaml`

- Adds `web` (Nginx) publishing `80:80` and `443:443`.
- Mounts `./deploy/nginx.conf:ro`, `./deploy/certs:ro`, `./frontend:/usr/share/cvi/frontend:ro`.
- Removes the API host port; sets `api.depends_on` health condition.
- Sets `restart: unless-stopped`, `init: true`, log driver options (`max-size`, `max-file`).
- Suggested env for offline production:

```env
CVI_LLM_PROVIDER=ollama
CVI_OLLAMA_BASE_URL=http://ollama:11434
LOCAL_LLM_MODEL=llama3.2:3b
CVI_VOICE_STT_PROVIDER=local
CVI_VOICE_TTS_PROVIDER=browser
CVI_VOICE_STT_FALLBACKS=local,typed
CVI_VOICE_TTS_FALLBACKS=browser,local
```

A cloud-assisted model profile is also supported when a Groq key is available.
Storage, speech, and participant traffic remain self-hosted; only the LLM
boundary changes:

```env
CVI_LLM_PROVIDER=groq
GROQ_MODEL=qwen/qwen3.8-27b
CVI_LLM_STRUCTURED_METHOD=function_calling
CVI_EVALUATOR_MODEL=
# GROQ_API_KEY=set_in_the_deployment_secret_store
```

Leave `GROQ_API_KEY` unset for the default self-hosted profile. The local
`.env` may use Groq for faster interviews, but the offline Compose overlay
still forces Ollama so tests and offline runs never require network access.

---

## 6. Nginx routing (both targets)

Requirements:

- Serve only static assets directly: `/`, `/style.css`, `/app.js`, `/theme.js`, `/audio-worklet.js`.
- Proxy `/researcher*` to the API so the existing Basic-auth middleware is never bypassed.
- Proxy `/sessions*`, `/health`, and `/debug*` to the API.
- Upgrade WebSocket connections for `/sessions/{id}/voice`.
- Set secure headers and HSTS in prod.

Compose upstream: `http://api:8000`.  
VM upstream: `http://127.0.0.1:8000` (same file shape, separate `deploy/nginx-vm.conf`).

Microphone access requires HTTPS (or `localhost`), so the VM target must terminate TLS at Nginx; do not expose raw HTTP for participants.

---

## 7. Health checks, logs, migrations, backups

### Health

- `GET /health` remains the simple status endpoint.
- `GET /health/live` (process alive) and `GET /health/ready` (DB readable, model provider configured) are implemented; Compose uses the readiness endpoint for its healthcheck.
- Readiness returns 503 when SQLite is unavailable, `CVI_LLM_PROVIDER` is invalid, or the Groq provider has no `GROQ_API_KEY`. It does not call out to the model provider, so it stays fast and offline-safe.

### Structured logs

- `print()` calls in the agent package were replaced with a stdlib `logging` JSON formatter configured by `CVI_LOG_FORMAT=json` (default) or `text`.
- Middleware generates/propagates `X-Request-ID`, returns it on every response, and includes it in every application log line.
- Log to stdout only; let Docker/VM log rotation handle retention. No external log SaaS.

### Migrations

- `scripts/migrate.py` creates `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT)` and applies idempotent SQL from `agent/read_model.py` plus future numbered migrations.
- Enable `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` for the single-writer/multi-reader pattern.
- Run as a one-shot service before `api`; keep `CREATE TABLE IF NOT EXISTS` as a safe fallback.

### Backups

- `scripts/backup.py` uses `sqlite3.Connection.backup()` (online-safe) to write `/backups/cvi-<UTC timestamp>.sqlite3`.
- `CVI_BACKUP_RETENTION` prunes older backups.
- Run with `docker compose run --rm backup`; on the VM target, schedule the same command via a host `cron` or `systemd` timer. No extra container is needed.

---

## 8. Security and multi-tenancy

Implemented in the application (not deployment add-ons):

1. **Authorization** — `agent/auth.py` provides one `require_researcher` dependency used by every researcher endpoint, `/debug/*`, `POST /sessions`, and the researcher static assets. The legacy shared code is an admin account; named accounts are optional. Researchers see only sessions they created (plus legacy ownerless sessions); admins see all tenant sessions.
2. **Tenant isolation** — `tenant_id` is stored on every tenant-scoped durable table, including LangGraph checkpoint, blob, and write tables, and reads are filtered by the authenticated deployment tenant (`CVI_TENANT_ID`). `002_tenant_audit_fields` upgrades existing databases; `004_durable_langgraph_checkpoints` adds the checkpoint tables.
3. **Auditability** — `session_events` carries `actor`, `action`, `resource`, `request_id`, and `tenant_id`; a separate `audit_log` records session creation, interview start, report generation, and CSV export. `GET /researcher/audit` exposes tenant-scoped entries.

Still open for a later phase:

1. Hash named-account passwords at rest instead of reading them directly from `CVI_RESEARCHER_ACCOUNTS`. Keep HTTP Basic only behind HTTPS; otherwise move to a session/cookie flow.
2. Record link revocation and expiry changes in the audit log (issuance is already recorded).
3. Add per-researcher invitation templates or custom roles beyond the current `researcher` / `admin` pair. API session state, pending interrupts, jobs, voice outbox events, and session leases are already durable.

---

## 9. Offline operation target

A complete interview without paid APIs or SaaS:

- `CVI_LLM_PROVIDER=ollama`, `CVI_OLLAMA_BASE_URL=http://ollama:11434`.
- `CVI_VOICE_TTS_PROVIDER=browser` (browser speech synthesis, no network).
- `CVI_VOICE_STT_PROVIDER=local` pointing at a self-hosted STT endpoint, or `typed` only.
- No `SARVAM_API_KEY`; legacy realtime WebSocket path is not used.
- Pull the model once: `docker compose --profile local-models run --rm ollama ollama pull llama3.2:3b`.

This requires the two adapter fixes listed in section 1 and `langchain-ollama` in `requirements.txt`.

---

## 10. Implementation sequence

1. Add `langchain-ollama` to `requirements.txt`; add `CVI_OLLAMA_BASE_URL` support in `agent/llm.py`.
2. Create `.dockerignore`, `Dockerfile`, `requirements-dev.txt`.
3. Create `compose.yaml`, `compose.dev.yaml`, `compose.prod.yaml`.
4. Add `scripts/migrate.py` and `scripts/backup.py`.
5. Add `deploy/nginx.conf` (done). `deploy/nginx-vm.conf` is still to do for the host-Nginx path.
6. Add structured logging + request IDs; add `/health/live` and `/health/ready` (done).
7. Add tenant columns and audit fields through the migration layer; refactor API auth into dependencies (done).
8. Add the repository-backed job queue, voice outbox, pending-action store, and `worker` command (done). Production enables `CVI_WORKER_ENABLED=1` on both API and worker; tests keep in-process executors.
9. Update `.env.example`; rerun the full offline suite inside the dev Compose service.
10. Verify both paths: Compose local (`docker compose -f compose.yaml -f compose.dev.yaml up`) and VM-style (`compose.prod.yaml` or host Nginx).
