# Customer Voice Interviewer (CVI)

CVI is a hosted, evidence-first customer and market intelligence platform. It uses **LangGraph** as its research engine to run adaptive voice interviews, decompose answers into provenance-bound evidence, and generate reports where every claim cites the response it came from.

Participants never configure the study. A researcher preconfigures the company, product, goal, and audience, then shares a capability-bearing invite link. The participant sees only a themed interview experience with automatic microphone/compatibility checks, server-side VAD, and a voluntary **Begin interview** button.

> **Status:** implementation and offline verification are complete. Hosted production requires the deployment checklist in [`docs/publication_checklist.md`](docs/publication_checklist.md) before go-live.

---

## What it does

- **Adaptive LangGraph interviews** — meta-planning, proposal generation, evaluation, decision policy, answer decomposition, state/reflection updates, and question delivery.
- **Evidence-first reports** — every report claim carries `evidence_id`, `response_id`, and a verbatim `source_quote`.
- **Voice-first UX** — server-side Silero VAD, local STT/TTS, automatic response capture, and manual controls that are not hidden as backups.
- **Two-layer conversation before each next question**
  1. A model-free, 1–3 word acknowledgement emitted at VAD speech end, before transcript finalization.
  2. A short, warm reflection generated after answer decomposition, in parallel with the research graph.
  - The participant UI waits for both spoken layers before rendering the next question.
- **Consent-gated participation** — participants must confirm they understand recording, evidence use, access, retention, and deletion requests before the interview starts.
- **Tokenless rotating demo links** — a public `/?demo=<slug>` URL exchanges a slug for a short-lived capability; raw tokens stay out of the address bar.
- **Tenant and researcher isolation** — named researcher accounts, roles, tenant stamping, ownership filtering, and fail-closed hosted auth.
- **Durable background work** — repository-backed job queue, fencing tokens, leases, heartbeats, voice outbox, and ack-based event delivery.
- **Auditable memory** — PostgreSQL production persistence, SQLite offline-test persistence, versioned migrations, backups, structured logs, health checks, and request IDs.
- **Replaceable boundaries only** — storage, voice/speech, and LLM provider can be swapped through configuration. No paid AI API or third-party SaaS is required by default.

---

## Architecture at a glance

```text
Participant invite link or tokenless demo slug
        |
        v
FastAPI participant UI  <---- voice websocket ---->  Local speech service
        |                                             (Silero VAD + STT/TTS)
        | status / voice-events / responses
        v
FastAPI API  <-------- durable job queue --------  Worker
        |                                            |
        | LangGraph stream                           | contextual engagement
        v                                            v
Repository + checkpointer + read model + voice outbox + audit log
        |
        v
Evidence-bound analysis report
```

### Key modules

| Path | Purpose |
|---|---|
| `api.py` | FastAPI routes, participant/researcher boundaries, voice sockets, durable job wiring |
| `agent/graph.py` | LangGraph interview engine and node sequencing |
| `agent/llm.py` | Provider-neutral LLM boundary with per-purpose temperature settings |
| `agent/answer_decomposer.py` | Produces structured evidence with response provenance |
| `agent/voice/` | Acknowledgement policy, contextual engagement, voice profiles, and event contracts |
| `agent/jobs/` | Durable job service and fencing/lease logic |
| `agent/persistence/` | Repository adapters and LangGraph checkpointer |
| `deploy/speech/` | Self-hosted STT/TTS and server-side VAD service |
| `frontend/` | Participant UI, theming, timing instrumentation, and voice playback queue |
| `scripts/` | Migrations, backups, model bootstrap, and worker entrypoint |
| `tests/` | Offline suite with all model/provider boundaries mocked |

---

## Run the tests

The offline suite mocks the LLM, speech, and network boundaries. It does **not** require Ollama, Groq, a microphone, a GPU, or a real database server.

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q
```

Latest verified result: **435 passed, 4 skipped**.

---

## Run locally

1. Copy the example configuration:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Fill in secrets locally only. Never commit `.env`.

3. Start the offline-compatible stack:

   ```powershell
   docker compose -f compose.yaml -f compose.local.yaml --profile offline up -d --build
   ```

4. Open `http://localhost:8000/researcher` with the researcher credentials from your local `.env`.

5. Create a session and copy the generated participant invite URL. Participants open that link; they never enter company, product, audience, or goal details.

See [`docs/offline.md`](docs/offline.md) for the full offline checklist and [`docs/deployment.md`](docs/deployment.md) for hosted deployment details.

---

## Model policy

The default self-hosted profile uses Ollama and local speech services. On CPU-only machines, large local reasoning models are too slow for the first-question target.

A faster cloud-assisted profile is supported by changing only the LLM boundary:

```dotenv
CVI_LLM_PROVIDER=groq
CVI_LLM_STRUCTURED_METHOD=function_calling
GROQ_MODEL=qwen/qwen3.8-27b
CVI_EVALUATOR_MODEL=
```

