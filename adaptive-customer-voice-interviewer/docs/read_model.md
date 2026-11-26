# Durable read model

The research engine keeps two distinct kinds of state:

1. **LangGraph checkpointer state** — drives the active interview.
2. **Durable read-model state** — supports dashboards, reports, and traceability after a restart.

Both layers use the same configured repository. `agent/read_model.py` is the
compatibility facade, and `agent/persistence/checkpointer.py` adapts LangGraph's
checkpoint API to the repository boundary.

## Backend boundary

Backend selection lives in `agent/persistence/config.py`. The API resolves a
`PersistenceConfig` at startup and obtains a repository through
`configure_repository()`. Readiness probes call `repository.health_check()`.
The `api.py`, graph, evidence, and report call sites do not change between the
two adapters.

```env
# Hosted production
CVI_PERSISTENCE_BACKEND=postgresql
CVI_DATABASE_URL=postgresql://cvi:change-me@db:5432/cvi_research

# Offline tests only
# CVI_PERSISTENCE_BACKEND=sqlite
```

`agent/persistence/postgres.py` implements the production adapter with
`psycopg[binary]`. It creates the full schema, records the same schema versions,
stores checkpoints as `BYTEA`, and applies tenant filters to every read and
write. Credentials are masked in readiness/log output.

`agent/persistence/sqlite.py` remains the test-only adapter.

Hosted production separates credentials: migrations use the DDL-capable owner
URL, API/worker use a DML-only role, and backups use a read-only role.
`scripts/bootstrap_postgres_roles.py` creates those roles and their default
privileges before migrations. API and worker processes call
`configure_repository(..., ensure_schema=False)` on PostgreSQL, so they set the
DSN without attempting DDL; SQLite offline tests keep automatic schema setup.
`scripts/migrate.py` then calls `verify_migration_invariants()` on the configured
adapter. Security-critical
tables, tenant columns, primary keys, and indexes are declared in
`agent/persistence/invariants.py`; verification never repairs drift, so a
missing or altered constraint fails the migration.

## Configuration

For SQLite tests, set `CVI_READ_MODEL_DB` to choose the database file:

```env
CVI_READ_MODEL_DB=./data/research_read_model.sqlite3
```

The default is `data/research_read_model.sqlite3` under the project root.
The parent directory is created automatically. Production ignores this value.

## Durable LangGraph checkpointer

`agent/persistence/checkpointer.py` implements `RepositoryCheckpointer`, a
tenant-scoped `BaseCheckpointSaver` backed by the configured repository. The
API builds the interview graph with it instead of `MemorySaver`, and
`thread_config(session_id)` attaches the session tenant so resumed graph calls
cannot cross tenant boundaries.

Schema version `004_durable_langgraph_checkpoints` adds:

- `langgraph_checkpoints` — serialized checkpoint state and metadata.
- `langgraph_checkpoint_blobs` — versioned channel values keyed by thread and namespace.
- `langgraph_checkpoint_writes` — pending/intermediate task writes.

Checkpoints are serialized with LangGraph's configured serializer, not with
ad-hoc JSON. Channel values are stored as versioned blobs and rebuilt on read,
matching LangGraph's in-memory saver semantics; this keeps interrupts and
time-travel state correct. `delete_for_runs()` is intentionally a no-op: run
checkpoints are kept for audit rather than silently removed.

## Durable worker queue

Schema version `005_durable_worker_queue` adds:

- `background_jobs` — queued/processing/completed/failed response and engagement jobs, with attempts and retry timing.
- `voice_output_events` — tenant-scoped, acknowledgement-tracked voice outbox listed by the API for a participant session.
- `session_pending_actions` — durable interrupt state so the worker can resume an interview after restart.

The API enqueues only when `CVI_WORKER_ENABLED=1`; hosted production enables
this on both `api` and the separate `worker` service. The worker claims jobs
atomically (`FOR UPDATE SKIP LOCKED` on PostgreSQL), dispatches them through
`agent/jobs/service.py`, and records results back in the same repository.
Offline tests keep `CVI_WORKER_ENABLED=0` and use in-process executors.
The queue methods are available on `ResearchMemoryRepository` and are
delegated by `agent.read_model`.

Schema version `006_session_leases` adds `session_leases`, a tenant-scoped
`session_id` lease owned by one worker with a renewable expiry. The API acquires
the lease before resuming a graph and releases it in a `finally` block, so two
worker replicas never stream the same interview at once. Stale leases expire
using `CVI_SESSION_LEASE_TTL_SECONDS` (default `300`), matching the worker's
stale-job recovery window.

