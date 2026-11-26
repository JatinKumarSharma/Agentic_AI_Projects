# CVI Security Remediation Plan

**Prepared:** 2026-11-25  
**Scope:** Participant boundary, researcher boundary, durable worker/queue boundary, persistence, migrations, backups, and hosted deployment configuration.  
**Status:** Remediation implemented. Items 1 through 10 are covered by regression tests, including the durable-worker and lease hardening folded into implementation step 6. Findings were identified by static review; no exploit was executed against a live deployment.

---

## Purpose

CVI accepts invitations, participant speech, typed responses, researcher-created sessions, and durable background work. These paths share session IDs, tenant IDs, repository records, queues, and the LangGraph checkpointer. The security review found several places where a person with a leaked session ID, a misconfigured deployment, or access to an internal component could manipulate interview state, consume resources, suppress voice output, or read data outside the intended boundary.

This document records the remediation order and the reason each item is being changed. It is intended to keep the work evidence-first, bounded, and auditable.

---

## Guiding principles

1. **Explicit boundaries over implicit trust.** A session UUID is an identifier, not sufficient proof of participant authority.
2. **Explicit lifecycle over inferred state.** Interview start, in-progress, and completion states must be persisted, not inferred from the presence or absence of an interrupt.
3. **Fail closed in hosted mode.** Production must not depend on permissive local-development defaults.
4. **Every mutation is authorized, bounded, and auditable.** Participant, researcher, and worker actions must have clear limits and durable evidence.
5. **Worker work is fenced.** Queue claims, completion, retries, and leases must be tied to an owning worker and a valid state transition.
6. **No new external dependencies.** Remediation stays within the existing FastAPI, PostgreSQL/SQLite repository, and self-hosted model/speech boundaries.

---

# Prioritized remediation list

## 1. Add explicit interview lifecycle state and reject restart after completion

**Why we are doing this:**  
`/sessions/{id}/start` currently decides whether an interview can start by checking whether a pending interrupt exists. Completion clears the pending interrupt, so a person with the session UUID can call `/start` again, inject a fresh initial state into the same graph thread, and overwrite or duplicate durable research data. This is a direct interview-hijack and research-integrity risk.

**Planned outcome:**  
Sessions have a durable lifecycle state such as `created`, `started`/`in_progress`, `completed`, and `failed`. `/start` only works from `created`. Re-running or resetting a completed interview requires an authenticated researcher action and an audit record.

**Implemented:**  
`agent/session.py` now defines `interview_status` and one-way transitions. `api.py` persists a `starting` claim before the first graph call, normalizes legacy sessions, and marks terminal states from `extract_next_action()`. New tests in `tests/test_interview_lifecycle.py` prove that completed and in-progress interviews cannot be restarted after a pending interrupt is lost.

---

## 2. Add a separate participant capability and enforce it on participant endpoints

**Why we are doing this:**  
The session UUID is currently the only capability needed to start, advance, submit responses, read status, drain voice events, and use STT/TTS. A leaked UUID from a screenshot, log, browser history, or shared link allows participant impersonation and response forgery.

**Planned outcome:**  
The invite link carries a separate high-entropy participant token. Every participant endpoint and websocket validates that token. The UUID remains a public identifier.

**Implemented:**  
`agent/participant_token.py` generates, hashes, and compares a 256-bit URL-safe capability. `POST /sessions` returns the raw token only inside `participant_url` and stores only `participant_token_hash` plus an issue timestamp on the session. `api.py` enforces the capability on all nine participant HTTP routes and both websocket routes. Legacy sessions without a stored hash remain usable. Tests in `tests/test_participant_capability.py` and `tests/test_api_participant_view.py` cover missing, invalid, and valid capabilities, including websockets. The participant UI forwards the capability from the invite URL.

---

## 3. Apply link expiry and participant authorization consistently

**Why we are doing this:**  
Link expiry is checked only on invitation and start. Expired links can still use speech endpoints, submit timings, poll status, and drain voice events. Starting an interview also permanently exempts it from expiry, which allows a leaked link to be made durable.

**Planned outcome:**  
Every participant endpoint applies the same expiry/capability policy, with an explicit exception only for valid in-progress interviews.

