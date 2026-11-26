# Render + Neon free demo deployment

This is the recommended public profile when there is **no custom domain** and
**no paid hosting**. It uses:

- a Render **free web service** with a public `https://<service>.onrender.com`
  URL;
- an external **Neon Free PostgreSQL** database, because Render's free database
  expires after 30 days;
- the rate-safe Groq key already provisioned for CVI;
- browser speech synthesis for playback;
- Groq transcription plus a lightweight server-side energy VAD bridge.

The Render container starts the database migrations, the durable worker, the
speech bridge, and the API in one process tree. Render has no free background
worker service and its filesystem is ephemeral, so all authoritative state must
stay in Neon.

**Do not paste secrets into chat, issues, screenshots, `render.yaml`, or any
committed file.** Enter them only in the Render dashboard.

## Free-tier expectations

- The service sleeps after about 15 minutes of inactivity; the first request
  after sleep can take about a minute.
- This is a portfolio demo, not a production SLA.
- Render free has about 0.1 CPU and 512 MB RAM, so the local Whisper/Piper/
  Silero model stack is deliberately not used here.
- Participant microphone audio is still streamed to the **server** for turn
  detection; the browser does not own transcription or VAD logic.
- Keep the demo pool small (5–10 sessions) so the Groq key stays rate-safe.
- Groq's free on-demand tier allows about 1000 output tokens per minute. A
  structured node can request about 750, so `CVI_LLM_MAX_RETRIES` is set to `2`
  in the blueprint; the Groq SDK then honours short `Retry-After` values. The
  SDK caps each wait at 60 seconds, which prevents multi-minute stalls.
  `CVI_LLM_MAX_OUTPUT_TOKENS` is lowered to `400` so a structured node does not
  consume most of that budget in one request. Self-hosted profiles keep the
  provider default because they do not share a token budget.

## 1. Create the Neon database

1. Sign up at Neon and create a free project.
2. Copy the PostgreSQL connection string. Prefer the pooled DSN if Neon offers
   one; if migrations fail under the pooler, use the direct DSN.
3. Keep `sslmode=require` in the DSN.
4. Store the DSN temporarily in a local secret manager or shell variable. Do not
   add it to Git.

Neon Free provides 1 GB of storage per project. Export or delete old research
data periodically; a free tier is not a backup contract.

## 2. Create the Render web service

1. In Render, choose **New → Blueprint**.
2. Use the GitHub repository and point the blueprint at
   `adaptive-customer-voice-interviewer/render.yaml`.
3. Select the merged branch that contains this file.
4. Render will prompt for these values because they are marked `sync: false`:

   | Variable | Value |
   |---|---|
   | `CVI_DATABASE_URL` | The Neon PostgreSQL DSN |
   | `GROQ_API_KEY` | The rate-safe Groq key |
   | `RESEARCHER_ACCESS_CODE` | A long random researcher password |

   Generate the researcher code locally with:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

5. Deploy. Render builds the `render` stage of `Dockerfile.render` and starts
   `python -m scripts.render_start`.
6. Wait for `/health/ready` to return HTTP 200. The first deploy and each cold
   start may take about a minute.

No custom domain is required. The demo URL is:

```text
https://<service>.onrender.com/?demo=portfolio-demo
```

## 3. Create the demo session pool

The public URL never contains a participant token. The tokenless broker needs
one or more unclaimed sessions with the same `demo_slug`. Create them from a
trusted machine using the researcher credential:

```powershell
$base = "https://<service>.onrender.com"
$auth = "researcher:$env:RESEARCHER_ACCESS_CODE"

1..6 | ForEach-Object {
  $body = @{
    company_id = "demo-company"
    company_name = "Demo Company"
    product_id = "demo-product"
    product_name = "Demo Product"
    product_category = "software"
    research_goal = "Understand first-run setup friction and purchase confidence"
    start_date = "2026-01-01"
    end_date = "2026-12-31"
    target_audience = "Small-team buyers evaluating the product"
    customer_segment = "New customers"
    interview_constraints = @()
    link_expiry = "none"
    demo_slug = "portfolio-demo"
  } | ConvertTo-Json

  Invoke-RestMethod -Method Post `
    -Uri "$base/sessions" `
    -Headers @{ Authorization = "Basic $([Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($auth)))" } `
    -ContentType "application/json" `
    -Body $body
}
```

The creation response includes a raw `participant_url`. **Never publish that
URL.** Publish only `?demo=portfolio-demo`. The public `POST
/demo/invites/portfolio-demo` endpoint rotates one short-lived capability into
the browser's session storage, and the frontend removes the capability from the
address bar.

## 4. Smoke test

1. Open `https://<service>.onrender.com/health/live`.
2. Open `https://<service>.onrender.com/health/ready`.
3. Open `https://<service>.onrender.com/?demo=portfolio-demo`.
4. Wait for the automatic compatibility and microphone checks. Click
   **Recheck setup** if you changed browser permissions.
5. Confirm that **Begin interview** stays disabled until consent is checked.
6. Check consent, then click **Begin interview** voluntarily.
7. Answer the first question by voice. Expect:
   - immediate acknowledgement after server-side speech end;
   - reflection after decomposition;
   - the next question only after both arrive.
8. Confirm typed fallback works when microphone permission is denied.
9. Sign in to the researcher dashboard with Basic auth and verify the report
   cites `evidence_id`, `response_id`, and verbatim `source_quote`.
10. Call `POST /demo/invites/portfolio-demo` again and confirm a different
    unclaimed session is issued.

## 5. Operational notes

- `/health/ready` verifies the configured database and provider settings.
- Structured JSON logs are written to stdout and correlated with `X-Request-ID`.
- `CVI_WORKER_ENABLED=1` keeps job processing durable in Neon. The worker and
  speech threads are per-container; do not scale the free service to multiple
  instances.
- The speech bridge uses `CVI_SPEECH_STT_ENGINE=groq` and
  `CVI_SPEECH_VAD_PROVIDER=energy`. Local Piper TTS and Silero/Whisper models
  are disabled on this profile.
- The strict VPS profile in `deployment.md` remains the full production target
  when a domain and paid hosting are available.

## Updating the portfolio link

Replace `Live demo: pending deployment` in `PORTFOLIO.md` with the public
tokenless URL:

```text
Live demo: https://<service>.onrender.com/?demo=portfolio-demo
```