Schema version `007_worker_fencing` adds `fencing_token` and `heartbeat_at` to
`background_jobs` in both adapters. A claim issues a token, and completion,
retry, failure, and stale recovery are conditional on that token and the
current `processing` state. `heartbeat_job()` refreshes long-running claims;
`CVI_WORKER_HEARTBEAT_SECONDS` controls the interval (default `30`, never more
than half the stale window). `enqueue_job()` stamps `job_id`, `job_type`, `session_id`, and `tenant_id` into
the payload; `DurableWorker` requires those fields to agree with the claimed
row and uses a per-process owner identity, so replicas sharing `CVI_WORKER_ID`
cannot finish or renew each other's work.

## Governed evidence retrieval

`agent/evidence_retrieval.py` is the read API for citation-ready evidence. It
reads append-only `research_memory_records`, selects the latest authoritative
snapshot per session, and returns normalized records with `evidence_id`,
`response_id`, verbatim `source_quote`, turn, category, parameter, and the
authoritative record metadata.

```python
from agent.evidence_retrieval import retrieve_evidence, get_evidence_reference

items = retrieve_evidence(
    tenant_id="research-team",
    session_id="session-id",
    response_id="resp_1",
    turn=3,
)
citation = get_evidence_reference("ev_123", tenant_id="research-team")
```

All reads are tenant-scoped. Quotes that do not appear verbatim in the cited
response are suppressed unless `include_unverified=True` is explicitly passed.
Results are deterministic, so analysis and report code can resolve a citation
without reparsing loose graph state.

## Memory-backed reports

`agent/analysis_report.py` keeps two report paths with different roles:

- `build_session_report(session, state)` remains the **writer-side** path. It is
  used while a graph state is being projected into durable memory, so the
  report is validated before the authoritative snapshot is written.
- `build_session_report_from_memory(session_id, tenant_id, session=...)` is the
  **read-side** path. It rebuilds claims from `retrieve_evidence()` and the
  latest authoritative snapshot. The researcher endpoint
  `POST /researcher/analysis/reports/{session_id}/generate` uses this path.

Evidence whose quote is not verbatim is rejected rather than silently dropped.
That keeps a suppressed citation from making an ungrounded report appear clean.

## Researcher evidence search

`GET /researcher/evidence` exposes governed citations to authenticated
researchers. It supports tenant-scoped filters for `session_id`, `evidence_id`,
`response_id`, `turn`, `category`, and `parameter`, plus `include_unverified`
and `limit`.

With `q`, `agent.evidence_retrieval.search_evidence()` applies deterministic
model-free lexical ranking: weighted term frequency over `source_quote`,
`value`, `response_text`, `question`, `category`, and `parameter`, with bonuses
for exact phrases in the source quote or response. Ties keep the normal
evidence order. Without `q`, results keep that deterministic order unchanged.

Each request writes an `evidence.retrieved` audit entry with the filters,
ranking mode, and result count.

## What is persisted

- Session metadata from `POST /sessions`.
- A snapshot after each graph write:
  - `session_created`
  - `interview_started`
  - `interview_advanced`
  - `response_processed`
- Flattened knowledge/evidence, conversation timeline, gaps, quality
  indicators, meta-plan, selected proposal, reflection, and final insights.
- The raw state JSON behind each projection.
- Lifecycle events such as `response_submitted`, `contextual_engagement`,
  `graph_state_saved`, and `response_failed`.
- Evidence-grounded analysis reports with normalized claim and evidence
  ledgers.
- Conversation latency spans in `conversation_timings`, including browser,
  runtime, and voice-chain measurements.

## Conversation timing ledger

`agent/timing.py` writes monotonic latency spans to the read model. The ledger
uses `time.perf_counter_ns()` on the backend and `performance.now()` in the
browser; those clocks are intentionally kept separate by the `source` field and
are never compared directly.

Each row records:

- `session_id` and 1-based `turn`.
- `span_name`, `source` (`frontend`, `runtime`, or `voice_chain`), and optional
  `parent_span_id`.
- `started_ms`, `ended_ms`, and derived `duration_ms`.
- JSON `metadata` such as status, input mode, or response ID.

Key spans are:

