# Participant invitation links

A participant link is
`/?session_id=<session-id>&participant_token=<capability>`. The link is created
by `POST /sessions`, which returns both `session_id` and `participant_url`.

The `participant_token` is a separate high-entropy capability. The session UUID
is an identifier only; it is not enough to start or interact with an interview.
Only a SHA-256 digest (`participant_token_hash`) is stored with the session.
The raw token appears only in the invite link returned to the researcher.

The participant UI reads the token from the link and sends it as
`X-CVI-Participant-Token` on HTTP requests and as a `participant_token` query
parameter on voice websockets (browser websockets cannot set custom headers).
Sessions created before this capability existed remain usable with the UUID
alone, so an in-progress interview is not broken by a deployment upgrade.

Until link expiry was added, an interview ID worked indefinitely (subject to
the research campaign's `start_date`/`end_date`, which are calendar dates
checked when the interview starts). Links can now carry an exact deadline.

## Setting an expiry

Pass `link_expiry` in the `POST /sessions` body:

```json
{
  "company_id": "acme",
  "company_name": "Acme Technologies",
  "product_id": "insight-kit",
  "product_name": "Insight Kit",
  "product_category": "Analytics",
  "research_goal": "Understand setup friction.",
  "link_expiry": "3h"
}
```

Accepted values:

| Value | Meaning |
| --- | --- |
| omitted, `null`, `""`, `"none"` | No expiry — link works until the campaign `end_date` |
| `"1h"`, `"3h"`, `"1d"` | Preset durations measured from link creation |
| `"1 hour"`, `"3 hours"`, `"1 day"` | Friendly aliases for the same presets |
| Timezone-aware ISO-8601 datetime | Exact deadline, e.g. `"2026-11-22T15:00:00+05:30"` |

A custom datetime **must include a timezone offset** (or a `Z` suffix). Naive
timestamps are rejected with HTTP 422 because their meaning would otherwise
depend on the server's timezone. They are normalized to UTC and stored in
`link_expires_at`, so a researcher can set a deadline in the participant's
local time regardless of where the server runs.

The create response echoes `link_expiry` (normalized label) and
`link_expires_at` (UTC ISO-8601).

## Enforcement

- Every participant surface — invitation, start, advance, response submission,
  status, timing, voice transcription/synthesis, voice events, voice-event
  acknowledgement, and both voice websockets — requires the participant
  capability. A missing or invalid token returns HTTP 401/WS 4401 with
  `A valid participant link is required. Please open the interview from your
  invitation link.`
- The same surfaces all check the link deadline. An expired link returns HTTP
  410/WS 4410 with:
  `This interview link has expired. Please ask for a fresh interview link.`
  The participant UI renders this message.
- Participant traffic is also rate limited per session and per client IP. An
  excess HTTP request returns 429 with
  `Too many requests for this interview. Please slow down.` (or the network
  variant). An excess voice websocket closes with 4429.
- `GET /sessions/{id}/voice-events` is non-destructive. Events carry an
  `event_id` and stay available until the participant calls
  `POST /sessions/{id}/voice-events/ack` with `{"event_ids": [...]}`. The UI
  deduplicates by `event_id` and acknowledges only after handling an event, so
  a failed acknowledgement can be retried on the next poll.
- The deadline applies to **new interviews only**. Once
  `POST /sessions/{id}/start` succeeds, `interview_started_at` is recorded and
  the participant can reload the link or finish the interview after the
  deadline without being blocked.
- Each session also has an explicit `interview_status`: `created`, `starting`,
  `in_progress`, `completed`, or `failed`. `POST /sessions/{id}/start` is a
  one-time transition that is only accepted from `created`. The claim is
  persisted before the first graph call; `in_progress`, `completed`, and
  `failed` sessions return HTTP 409 instead of reinitializing the same
  LangGraph thread. Legacy sessions without `interview_status` are normalized
  from `interview_started_at` and campaign status.
- Rejected opens are recorded as a `participant_link_expired` event in the
  read model for auditability.
- Campaign `start_date`/`end_date` remain a separate, independent boundary
  enforced by `agent/session_validator.py` when the interview begins.

To give a participant more time, create a fresh session with a later deadline;
the storage layer keeps the old session and its evidence intact.
