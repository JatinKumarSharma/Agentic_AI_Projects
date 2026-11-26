# Offline testing

Offline mode is for testing only. Hosted production uses
`compose.yaml + compose.prod.yaml` with PostgreSQL.

The test stack runs the research model in Ollama, speech-to-text in a local
faster-whisper service, and text-to-speech through the local Amy voice model
in the same speech service. No participant machine needs to run a model.

The first bootstrap needs network access to download model files. After the
files are cached in named volumes, the test stack runs without external API
calls.

## One-command model pull

```powershell
docker compose -f compose.yaml -f compose.local.yaml --profile offline run --rm pull-models
```

That single command:

1. waits for Ollama and the speech service,
2. pulls the configured Ollama research model (default `qwen2.5:7b-instruct`) into Ollama,
3. downloads the configured Amy voice model and config into `speech-models`, and
4. calls the speech service `/admin/warmup` so faster-whisper **and** the Silero
   VAD model are cached before any participant joins.

`docker compose ... --profile offline up -d` runs the same one-shot service
automatically before the API starts.

For a host-only run, install `deploy/speech/requirements.txt` into a virtual
environment, start both Ollama and the speech service yourself, then run:

```powershell
.\.venv\Scripts\python.exe -m scripts.pull_local_models
```

Environment overrides are documented in `.env.example`. The most useful ones
are `CVI_LOCAL_PULL_MODELS`, `LOCAL_LLM_MODEL`, `CVI_LOCAL_STT_MODEL`, `CVI_LOCAL_TTS_MODEL_URL`,
and `CVI_SPEECH_URL`.

### Using an already-installed host Ollama model

If the model is already present in host Ollama and downloading another copy is
impractical, use the host daemon instead of the Compose Ollama service:

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_HOST','0.0.0.0:11434','User')
```

Fully quit and restart the Ollama app after setting `OLLAMA_HOST`, then set:

```dotenv
CVI_OLLAMA_BASE_URL=http://host.docker.internal:11434
LOCAL_LLM_MODEL=<installed-model-tag>
CVI_EVALUATOR_MODEL=<installed-model-tag>
CVI_SKIP_OLLAMA_PULL=1
```

`CVI_SKIP_OLLAMA_PULL=1` keeps the bootstrap from calling `/api/pull`; it still
downloads and warms the local speech assets. The bundled `ollama` Compose
service remains available but is unused by the API and worker.

## Start and verify

Use the dev overlay to publish the API on port 8000:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml -f compose.local.yaml --profile offline up -d --build
```

Then work through this checklist.

### 1. Services are up

```powershell
docker compose -f compose.yaml -f compose.dev.yaml -f compose.local.yaml --profile offline ps
```

Expected services include `api`, `migrate`, `ollama`, `speech`, and a completed
`pull-models` container.

### 2. API is ready in local mode

```powershell
curl.exe http://127.0.0.1:8000/health/ready
```

Expected:

```json
{
  "status": "ok",
  "checks": {
    "database": "ok",
    "model_provider": "ollama"
  }
}
```

A `GROQ_API_KEY` error here means the offline overlay was not included in the
Compose command.

### 3. Models are present

```powershell
docker compose -f compose.yaml -f compose.local.yaml --profile offline exec ollama ollama list
docker compose -f compose.yaml -f compose.local.yaml --profile offline exec speech python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9000/health/ready').read().decode())"
```

Expected: the Ollama tag from `LOCAL_LLM_MODEL`, and a speech readiness payload
with `stt` and `tts` set to `configured` or `disabled` according to your
environment.

### 4. Offline unit tests pass

```powershell
docker compose -f compose.yaml -f compose.dev.yaml -f compose.local.yaml run --rm test
```

These tests mock every model and speech boundary. They do not need Ollama,
Groq, a microphone, or downloaded models.

### 5. Researcher and participant flows work

1. Open `http://127.0.0.1:8000/researcher` and sign in with user `researcher`
   and `RESEARCHER_ACCESS_CODE`.
2. Create an interview. The returned participant link is the only context the
   participant sees; they never enter company, product, audience, or goal
   details.
3. Open the participant link. Compatibility and microphone checks run
   automatically; `Recheck setup` is only a fallback.
4. Start the interview with the themed `Begin interview` button.
5. Confirm server-side VAD behavior: the browser opens one microphone stream,
   the UI shows `I'm listening to your answer...` only after real speech, a
   natural pause does not split the turn, and the next question is generated
   without a click. The acknowledgement plays at speech end, before
   transcription finishes.
6. If speech fails, `Stop speaking (backup)` and `Submit response (backup)`
   still work, and an empty transcript shows the typed fallback without a false
   acknowledgement.
7. In the invitation response, confirm the voice profile reports
   `stt_provider: local` and `tts_provider: browser`. No API key or endpoint is
   exposed to the participant.

### 6. Runtime is network-free

After the model pull, disconnect external network access and repeat steps 2 and
5. Interview creation, research generation, transcription, and browser speech
output should continue to work. The only external calls left are the browser's
own speech-synthesis engine and any DNS/browser behavior outside CVI.

## Local TTS (optional)

The offline overlay leaves browser TTS as primary because it preserves the
`en-IN` voice profile. To use Piper for playback instead:

```yaml
# compose.local.yaml, api.environment
CVI_VOICE_TTS_PROVIDER: local
CVI_VOICE_TTS_FALLBACKS: local,browser
```

The bundled Piper voice defaults to `en_US-amy-medium`; replace the
`CVI_LOCAL_TTS_MODEL_URL` and `CVI_LOCAL_TTS_CONFIG_URL` values in `.env.example`
with an Indian English or Hindi voice if one is available for your research
population. CVI keeps `en-IN` as the delivery locale while the local adapter
forwards the language hint to the speech service.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `/health/ready` reports `GROQ_API_KEY` | Include `compose.local.yaml` and enable the `offline` profile. |
| First question fails with an Ollama connection error | `docker compose ... logs ollama`; rerun `pull-models`. |
| STT always falls back to typed | Check `speech` logs and rerun `pull-models` to warm faster-whisper and Silero. |
| Background noise starts a turn | Raise `CVI_SPEECH_VAD_THRESHOLD` or increase `CVI_SPEECH_MIN_SPEECH_MS`. |
| Answers cut off at pauses | Increase `CVI_SPEECH_END_SILENCE_MS` (default 700 ms). |
| One answer splits into several turns | Increase `CVI_SPEECH_END_SILENCE_MS`; short silences are already merged by the segmenter. |
| Silero model cannot be downloaded temporarily | Set `CVI_SPEECH_VAD_PROVIDER=energy` for the adaptive-noise-floor fallback. |
| TTS request returns 503 | Confirm the Piper `.onnx` and `.onnx.json` files exist in `speech-models`; the browser fallback still works. |
| Participant sees `Speech unavailable` | Browser microphone permission is required; typed fallback remains available. |
| Read model and checkpoints survive restart, but pending in-process voice jobs do not | Expected in the offline/test overlay (`CVI_WORKER_ENABLED=0`): SQLite holds the read model and LangGraph checkpoints; response/engagement executor state is process-local. Hosted production uses `CVI_WORKER_ENABLED=1` and the durable queue. |

## Persistence

- `cvi-data` — SQLite read model, LangGraph checkpoints, reports, audit log.
- `ollama-models` — downloaded research model.
- `speech-models` — faster-whisper cache, Silero VAD model, and Piper voice files.

Backups cover `cvi-data`; model volumes are reproducible by rerunning
`pull-models`.
