# Publication and go-live checklist

This project is **ready to prepare for publication**, but it is **not ready to push or make public yet**. The implementation and offline verification are complete; the remaining items are secret hygiene, repository destination, licensing, and hosted deployment decisions.

---

## Current status

### Ready

- LangGraph interview engine and evidence contract implemented.
- Participant capability, lifecycle, link expiry, abuse limits, tenant isolation, audit, migrations, backups, health checks, and structured logs implemented.
- Durable worker queue, fencing tokens, leases, heartbeats, voice outbox, and ack-based delivery implemented.
- Two conversational layers before each next question implemented and verified.
- Offline suite: **410 passed, 4 skipped**.
- Compose configuration validated for base, dev, prod, and offline overlays.
- Root `README.md`, `PORTFOLIO.md`, and this checklist added.

### Not ready yet

- Repository destination was not found locally and GitHub CLI is not installed.
- `.env` contains live local credentials and must never be committed.
- `data/research_read_model.sqlite3` contains real session transcripts and timing data.
- `cvi_voice_integration_snapshot.zip` contains an older source snapshot and compiled files.
- MIT license added as `LICENSE`.
- GitHub CLI setup was requested; authentication is still pending user confirmation.
- No production domain, TLS certificate, PostgreSQL server, or demo-link strategy has been confirmed.
- Live model capacity decision: **rate-safe Groq tier**. The current free-tier key is not sufficient for unpredictable public traffic.
- Next action decision: **prepare only**; do not push until explicitly requested.

---

## 1. Before pushing to GitHub

### Rotate credentials

Rotate these before any public push or public deployment:

- `GROQ_API_KEY`
- `RESEARCHER_ACCESS_CODE`
- any PostgreSQL passwords created for the deployment
- any named researcher account passwords in `CVI_RESEARCHER_ACCOUNTS`

The current values are local-only and ignored by `.gitignore`, but rotation is the safe default once a repository becomes public.

### Verify ignored files

`.gitignore` now excludes `.env`, `.env.*` except `.env.example`, `data/`, SQLite databases, backups, logs, local models, TLS keys/certs, virtual environments, caches, and one-off ZIP snapshots.

Before staging anything, run:

```powershell
git status --short --ignored
git check-ignore -v .env data/research_read_model.sqlite3 cvi_voice_integration_snapshot.zip
```

Only `.env.example` should be committed. No `.env`, database, transcript, certificate, ZIP snapshot, or compiled artifact should appear in the staged file list.

### Choose a license

No `LICENSE` file has been added because the license choice is yours. Common choices:

- **MIT** — permissive, simple portfolio default.
- **Apache-2.0** — permissive with explicit patent grant.
- **Private / no license** — code visible but not legally reusable.

Add the selected `LICENSE` file before pushing if you want others to reuse the project.

### Repository destination

The local workspace is not a Git repository, and no local `Agentic_AI_Projects` directory was found. Provide one of:

1. The GitHub repository URL, for example  
   `https://github.com/<your-account>/Agentic_AI_Projects.git`
2. The local path to the cloned `Agentic_AI_Projects` repository.
3. Permission to install/configure GitHub CLI and authenticate, so the remote can be inspected directly.

Once the destination is known, the safe flow is:

```powershell
git clone <repository-url> Agentic_AI_Projects
cd Agentic_AI_Projects
# create or enter the customer-voice-interviewer subdirectory
# copy the ignored-safe project files from the working directory
git status --short
git add .
git commit -m "Add Customer Voice Interviewer research platform"
git push
```

Do not use `git add -f` on ignored files.

---

## 2. Before making it live

### Hosting requirements

Confirm and configure:

- domain or subdomain
- DNS pointing at the host
- TLS certificate and key in `deploy/certs/` or your proxy's secret store
- `compose.prod.yaml` deployment on a server with enough CPU/RAM for the chosen model profile
- PostgreSQL server reachable through the production DSNs
- backup destination and retention policy
- `PUBLIC_BASE_URL` used in invite links
- researcher accounts/roles and tenant IDs

Start with:

```powershell
docker compose -f compose.yaml -f compose.prod.yaml config --quiet
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

### Production database verification

The local environment could not run a full hosted PostgreSQL smoke test. Before go-live, verify on the target host that:

- `migrate` exits successfully and fails closed on drift.
- `api` and `worker` use the DML-only runtime DSN.
- `backup` uses the read-only DSN and produces a restorable backup.
- tenant isolation and audit queries work with real PostgreSQL.

### Model capacity

The 6–8 second first-question target was met with the fast provider profile, not with a CPU-only large local model. Choose one:

- paid or rate-limit-safe Groq tier for predictable public traffic;
- a GPU-backed self-hosted model with measured latency;
- a demo-only invite queue that limits concurrent interviews.

Do not publish a public demo that relies on the current free-tier key under unknown traffic.

### Participant privacy

Add a participant-facing consent/privacy notice covering:

- what is recorded;
- how transcripts and derived evidence are used;
- who can see the report;
- retention period;
- how deletion requests are handled.

Audit and checkpoints are intentionally immutable, so deletion policy needs an explicit design decision before public participants are involved.

### Demo link strategy

Participants must not enter company, product, audience, or goal details. Create a small set of researcher-managed demo sessions with expiring invite links, and rotate them. Never paste raw invite URLs into public README content because the URL contains the participant capability token.

Use a placeholder in public content:

```text
https://<your-domain>/?session_id=<public-demo-id>&participant_token=<rotated-demo-token>
```

Then publish a safe entry page that issues a short-lived demo invite without revealing a permanent token.

---

## 3. Final live smoke test

Run this on the production host after deployment:

1. `GET /health/live` returns OK.
2. `GET /health/ready` reports `database: ok` and the configured model provider.
3. Researcher auth returns 401/503 as expected when misconfigured.
4. Create a demo session with authenticated researcher credentials.
5. Open the invite link in a clean browser profile.
6. Confirm compatibility and mic checks run automatically.
7. Confirm **Begin interview** requires a voluntary click.
8. Confirm the loading graphic appears while the first question is prepared.
9. Confirm first question renders within the chosen target.
10. Speak a 7–10 second answer and stop naturally.
11. Confirm the immediate 1–3 word acknowledgement plays at speech end.
12. Confirm the contextual reflection plays after decomposition.
13. Confirm the next question appears only after both voice layers finish.
14. Open the report and verify every claim has `evidence_id`, `response_id`, and `source_quote`.
15. Confirm tenant/audit records exist for creation, start, response, report, and export.

---

## 4. Information needed from you

To finish publication, tell me:

1. the GitHub repository URL or local clone path for `Agentic_AI_Projects`;
2. whether you want me to push the code or only prepare the content;
3. your chosen license;
4. the intended live host/domain and model-capacity choice;
5. whether to generate a rotating demo-link flow before launch.