Storage, participant traffic, and speech remain self-hosted. Keep `GROQ_API_KEY` in the deployment secret store, never in the repository. A local smoke with this profile rendered the first question in about **4–6 seconds**; Groq free-tier rate limiting can still affect sustained public traffic.

---

## Security and privacy notes

- Participant links carry a high-entropy capability token; only its hash is stored.
- Session lifecycle is one-way and cannot be restarted after completion.
- Every participant endpoint enforces capability, expiry, lifecycle, and abuse limits.
- Voice events remain visible until explicitly acknowledged; they are not destructively drained.
- Researcher create endpoints fail closed in hosted mode unless local unauthenticated mode is explicitly enabled.
- Tenant IDs are stamped on sessions, cache keys, snapshots, events, and audit records.
- Runtime and backup database roles are least-privilege; migrations use a separate DDL-capable role.
- Participant transcripts are durable research evidence. A public deployment must include consent, retention, and deletion policy outside this repository.

Details are in [`docs/security_remediation_plan.md`](docs/security_remediation_plan.md).

---

## Portfolio notes

See [`PORTFOLIO.md`](PORTFOLIO.md) for a concise case-study version of this project.

### Demo link

**Researcher Dashboard (demo):** https://cvi-portfolio.onrender.com/researcher

This is a demo link only. The researcher route is access-controlled, so treat it as a demo link and not as a production go-live claim. No credentials, participant data, client data or recordings are published in this repository.

---

## Future scope

The items below are future scope, not claims about the current demo. They are split into platform foundations, research measurement, and a longer-term market-intelligence direction.

### Foundational platform scope

- **Production-grade deployment:** Move from the current free Render + Neon demo setup to the strict production target: VPS/custom domain deployment with Docker Compose, managed secrets, HTTPS, backups, monitoring, and production auth hardening.
- **Reliable, higher-accuracy voice:** Upgrade speech capabilities beyond free-tier constraints by using higher-RAM/paid hosting or self-hosted STT/TTS models for better transcript accuracy, lower latency, and always-on reliability, while preserving typed-input fallback.
- **Pluggable provider architecture:** Extend the existing client-replaceable boundaries for storage/memory, voice/speech, and LLM model/provider so deployments can swap demo/free providers (Groq, browser speech, Neon/Render) with self-hosted or enterprise-grade alternatives without changing the core evidence-first interview workflow.
- **Self-hosted/privacy-first profile:** Strengthen the strict self-hosted deployment profile for privacy-sensitive environments, keeping participant data, speech processing, and model inference within controlled infrastructure.

### Research measurement layer

A stronger measurement and operations layer around the loop already built:

- **Question-quality eval set** — consented, human-reviewed transcripts scored for follow-up relevance, redundancy and research progress.
- **Live timing dashboard** — first-question latency, acknowledgement timing, engagement emit time and failed-job counts in one view.
- **Researcher review workflow** — human confirmation of evidence claims plus published retention and deletion controls for each study.
- **Cross-session insight views** — aggregate themes across sessions with tenant isolation and source quotes preserved end to end.

### Longer-term direction: consumer and market intelligence

Evolve CVI from an adaptive AI interviewer into an autonomous consumer and market intelligence platform that connects what people say, what they do, and what the market reveals.

#### CVI Intelligence Fusion Engine — evidence worlds

- **Stated perception / consumer intelligence** — what people say, captured through first-party interviews.
- **Observed behavior / market & commercial signals** — what people do, captured only through authorized behavioral or commercial sources.
- **Public market sentiment / external signals** — what the market reveals through public, provenance-bearing sources.

The fusion engine should align products/variants, time periods, regions, and segments; detect trends; compare evidence; identify contradictions; and generate testable hypotheses.

#### Key capabilities

- Perception–behavior gap detection
- Unified market understanding
- Continuous market monitoring
- Historical intelligence
- Decision support

#### Data strategy

First-party data foundation; authorized external data; public signal enrichment; clear boundaries against private marketplace data, login bypass, CAPTCHA bypass or access-control bypass.

#### Trust guardrails

Preserve provenance; do not merge truths blindly; hypotheses not automatic conclusions; privacy and compliance; reliable product identity via GTIN/EAN/UPC/ASIN/SKU/MPN/model number.

#### Phased roadmap

1. Single-product case study
2. Continuous monitoring
3. Authorized commercial integrations
4. Fusion engine
5. Commercial intelligence service

---

## Publication checklist

Do not publish until every item in [`docs/publication_checklist.md`](docs/publication_checklist.md) is checked. In particular: rotate existing local credentials, confirm `.env` and `data/` are ignored, provide the repository destination, and provide the production domain/TLS/model capacity. The repository is MIT licensed; see [`LICENSE`](LICENSE).
