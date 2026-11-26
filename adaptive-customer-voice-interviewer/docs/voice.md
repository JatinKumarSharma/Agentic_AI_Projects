# Voice architecture

CVI treats voice as a replaceable transport around the research engine. The
LangGraph interviewer never imports a speech SDK and never decides which
provider should be used.

## Delivery profile

`agent/voice/voice_profile.py` builds a provider-neutral profile from
environment variables. The default profile is **English with an Indian
cadence** (`en-IN`) and a **gentle** delivery.

| Setting | Default | Purpose |
| --- | --- | --- |
| `CVI_VOICE_LANGUAGE` | `en-IN` | STT hint and browser TTS locale |
| `CVI_VOICE_SOFTNESS` | `gentle` | `neutral`, `gentle`, or `soft` |
| `CVI_VOICE_RATE` | `1.0` | Global speech-rate multiplier |
| `CVI_VOICE_PITCH` | `1.0` | Global pitch multiplier |
| `CVI_VOICE_VOLUME` | `1.0` | Global volume multiplier |
| `CVI_VOICE_STT_PROVIDER` | `local` | Primary STT provider in hosted production |
| `CVI_VOICE_STT_FALLBACKS` | `local,typed` | Ordered STT fallback chain |
| `CVI_VOICE_TTS_PROVIDER` | `local` | Primary TTS provider in hosted production |
| `CVI_VOICE_TTS_FALLBACKS` | `local,browser` | Ordered TTS fallback chain |
| `CVI_VOICE_TTS_VOICE` | unset | Optional named voice |

Softness adjusts delivery by event kind:

- Questions: slower, clear delivery.
- Acknowledgements: slightly quicker and lighter.
- Reflections: calm, lower-volume delivery.
- Recovery prompts: slower and deliberate.

Every outbound voice event carries a `delivery` block with `kind`, `language`,
`provider`, `voice`, `rate`, `pitch`, and `volume`. The participant UI reads
this block instead of hardcoding playback behavior.

## Speech to text

`agent/voice/stt_providers.py` defines a small provider contract:

```python
provider.is_available() -> bool
provider.transcribe(audio, *, filename, language, prompt) -> STTResult
```

Implemented adapters:

- **`groq`** — Groq Whisper transcription through a free-tier-compatible
  OpenAI-style endpoint. Uses `GROQ_API_KEY` and `GROQ_STT_MODEL` (default
  `whisper-large-v3-turbo`).
- **`local`** — Any self-hosted STT service. Set `CVI_LOCAL_STT_URL` and
  optionally `CVI_LOCAL_STT_API_KEY`. CVI posts multipart form data with a WAV
  file plus optional `language` and `prompt` fields. The service should return
  JSON containing `text` or `transcript`.
- **`typed`** — Terminal fallback. Returns an empty transcript so the
  participant can type the answer.

`transcribe_with_fallback()` walks the configured chain, skips unavailable
providers, records failure reasons, and returns the first successful result.

### Browser flow

There are three capture paths:

| STT provider | Transport | Turn detection |
| --- | --- | --- |
| `local` | `WS /sessions/{session_id}/voice/local` | **Server-side Silero VAD** in the speech service |
| `sarvam` | `WS /sessions/{session_id}/voice` | Sarvam realtime VAD (requires `SARVAM_API_KEY`) |
| `groq`, `typed` | `POST /sessions/{session_id}/voice/transcribe` | Browser energy detector (batch fallback) |

For `local`, the browser opens **one** PCM16 16 kHz microphone stream after
question playback and forwards frames to the speech service. The service runs
Silero VAD behind `deploy/speech/vad.py`'s `VADProvider` boundary and a
`SpeechSegmenter` that applies minimum speech duration, hangover, maximum
segment duration, and a short cooldown so fragmented speech stays in one turn.
It emits `speech_start`, `speech_end`, and the completed audio segment
immediately; transcription runs in a worker thread. The API bridge sends the
model-free acknowledgement on `speech_end` and schedules answer decomposition
only after `transcript_final`, so acknowledgement latency is independent of
model speed.

