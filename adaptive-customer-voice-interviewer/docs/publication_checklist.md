# Publication and go-live checklist

Source publication is complete. The code is in
`JatinKumarSharma/Agentic_AI_Projects/adaptive-customer-voice-interviewer`.
This checklist now covers the remaining hosted go-live work.

---

## Current status

### Complete

- LangGraph interview engine and evidence contract implemented.
- Participant capability, lifecycle, link expiry, abuse limits, tenant
  isolation, audit, migrations, backups, health checks, and structured logs
  implemented.
- Durable worker queue, fencing tokens, leases, heartbeats, voice outbox, and
  ack-based delivery implemented.
- Two conversational layers before each next question implemented and verified.
- Participant consent gate added to the welcome panel.
- Tokenless rotating demo invite flow added:
  public URL `/?demo=<slug>` exchanges the slug for a short-lived capability;
  the raw capability is returned once to the browser and removed from the URL.
- Offline suite: **415 passed, 4 skipped**.
- Compose configuration validated for base, dev, prod, and offline overlays.
- MIT `LICENSE`, portfolio links, and signed GitHub commits are in place.

### Remaining before public traffic

- Rotate all local credentials.
- Provision the VPS, domain, DNS, and TLS.
- Set production secrets and deploy `compose.prod.yaml`.
- Verify PostgreSQL roles, migrations, backup/restore, and tenant isolation.
- Provision rate-safe Groq capacity or a strict concurrent-demo queue.
- Set the study's retention/contact details in participant-facing policy.
- Create a small pool of demo sessions with `demo_slug` values.
- Run the final live smoke test below.

---

## 1. Credential rotation

Rotate before public deployment:

- `GROQ_API_KEY`
- `RESEARCHER_ACCESS_CODE`
- PostgreSQL passwords
- named researcher account passwords in `CVI_RESEARCHER_ACCOUNTS`

Keep these in the host secret store or `.env` outside Git. Never commit them.
The repository already ignores `.env`, databases, backups, logs, certs, keys,
models, and ZIP snapshots.

---

## 2. VPS and TLS

Chosen profile: **VPS + domain + rate-safe Groq**.

Provision:

1. Docker and Docker Compose on the VPS.
2. DNS A/AAAA record for the chosen domain pointing at the VPS.
3. TLS certificate and key for Nginx. Use Let's Encrypt or your proxy's secret
   store; do not commit certificate files.
4. Firewall rules allowing HTTP/HTTPS and SSH only.
5. A persistent backup destination with a tested restore path.

Start with:

```powershell
docker compose -f compose.yaml -f compose.prod.yaml config --quiet
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

Required production variables include:

```dotenv
CVI_PERSISTENCE_BACKEND=postgresql
CVI_MIGRATION_DATABASE_URL=postgresql://...
CVI_RUNTIME_DATABASE_URL=postgresql://...
CVI_BACKUP_DATABASE_URL=postgresql://...
POSTGRES_PASSWORD=...
CVI_RUNTIME_DB_PASSWORD=...
CVI_BACKUP_DB_PASSWORD=...
CVI_LLM_PROVIDER=groq
GROQ_API_KEY=...
GROQ_MODEL=qwen/qwen3.8-27b
CVI_LLM_STRUCTURED_METHOD=function_calling
CVI_EVALUATOR_MODEL=
RESEARCHER_ACCESS_CODE=...
CVI_TENANT_ID=...
CVI_DEMO_LINK_TTL_MINUTES=15
```

The UI uses the request origin, so no `PUBLIC_BASE_URL` variable is required.

---

## 3. PostgreSQL verification

On the production host, verify:

- `migrate` completes and fails closed on schema drift.
- `api` and `worker` use the DML-only runtime DSN.
- `backup` uses the read-only DSN and produces a restorable backup.
- tenant IDs are stamped on sessions, cache keys, snapshots, events, and audit.
- report claims retain `evidence_id`, `response_id`, and `source_quote`.

---

## 4. Model capacity

The local CPU profile is too slow for the first-question target. Public traffic
must use one of:

- a rate-safe/paid Groq tier;
- a GPU-backed self-hosted model with measured latency;
- a strict demo-only concurrency queue.

The current implementation supports the Groq boundary without changing storage
or speech services. Do not expose a public demo backed by a free-tier key with
unbounded traffic.

---

## 5. Participant privacy and consent

The UI now shows a consent panel before **Begin interview** covering recording,
transcription, evidence conversion, report visibility, retention, and deletion
requests.

Before launch, decide and publish outside the repository:

- the study's retention period;
- the researcher contact for access/deletion requests;
- who inside the team may see reports;
- how deletion requests interact with immutable audit/checkpoint records.

The consent UI gates the interview but does not itself create a legal policy.

---

## 6. Demo link strategy

The public demo URL must be tokenless:

```text
https://<your-domain>/?demo=<slug>
```

Create a small pool of researcher-managed sessions through authenticated
`POST /sessions` with a shared `demo_slug`, for example:

```json
{
  "company_id": "demo-tenant",
  "company_name": "Demo Company",
  "product_id": "demo-product",
  "product_name": "Demo Product",
  "product_category": "SaaS",
  "research_goal": "Collect a short portfolio demo interview.",
  "demo_slug": "portfolio-demo"
}
```

The public broker:

- selects one unclaimed session from that slug's pool;
- rotates its participant capability;
- gives it a short `CVI_DEMO_LINK_TTL_MINUTES` deadline;
- records a `demo_invite_issued` event and audit entry;
- returns the token only in the one-time response body.

Never publish the `participant_url` returned by authenticated `POST /sessions`.
Never paste a raw `participant_token` into a public page or README.

---

## 7. Final live smoke test

Run on production after deployment:

1. `GET /health/live` returns OK.
2. `GET /health/ready` reports database OK and the configured model provider.
3. Researcher auth returns 401/503 when misconfigured.
4. Create a demo session pool with authenticated researcher credentials.
5. Open `https://<domain>/?demo=<slug>` in a clean browser profile.
6. Confirm the address bar remains tokenless after capability issuance.
7. Confirm compatibility and mic checks run automatically.
8. Confirm **Begin interview** requires consent and a voluntary click.
9. Confirm the loading graphic appears while the first question is prepared.
10. Confirm the first question renders within the chosen target.
11. Speak a 7–10 second answer and stop naturally.
12. Confirm the immediate 1–3 word acknowledgement plays at speech end.
13. Confirm the contextual reflection plays after decomposition.
14. Confirm the next question appears only after both voice layers finish.
15. Open the report and verify every claim has evidence provenance.
16. Confirm tenant/audit records exist for creation, demo issuance, start,
    response, report, and export.

After the smoke test passes, update the live demo URL in `PORTFOLIO.md`.
