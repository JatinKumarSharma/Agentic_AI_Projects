# Customer Voice Interviewer — portfolio case study

**One-line pitch:** An evidence-first voice research platform that turns adaptive LangGraph interviews into reports where every claim is traceable to the participant's exact words.

**Live demo:** pending deployment  
**Repository:** [adaptive-customer-voice-interviewer](https://github.com/JatinKumarSharma/Agentic_AI_Projects/tree/main/adaptive-customer-voice-interviewer)

---

## Problem

Customer interviews usually lose the connection between what a person said and what a report claims. Notes get paraphrased, evidence gets detached from its response, and follow-up questions are either rigid or generated without grounding.

## What I built

- A **LangGraph research engine** that plans, proposes, evaluates, decides, decomposes answers, updates state, and delivers questions.
- An **evidence contract** requiring `evidence_id`, `response_id`, and verbatim `source_quote` on report claims.
- A **voice-first participant flow** where the researcher preconfigures the study and the participant only opens a link and speaks.
- **Server-side VAD** so the browser does not need a local model or special hardware.
- A **two-layer conversational bridge** before every next question:
  - an immediate 1–3 word acknowledgement at speech end;
  - a warm, active reflection generated after decomposition while the graph prepares the next question.
- **Tokenless rotating demo invites**: public `/?demo=<slug>` links exchange a slug for a short-lived capability without exposing raw tokens in the address bar.
- **Consent-gated participation**: participants confirm recording, evidence use, access, retention, and deletion terms before the interview begins.
- **Durable worker infrastructure**: repository-backed queue, fencing tokens, leases, heartbeats, tenant isolation, and an ack-based voice outbox.
- **Hosted storage and observability**: PostgreSQL adapter, migrations, backups, least-privilege DB roles, structured logs, request IDs, and health checks.

## Hard problems solved

1. **Latency without losing quality**  
   A large local reasoning model rendered the first question in roughly six minutes on CPU. The model boundary was switched to a fast provider profile, bringing the first question to about 4–6 seconds while keeping storage and speech self-hosted.

2. **Natural timing is not a prompt tweak**  
   The immediate acknowledgement is model-free and emitted directly from the `vad.speech_end` event before transcript finalization. The reflection is generated separately after decomposition. Playback is serialized so the participant hears one clear voice at a time, while generation runs in parallel.

3. **No silent gaps**  
   The participant UI polls research status and the voice outbox together. It renders the next question only when research is complete, all engagement jobs are finished, and queued voice feedback has played.

4. **Abuse paths were designed down**  
   Participant capability tokens, link expiry, lifecycle guards, payload caps, rate limits, stream budgets, tenant stamping, and durable audit events close the paths that a public interview link exposes.

## Measured results

| Check | Result |
|---|---:|
| Offline test suite | **410 passed, 4 skipped** |
| First question with fast provider profile | **~4–6 seconds** |
| Response processing in successful live smoke | **~4 seconds** |
| Contextual engagement queued after submission | **~2.7 seconds** |
| Contextual engagement emitted | **~3.1 seconds** |
| Local provider-boundary test suite | offline, no network/GPU/mic required |

## Stack

Python, FastAPI, LangGraph, PostgreSQL, SQLite for offline tests, Docker Compose, Nginx, Silero VAD, faster-whisper, Piper TTS, Ollama/Groq adapter boundary, pytest.

## What I would add next

- Published retention period and deletion contact for the deployed study.
- Live dashboards for first-question latency, acknowledgement timing, and job failures.
- A rate-safe model tier or GPU-backed self-hosted model for predictable public traffic.