**Implemented:**  
Expiry is now enforced inside `_require_participant_session()`, so every participant HTTP route inherits it, and inside both voice websocket handlers via `reject_websocket_if_link_expired()`. Started interviews remain exempt. `tests/test_api_link_expiry.py` proves all participant surfaces return HTTP 410/WS 4410 for expired links while started interviews still work.

---

## 4. Make voice-event delivery acknowledgement-based and non-destructive

**Why we are doing this:**  
`GET /sessions/{id}/voice-events` drains events. Another party with the session ID can consume acknowledgements, reflections, and engagement messages before the real participant sees them.

**Planned outcome:**  
Voice events remain visible until explicitly acknowledged by the authorized participant connection. Event IDs act as cursors, and replay is controlled.

**Implemented:**  
Both repository backends now have `list_voice_events()` and `acknowledge_voice_events()`. `GET /sessions/{id}/voice-events` reads without draining and returns each payload with its `event_id`. `POST /sessions/{id}/voice-events/ack` marks listed events delivered for the authorized session only. Offline mode uses an ack-tracked in-memory outbox and can still adopt legacy queue entries. The participant UI deduplicates by `event_id` and retracts its seen marker if acknowledgement fails. Tests cover non-destructive replay, session-scoped acknowledgement, and worker-mode outbox behavior.

---

## 5. Add input bounds, quotas, and rate limits

**Why we are doing this:**  
Transcripts, timing metadata, STT prompts, filenames, audio uploads, TTS text, and websocket streams are insufficiently bounded. A participant could inflate LLM token usage, poison analytics, exhaust local CPU/memory, or drive paid speech-provider cost.

**Planned outcome:**  
Realistic caps are enforced for transcript length, metadata shape/size, audio bytes, TTS text, websocket duration/bytes, concurrent streams, and per-session/per-IP request rates.

**Implemented:**  
`agent/limits.py` owns payload, metadata, audio, TTS, websocket, and request-rate constants. Pydantic models now reject oversized transcripts, prompts, filenames, event IDs, timing metadata, synthesis delivery metadata, and declared request bodies (HTTP 413) before work is scheduled. Both voice websocket paths enforce frame/total/duration budgets and at most two concurrent streams per session. `tests/conftest.py` disables rate limiting by default for deterministic offline tests; `tests/test_abuse_limits.py` opts in and proves per-session throttling plus each major payload and stream boundary. Rate-limit windows are configurable through `CVI_RATE_LIMIT_*` environment variables; payload caps are intentionally code constants.

---

## 6. Require strict researcher authentication for hosted session creation

**Why we are doing this:**  
`POST /sessions` currently permits unauthenticated creation when researcher auth is unconfigured. In hosted mode this allows arbitrary campaign creation, participant-link generation, audit pollution, and downstream cost abuse.

**Planned outcome:**  
Hosted/production mode requires strict researcher authentication. Any local unauthenticated mode must be explicitly opt-in and cannot be the default.

**Implemented:**  
`POST /sessions` now uses a fail-closed dependency. If no researcher code or named account is configured, the request returns 503 unless `CVI_ALLOW_UNAUTHENTICATED_LOCAL=1` is explicitly set for a non-hosted local process. Tests cover both the 503 default and the explicit local opt-in.

---

## 7. Stamp tenant from authenticated researcher context on session creation

**Why we are doing this:**  
Named researcher accounts can specify `tenant_id`, but created sessions are stored with the deployment tenant because the session dictionary does not carry the context tenant. This can make a researcher unable to see their own session while exposing it to default-tenant admins.

**Planned outcome:**  
Session creation writes the authenticated context tenant explicitly into the session, cache, snapshots, events, and audit records. Caches are keyed by `(tenant_id, session_id)`.

**Implemented:**  
`create_session()` stamps the authenticated context tenant onto the session and passes it to snapshots, events, and audit records. `api.sessions` is a tenant-qualified cache keyed by `(tenant_id, session_id)`, and `resolve_session()` hydrates with an explicit tenant filter. A regression test proves a named tenant's session is invisible to another tenant.

---

## 8. Harden the durable worker queue with fencing and ownership checks

**Why we are doing this:**  
Stale-job recovery has no heartbeat or fencing token. `complete_job()` and `fail_job()` do not verify current status or worker ownership. Job payload identity is not validated against row/session/tenant identity. A rogue or misconfigured worker could duplicate, lose, forge, or hijack jobs.

