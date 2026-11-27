"""Local, paid-API-free STT/TTS service for CVI.

This service is intentionally outside the research package. It implements the
same plain HTTP contracts as the enterprise ``local`` adapters in
``agent/voice`` and imports the heavy model SDKs lazily, so the main offline
test suite never needs speech models, microphones, or network access.
"""

import io
import os
import wave
from email import policy
from email.parser import BytesParser
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from .stt import create_stt_engine
from .stream import stream_audio
from .vad import create_vad_provider


app = FastAPI(
    title="CVI Local Speech Service",
    version="0.1.0",
)


class SynthesizeRequest(BaseModel):
    text: str
    language: Optional[str] = None
    voice: Optional[str] = None
    rate: float = 1.0
    pitch: float = 1.0
    volume: float = 1.0
    format: str = "mp3"


_voice = None
_voice_model: Optional[str] = None


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_rate(value) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 1.0

    return max(0.5, min(1.5, rate))


def _bounded_volume(value) -> float:
    try:
        volume = float(value)
    except (TypeError, ValueError):
        return 1.0

    return max(0.0, min(1.0, volume))


def _language_code(language: Optional[str]) -> Optional[str]:
    if not language:
        return None

    code = language.strip().split("-")[0].split("_")[0].lower()

    return code or None


def stt_enabled() -> bool:
    return _env_bool("CVI_SPEECH_STT_ENABLED", True)


def tts_enabled() -> bool:
    return _env_bool("CVI_SPEECH_TTS_ENABLED", True)


def warm_transcriber() -> dict:
    if not stt_enabled():
        return {"status": "disabled"}

    return create_stt_engine().warm()


def warm_vad() -> dict:
    return create_vad_provider().warm()


def _get_voice():
    global _voice, _voice_model

    if not tts_enabled():
        raise RuntimeError("Local TTS is disabled.")

    model_path = os.getenv("CVI_LOCAL_TTS_MODEL", "").strip()

    if not model_path:
        raise RuntimeError("CVI_LOCAL_TTS_MODEL is not configured.")

    if not os.path.exists(model_path):
        raise RuntimeError(f"Piper voice not found: {model_path}")

    if _voice is None or _voice_model != model_path:
        from piper import PiperVoice

        _voice = PiperVoice.load(
            model_path,
            use_cuda=_env_bool("CVI_LOCAL_TTS_USE_CUDA", False),
        )
        _voice_model = model_path

    return _voice


def warm_voice() -> dict:
    if not tts_enabled():
        return {"status": "disabled"}

    model_path = os.getenv("CVI_LOCAL_TTS_MODEL", "").strip()

    if not model_path:
        return {"status": "not_configured"}

    _get_voice()

    return {"status": "ready", "model": os.path.basename(model_path)}


def readiness_status() -> dict[str, str]:
    stt = "disabled"

    if stt_enabled() and os.getenv("CVI_LOCAL_STT_MODEL", "small").strip():
        stt = "configured"

    tts = "disabled"

    if tts_enabled() and os.getenv("CVI_LOCAL_TTS_MODEL", "").strip():
        tts = "configured"

    return {
        "stt": stt,
        "tts": tts,
        "vad": "configured",
        "stt_engine": os.getenv("CVI_SPEECH_STT_ENGINE", "faster-whisper").strip(),
    }


def _parse_multipart(
    content_type: str,
    body: bytes,
) -> dict[str, str | bytes]:
    """Parse the small STT multipart body without a third-party dependency."""

    if "boundary=" not in content_type:
        raise HTTPException(
            status_code=400,
            detail="Audio payload must be multipart form data.",
        )

    raw = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode(
            "utf-8"
        )
        + body
    )
    message = BytesParser(policy=policy.default).parsebytes(raw)
    fields: dict[str, str | bytes] = {}

    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")

        if not name:
            continue

        payload = part.get_payload(decode=True) or b""

        if part.get_filename():
            fields[name] = payload
        else:
            fields[name] = payload.decode("utf-8", "replace")

    return fields


def _validate_wav(audio: bytes) -> None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            if wav_file.getnframes() <= 0:
                raise ValueError("empty WAV")
    except (EOFError, ValueError, wave.Error) as exc:
        raise HTTPException(
            status_code=400,
            detail="Audio payload must be a non-empty WAV file.",
        ) from exc


def transcribe_audio(
    audio: bytes,
    *,
    language: Optional[str],
    prompt: Optional[str],
) -> dict:
    return create_stt_engine().transcribe(
        audio,
        language=language,
        prompt=prompt,
    )


def synthesize_audio(payload: SynthesizeRequest) -> tuple[bytes, str]:
    from piper import SynthesisConfig

    voice = _get_voice()
    synthesis_config = SynthesisConfig(
        volume=_bounded_volume(payload.volume),
        length_scale=1.0 / _bounded_rate(payload.rate),
        normalize_audio=_env_bool("CVI_LOCAL_TTS_NORMALIZE", False),
    )
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(
            payload.text,
            wav_file,
            syn_config=synthesis_config,
        )

    return buffer.getvalue(), "audio/wav"


@app.get("/health/live")
def live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict:
    return {"status": "ok", "checks": readiness_status()}


@app.post("/admin/warmup")
def warmup() -> dict:
    try:
        return {
            "stt": warm_transcriber(),
            "tts": warm_voice(),
            "vad": warm_vad(),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Warmup failed: {type(exc).__name__}: {exc}",
        ) from exc


@app.post("/transcribe")
async def transcribe(request: Request) -> dict:
    fields = _parse_multipart(
        request.headers.get("content-type", ""),
        await request.body(),
    )
    audio = fields.get("file")

    if not isinstance(audio, bytes):
        raise HTTPException(
            status_code=400,
            detail="Audio payload must include a file field.",
        )

    _validate_wav(audio)

    try:
        return transcribe_audio(
            audio,
            language=fields.get("language") or None,
            prompt=fields.get("prompt") or None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Transcription failed: {type(exc).__name__}: {exc}",
        ) from exc


@app.websocket("/stream")
async def speech_stream(websocket: WebSocket):
    await stream_audio(websocket)


@app.post("/synthesize")
def synthesize(payload: SynthesizeRequest):
    text = payload.text.strip()

    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty.")

    try:
        audio_bytes, content_type = synthesize_audio(
            SynthesizeRequest(**{**payload.model_dump(), "text": text})
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Synthesis failed: {type(exc).__name__}: {exc}",
        ) from exc

    return Response(content=audio_bytes, media_type=content_type)
