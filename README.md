# Agentic AI Projects

A monorepo for Jatin Sharma's agentic AI projects. Each project occupies its
own top-level folder so code, dependencies, deployment configuration and
evaluation evidence stay isolated.

## Projects

| Folder | Status | What it is |
|---|---|---|
| [`financial-research-assistant/`](financial-research-assistant/) | Live | Tool-calling financial research agent. FastAPI + LangChain + OpenBB, configurable LLM provider. |
| [`adaptive-customer-voice-interviewer/`](adaptive-customer-voice-interviewer/) | Reserved | Adaptive customer voice interview agent. Space reserved; code to be added. |

## Conventions

- One project per top-level directory.
- Each project owns its own `requirements.txt`, `.env.example` and `README.md`.
- API keys never enter the repository. `.env` is ignored at every level.
- Evaluation runs and their raw traces live inside the owning project under
  `eval-runs/`, with a `EVAL_LOG.md` recording what changed and what moved.
- Evidence belongs with the system it evaluates — not in a separate report.

## Repository

https://github.com/JatinKumarSharma/Agentic_AI_Projects