**Planned outcome:**  
Jobs have ownership, fencing tokens, heartbeats/expiry, validated payloads, and explicit state transitions. Completion/failure updates are conditional on the current owner and state.

**Implemented:**  
Schema version `007_worker_fencing` adds `fencing_token` and `heartbeat_at` to `background_jobs` in both SQLite and PostgreSQL. `claim_jobs()` issues a fencing token; `complete_job()` and `fail_job()` only update a `processing` job that still holds that token, and stale recovery clears the token when it requeues or fails a job. `heartbeat_job()` refreshes ownership during long handlers. `enqueue_job()` stamps `job_id`, `job_type`, `session_id`, and `tenant_id` into the stored payload, and `DurableWorker` requires those fields to match the claimed row before dispatch. It keeps a heartbeat thread alive while a handler runs and logs rejected completions/failures. `CVI_WORKER_HEARTBEAT_SECONDS` tunes the interval. `tests/test_worker_hardening.py` and the updated queue tests cover forged ownership, invalid state transitions, payload mismatch, heartbeat liveness, and shared worker labels.

---

## 9. Fix lease-owner generation so replicas cannot share `CVI_WORKER_ID`

**Why we are doing this:**  
API and worker replicas can share a configured `CVI_WORKER_ID`. The lease implementation treats the same owner ID as renewable ownership, so two processes can acquire, renew, or release the same session lease.

**Planned outcome:**  
Lease owners include a per-process UUID/PID. A configured ID is only a label, never the sole owner identity.

**Implemented:**  
`worker_identity()` produces a unique `label-<random>` owner per process. `DurableWorker` uses it as the job fencing token, and `api.py` uses it for `_SESSION_LEASE_OWNER`, so replicas sharing `CVI_WORKER_ID` cannot renew or release each other's session leases. Two workers with the same configured label still claim different fencing tokens. Tests in `tests/test_worker_hardening.py` prove the identity and token separation.

---

## 10. Separate DB roles and verify migration invariants

**Why we are doing this:**  
Runtime API/worker services use the same DDL-capable DB URL as migrations and backups. Migrations also stamp versions without verifying that critical unique indexes and constraints still exist. A compromised runtime process or schema drift could silently disable security invariants such as active-job deduplication.

**Planned outcome:**  
Migrations use DDL-capable credentials; API/worker use DML-only credentials; backups use read-only credentials. Migration verification fails closed on missing or altered security constraints.

**Implemented:**  
`scripts/bootstrap_postgres_roles.py` creates/updates `cvi_runtime` and `cvi_backup` roles with least-privilege grants and default privileges before migrations run. `compose.prod.yaml` gives `migrate` the DDL-capable owner URL, `api`/`worker` the DML-only runtime URL, and `backup` the read-only URL. `agent/persistence/invariants.py` declares required tables, tenant columns, primary keys, and security indexes; both repository adapters verify them without repairing drift, and `scripts/migrate.py` raises `MigrationInvariantError` on any issue.

---

# Remediation rules for implementation

1. Work one file or subsystem at a time.
2. Write the smallest relevant failing test first.
3. Run the targeted test after each change.
4. Run the full suite only at milestones.
5. Keep offline tests independent of Ollama, Groq, GPUs, microphones, and real speech.
6. Preserve the documented HTTP Basic contract and hosted self-contained boundaries unless explicitly changed.
7. Do not introduce Redis, Celery, paid SaaS, or client-controlled storage/LLM/speech dependencies.
8. Add or update documentation when behavior or operational contracts change.

---

# Current implementation order

1. **Done:** Explicit interview lifecycle state and restart-after-completion rejection.
2. **Done:** Participant capability and endpoint enforcement.
3. **Done:** Consistent link expiry on every participant surface.
4. **Done:** Voice-event acknowledgement model.
5. **Done:** Input bounds, quotas, and rate limits.
6. **Done:** Worker fencing, payload validation, and lease-owner hardening (original plan items 8 and 9).
7. **Done:** Hosted auth defaults, tenant stamping, DB roles, and migration verification.

---

# Validation checklist

Each remediation should include:

- [x] Smallest regression test added first.
- [x] Targeted test passes.
- [x] Related endpoint or repository behavior verified.
- [x] Documentation updated where contracts changed.
- [x] Full suite passes at the milestone.
- [x] Compose config validation passes when deployment files change.