For the batch path, the browser captures mono PCM16 at 16 kHz through the
audio worklet, buffers the samples, encodes a WAV file, and posts it to
`POST /sessions/{session_id}/voice/transcribe`. Automatic capture there uses a
client-side energy detector as a fallback for devices without the local speech
service. The `Start speaking (backup)` and `Submit response (backup)` controls
remain available on all paths.

The batch endpoint returns the transcript, provider metadata, failure history,
and a `failure_detail` string. A model-free immediate acknowledgement event is
returned only when a non-empty transcript is available. If every provider
fails, the UI shows the typed fallback without playing an acknowledgement that
would imply the response was accepted.

## Text to speech

`agent/voice/tts_providers.py` defines the matching output contract:

```python
provider.is_available() -> bool
provider.synthesize(text, delivery) -> TTSResult
```

Implemented adapters:

- **`browser`** — Zero-dependency Web Speech playback. It returns playback
  metadata instead of audio bytes and is the default so participant links work
  without cloud credentials.
- **`groq`** — Cloud audio through an OpenAI-compatible speech endpoint. Uses
  `GROQ_API_KEY`, `GROQ_TTS_MODEL`, and `GROQ_TTS_VOICE`.
- **`local`** — Any self-hosted neural TTS service. Set `CVI_LOCAL_TTS_URL`
  and optionally `CVI_LOCAL_TTS_API_KEY`. CVI posts JSON containing `text`,
  `language`, `voice`, `rate`, `pitch`, `volume`, and `format`. The service
  should return audio bytes.

Cloud and local providers are exposed to the browser through:

```text
POST /sessions/{session_id}/voice/synthesize
```

If the selected provider returns browser metadata, the endpoint returns JSON
and the browser uses `speechSynthesis`. If it returns audio, the browser plays
the returned blob through an `Audio` element. Any failure falls back to
browser playback, and browser playback failure still leaves the text visible.

## Participant-safe configuration

The public invitation endpoint includes the voice profile:

```text
GET /sessions/{session_id}/invitation
```

It exposes only language, softness, provider names, fallback names, voice name,
and prosody. API keys, endpoints, and authorization values are never returned.

## Local and enterprise integration

A bundled `speech` service under `deploy/speech/` implements both HTTP contracts
with faster-whisper and the installed Amy voice model, plus a `WS /stream`
endpoint for server-side VAD. It runs on the hosted production stack and is
selected by `compose.local.yaml` for tests. See `docs/offline.md` for the test
model-pull checklist.

The VAD engine is provider-neutral: `VADProvider` has `is_speech(frame)` and
`warm()`. `silero` is the default local neural engine; `energy` is a
dependency-free fallback that adapts to the room noise floor. Segmentation
tuning lives in `CVI_SPEECH_MIN_SPEECH_MS`, `CVI_SPEECH_END_SILENCE_MS`,
`CVI_SPEECH_MAX_SEGMENT_MS`, `CVI_SPEECH_MIN_SEGMENT_MS`,
`CVI_SPEECH_COOLDOWN_MS`, and `CVI_SPEECH_TAIL_SILENCE_MS`. Replacing Silero with
another detector requires one new provider class and no interview-engine
changes.

To connect an enterprise stack without changing application code:

```env
CVI_VOICE_STT_PROVIDER=local
CVI_VOICE_STT_FALLBACKS=local,typed
CVI_LOCAL_STT_URL=https://stt.internal.example/transcribe
CVI_LOCAL_STT_API_KEY=replace_me

CVI_VOICE_TTS_PROVIDER=local
CVI_VOICE_TTS_FALLBACKS=local,browser
CVI_LOCAL_TTS_URL=https://tts.internal.example/synthesize
CVI_LOCAL_TTS_API_KEY=replace_me
```

Both local adapters are plain HTTP contracts, so they can also sit in front of
WhisperX, Kokoro, or a commercial appliance. The local speech container returns
WAV audio; the browser plays it through the same `Audio` path as cloud audio.

## Testing

Voice provider tests mock the HTTP boundary. They do not require Groq,
Sarvam, Ollama, microphones, or speech pipelines:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_voice_profile.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_voice_providers.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_api_voice_transcription.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_api_voice_synthesis.py -q
```