- First question: `invitation_resolved`, `permission_check_completed`,
  `welcome_displayed`, `first_question_delivered`, `first_question_rendered`,
  and `first_question_advanced_to_listening`. Participant-controlled reading
  time is recorded separately from backend delivery time.
- Each turn: `response_submission_scheduled`, `response_processing`,
  `participant_response_time`, `next_question_delivered`, and
  `next_question_rendered`.
- Parallel voice chain: `speech_end_acknowledgement_emitted`,
  `contextual_engagement_queued`, `contextual_engagement_generation`,
  `contextual_engagement_emitted`, and
  `speech_end_acknowledgement_rendered`.

The timing tests use a controlled clock and a deliberately blocked engagement
worker to prove that the model-free acknowledgement and response processing are
not delayed by contextual-engagement model generation.

Timing endpoints:

- `POST /sessions/{session_id}/timings` — records a browser span.
- `GET /researcher/sessions/{session_id}/timings` — filtered latency spans.
- `GET /researcher/sessions/{session_id}` — includes `timings` in the projection.

## Evidence-grounded analysis reports

`agent/analysis_report.py` builds deterministic reports from accepted state;
it does not call a model. Every claim must cite an evidence item with:

- `evidence_id`
- `response_id` from the same session
- `source_quote` that is a verbatim excerpt of the stored response

Claim statements are built only from verbatim source quotes; normalized
evidence values remain metadata and never become assertions. If any reference
is missing, duplicated, cross-session, or not verbatim, the report is rejected
and no claims are stored. The SQLite tables are:

- `analysis_reports` — normalized report dimensions and full JSON.
- `report_claims` — one row per grounded claim.
- `claim_evidence` — one row per cited evidence item.
- `response_evidence` — accepted responses with provenance and reporting
  dimensions.
- `response_outcomes` — every classified submission, including invalid
  attempts that never enter the evidence ledger.

### Interview labels

Reports are rebuilt on every persisted graph write, so an unfinished
interview is stored as soon as it has state to report. Its `status_label` is
`unfinished`; completion replaces the same report with `completed`. A
decision-policy stop (`next_action = stop` or any `termination_reason`) counts
as completion even when the controller did not set `interview_complete`. The
`response_pattern_label` comes from the recorded outcome sequence:

- `recovered_after_invalid` — an invalid classification was followed by a
  valid answer. This covers answers that were irrelevant, non-responsive,
  unclear, silent, or affected by an audio issue.
- `continuous_valid` — only valid classifications.
- `unresolved_invalid` — an invalid classification was not followed by a
  valid answer.
- `unclassified` — older snapshots with no recorded outcomes.

The combined `tags` field allows both dimensions to be queried together, for
example `unfinished` plus `recovered_after_invalid`.

Reports can be filtered by `period_start`, `period_end`, `customer_segment`,
`product_category`, `research_goal`, `session_id`, `status_label`,
`response_pattern_label`, and `tag`; the first two use an overlap test against
the session reporting period.

## Dashboard endpoints

- `GET /researcher/sessions` — session summaries from the durable store.
- `GET /researcher/sessions/{session_id}` — full projection plus snapshot
  metadata and event history.
- `GET /researcher/analysis/reports` — filtered reports plus aggregates.
- `GET /researcher/analysis/reports/export.csv` — claim/evidence CSV ledger.
- `GET /researcher/analysis/reports/{report_id}` — one full report.
- `POST /researcher/analysis/reports/{session_id}/generate` — rebuild a report
  from the latest stored state.

## Important limitation

Active LangGraph checkpoints are now durable. `RepositoryCheckpointer` stores
tenant-scoped checkpoints, versioned channel blobs, and pending writes in
`langgraph_checkpoints`, `langgraph_checkpoint_blobs`, and
`langgraph_checkpoint_writes`, using the same database selected by
`CVI_READ_MODEL_DB`. A process restart no longer discards an in-flight
interview solely because `MemorySaver` was in use.

Sessions, pending interrupts, jobs, voice outbox events, and per-session worker
leases are now read from the configured repository, so multiple API and worker
replicas can share them. Response submission no longer uses a process-global
lock; duplicate protection is the durable unique active-job index. Named
researcher accounts are optional, and sessions created by a named researcher are
visible only to that owner unless an admin requests them.

## Tests

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_durable_checkpointer.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_read_model.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_analysis_report.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_api_analysis_reports.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_api_researcher_dashboard.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_response_outcomes.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_timing.py -q
```
