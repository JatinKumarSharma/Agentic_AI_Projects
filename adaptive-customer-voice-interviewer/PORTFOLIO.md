# Customer Voice Interviewer — portfolio case study

**One-line pitch:** An evidence-first voice research platform that turns adaptive LangGraph interviews into reports where every claim is traceable to the participant's exact words.

**Demo link — Researcher Dashboard (demo):** https://cvi-portfolio.onrender.com/researcher<br>
**Participant demo link:** https://cvi-portfolio.onrender.com/?demo=portfolio-demo<br>
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
| Offline test suite | **435 passed, 4 skipped** |
| First question with fast provider profile | **~4–6 seconds** |
| Response processing in successful live smoke | **~4 seconds** |
| Contextual engagement queued after submission | **~2.7 seconds** |
| Contextual engagement emitted | **~3.1 seconds** |
| Local provider-boundary test suite | offline, no network/GPU/mic required |

## Stack

Python, FastAPI, LangGraph, PostgreSQL, SQLite for offline tests, Docker Compose, Nginx, Silero VAD, faster-whisper, Piper TTS, Ollama/Groq adapter boundary, pytest.

## Future scope

The items below are future scope, not claims about the current demo.

### Foundational platform scope

- **Production-grade deployment:** Move from the current free Render + Neon demo setup to the strict production target: VPS/custom domain deployment with Docker Compose, managed secrets, HTTPS, backups, monitoring, and production auth hardening.
- **Reliable, higher-accuracy voice:** Upgrade speech capabilities beyond free-tier constraints by using higher-RAM/paid hosting or self-hosted STT/TTS models for better transcript accuracy, lower latency, and always-on reliability, while preserving typed-input fallback.
- **Pluggable provider architecture:** Extend the existing client-replaceable boundaries for storage/memory, voice/speech, and LLM model/provider so deployments can swap demo/free providers (Groq, browser speech, Neon/Render) with self-hosted or enterprise-grade alternatives without changing the core evidence-first interview workflow.
- **Self-hosted/privacy-first profile:** Strengthen the strict self-hosted deployment profile for privacy-sensitive environments, keeping participant data, speech processing, and model inference within controlled infrastructure.

### Research measurement layer

- **Question-quality eval set:** consented, human-reviewed transcripts scored for follow-up relevance, redundancy and research progress.
- **Live timing dashboard:** first-question latency, acknowledgement timing, engagement emit time and failed-job counts in one view.
- **Researcher review workflow:** human confirmation of evidence claims plus published retention and deletion controls for each study.
- **Cross-session insight views:** aggregate themes across sessions with tenant isolation and source quotes preserved end to end.

### Longer-term direction: consumer and market intelligence

Evolve CVI from an adaptive AI interviewer into an autonomous consumer and market intelligence platform that connects what people say, what they do, and what the market reveals.

**CVI Intelligence Fusion Engine — evidence worlds**

- **Stated perception / consumer intelligence:** what people say, captured through first-party interviews.
- **Observed behavior / market & commercial signals:** what people do, captured only through authorized behavioral or commercial sources.
- **Public market sentiment / external signals:** what the market reveals through public, provenance-bearing sources.

Align products/variants, time periods, regions, and segments; detect trends; compare evidence; identify contradictions; and generate testable hypotheses.

**Key capabilities:** perception–behavior gap detection, unified market understanding, continuous market monitoring, historical intelligence, decision support.

**Data strategy:** first-party data foundation; authorized external data; public signal enrichment; clear boundaries against private marketplace data, login bypass, CAPTCHA bypass or access-control bypass.

**Trust guardrails:** preserve provenance; do not merge truths blindly; hypotheses not automatic conclusions; privacy and compliance; reliable product identity via GTIN/EAN/UPC/ASIN/SKU/MPN/model number.

**Phased roadmap:** single-product case study → continuous monitoring → authorized commercial integrations → fusion engine → commercial intelligence service.
