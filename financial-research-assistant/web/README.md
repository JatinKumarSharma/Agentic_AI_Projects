# Financial Research Assistant — web interface

A minimal chat interface for the agent. Built with Vite, React, TypeScript and
Tailwind CSS, using the portfolio palette with a slight cool tint.

The interface talks to the deployed assistant through a restricted CORS
allowlist. Browser origins are configured on the assistant with the
`FRONTEND_ORIGINS` environment variable (comma-separated); localhost `5173` is
allowed by default.

## Run locally

```bash
npm install
npm run dev
```

Open `http://localhost:5173`. To point at a different assistant, copy
`.env.example` to `.env` and set `VITE_API_URL`.

## Build

```bash
npm run build
npm run preview
```

The output is in `dist/`. No API keys are needed in the frontend — all provider
credentials stay on the assistant side.

## Deploy to Vercel

Import the repository into Vercel and set **Root Directory** to:

```text
financial-research-assistant/web
```

Vercel detects Vite automatically. No frontend environment variables are
required; the deployed assistant URL is the default.

After deployment, copy the Vercel domain and add it to the assistant's
`FRONTEND_ORIGINS` environment variable on Render, for example:

```text
FRONTEND_ORIGINS=https://your-ui.vercel.app
```

Use a comma-separated list for multiple domains. Never use `*`.

## Framing

This is an agentic AI research system, not a thin data endpoint. It works with
Groq, OpenAI, Google Gemini and Ollama models through a single configurable
layer, and the interface shows the active provider and model on load.
