# Agentic AI Projects

A monorepo for Jatin Sharma's agentic AI projects. Each project occupies its
own top-level folder so code, dependencies, deployment configuration and
evaluation evidence stay isolated.

## Projects

| Folder | Status | What it is |
|---|---|---|
| [`financial-research-assistant/`](financial-research-assistant/) | Live | Agentic financial research system with a themed web UI: https://financial-research-assistant-web.vercel.app. Compatible with Groq, OpenAI, Gemini and Ollama models. |
| [`adaptive-customer-voice-interviewer/`](adaptive-customer-voice-interviewer/) | Implemented | Evidence-first LangGraph voice research platform with server-side VAD, tenant isolation, durable background work, and audit-ready reports. |

## Conventions

- One project per top-level directory.
- Each project owns its own `requirements.txt`, `.env.example` and `README.md`.
- API keys never enter the repository. `.env` is ignored at every level.
- Evaluation runs and their raw traces live inside the owning project under
  `eval-runs/`, with a `EVAL_LOG.md` recording what changed and what moved.
- Evidence belongs with the system it evaluates — not in a separate report.

## Deployment compatibility

The root `main.py` and `requirements.txt` are thin deployment shims only. They
exist so the existing Render service — which builds from the repository root
and runs `uvicorn main:app` — keeps working after the restructure. All real
code and dependencies live in `financial-research-assistant/`. There is no
need to change Render's Root Directory setting.

Dependencies are pinned to the versions used for the recorded evaluation runs.
Leaving `openbb` unpinned resolves to 5.x, which installs the CLI, MCP server,
devtools and charting stacks and exceeds Render's 512 MiB free-tier memory at
startup. `render.yaml` records the exact build and start commands.

## Repository

https://github.com/JatinKumarSharma/Agentic_AI_Projects
