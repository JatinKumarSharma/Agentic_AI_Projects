"""Server-side VAD streaming endpoint.

The browser sends one PCM16 microphone stream. Turn boundaries are decided
here, not on the participant's device. ``speech_end`` and the completed audio
segment are emitted immediately; transcription runs in a worker thread so an
acknowledgement is never held up by the model.
"""

import asyncio
import base64
import json
import os
from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect

from .segmenter import SegmentationConfig, SpeechSegmenter
from .stt import STTEngine, create_stt_engine, pcm16_to_wav
from .vad import VADProvider, create_vad_provider


@dataclass(frozen=True)
class StreamSettings:
    language: str | None
    prompt: str | None
    emit_audio_segments: bool


def _settings_from_env() -> StreamSettings:
    enabled = os.getenv(
        "CVI_SPEECH_EMIT_AUDIO_SEGMENTS",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}

    return StreamSettings(
        language=os.getenv("CVI_LOCAL_STT_LANGUAGE", "").strip() or None,
        prompt=os.getenv("CVI_LOCAL_STT_PROMPT", "").strip() or None,
        emit_audio_segments=enabled,
    )


def create_stream_components() -> tuple[
    VADProvider,
    SpeechSegmenter,
    STTEngine,
]:
    """Single injection point so tests can replace all three boundaries."""

    return (
        create_vad_provider(),
        SpeechSegmenter(SegmentationConfig.from_env()),
        create_stt_engine(),
    )


async def stream_audio(websocket: WebSocket) -> None:
    await websocket.accept()

    vad, segmenter, engine = create_stream_components()
    settings = _settings_from_env()
    frame_bytes = vad.frame_samples * 2
    send_lock = asyncio.Lock()
    transcription_tasks: set[asyncio.Task] = set()
    pending = bytearray()
    utterance_idx = 0

    async def send(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    async def transcribe_and_emit(
        wav: bytes,
        idx: int,
        speech_ms: float,
    ) -> None:
        try:
            result = await asyncio.to_thread(
                engine.transcribe,
                wav,
                language=settings.language,
                prompt=settings.prompt,
            )
        except Exception as exc:
            await send({
                "type": "error",
                "message": (
                    "Transcription failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            })
            return

        await send({
            "type": "transcript_final",
            "utterance_idx": idx,
            "text": result.get("text", ""),
            "language": result.get("language"),
            "confidence": result.get("confidence"),
            "metadata": result.get("metadata") or {},
            "speech_ms": speech_ms,
        })

    async def handle_boundary(event) -> None:
        nonlocal utterance_idx

        if event.type == "speech_start":
            utterance_idx += 1
            await send({
                "type": "speech_start",
                "utterance_idx": utterance_idx,
            })
            return

        if event.type == "speech_discarded":
            await send({
                "type": "speech_discarded",
                "reason": event.reason,
                "speech_ms": event.speech_ms,
            })
            return

        if event.type != "speech_end":
            return

        idx = utterance_idx
        await send({
            "type": "speech_end",
            "utterance_idx": idx,
            "speech_ms": event.speech_ms,
            "total_ms": event.total_ms,
            "reason": event.reason,
        })

        wav = pcm16_to_wav(event.audio, vad.sample_rate)

        if settings.emit_audio_segments:
            await send({
                "type": "audio_segment",
                "utterance_idx": idx,
                "audio_base64": base64.b64encode(wav).decode("ascii"),
                "duration_ms": event.total_ms,
            })

        task = asyncio.create_task(
            transcribe_and_emit(wav, idx, event.speech_ms)
        )
        transcription_tasks.add(task)
        task.add_done_callback(transcription_tasks.discard)

    try:
        await send({
            "type": "stream_ready",
            "vad": vad.name,
            "sample_rate": vad.sample_rate,
            "frame_samples": vad.frame_samples,
        })

        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            payload = message.get("bytes")

            if payload:
                pending.extend(payload)

                while len(pending) >= frame_bytes:
                    frame = bytes(pending[:frame_bytes])
                    del pending[:frame_bytes]
                    is_speech = vad.is_speech(frame)

                    for event in segmenter.feed(is_speech, frame):
                        await handle_boundary(event)

                continue

            text = message.get("text")

            if not text:
                continue

            try:
                control = json.loads(text)
            except json.JSONDecodeError:
                await send({
                    "type": "warning",
                    "message": "Invalid control message.",
                })
                continue

            if not isinstance(control, dict):
                await send({
                    "type": "warning",
                    "message": "Invalid control message.",
                })
                continue

            if control.get("type") != "stop":
                await send({
                    "type": "warning",
                    "message": "Unsupported control message.",
                })
                continue

            flushed = segmenter.flush()

            if flushed is not None:
                await handle_boundary(flushed)

            if transcription_tasks:
                await asyncio.gather(
                    *list(transcription_tasks),
                    return_exceptions=True,
                )

            await send({"type": "stream_stopped"})
            break

    except WebSocketDisconnect:
        pass

    except Exception as exc:
        try:
            await send({
                "type": "error",
                "message": (
                    "Speech stream failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            })
        except Exception:
            pass

    finally:
        for task in transcription_tasks:
            if not task.done():
                task.cancel()

        if transcription_tasks:
            await asyncio.gather(
                *list(transcription_tasks),
                return_exceptions=True,
            )

        try:
            await websocket.close()
        except Exception:
            pass
