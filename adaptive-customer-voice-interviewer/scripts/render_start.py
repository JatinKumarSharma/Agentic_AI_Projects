"""Single-process entry point for the free CVI demo on Render.

Render's free web service has no separate worker plan, no persistent disk, and
an ephemeral filesystem. This entry point keeps the API, durable worker, and a
small speech bridge in one container while leaving durable state in the
configured PostgreSQL database.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any


def configure_runtime_environment() -> None:
    """Apply free-platform defaults without overriding dashboard secrets."""

    speech_port = os.getenv("CVI_SPEECH_BIND_PORT", "9000").strip() or "9000"

    defaults = {
        "CVI_SPEECH_BIND_HOST": "127.0.0.1",
        "CVI_SPEECH_BIND_PORT": speech_port,
        "CVI_SPEECH_URL": f"http://127.0.0.1:{speech_port}",
        "CVI_SPEECH_STREAM_URL": f"ws://127.0.0.1:{speech_port}/stream",
        "CVI_LOCAL_STT_URL": f"http://127.0.0.1:{speech_port}/transcribe",
        "CVI_SPEECH_STT_ENABLED": "true",
        "CVI_SPEECH_STT_ENGINE": "faster-whisper",
        "CVI_LOCAL_STT_MODEL": "tiny.en",
        "CVI_LOCAL_STT_DEVICE": "cpu",
        "CVI_LOCAL_STT_COMPUTE_TYPE": "int8",
        "CVI_LOCAL_STT_CPU_THREADS": "1",
        "CVI_LOCAL_STT_NUM_WORKERS": "1",
        "CVI_LOCAL_STT_BEAM_SIZE": "1",
        "CVI_LOCAL_STT_VAD_FILTER": "false",
        "CVI_LOCAL_STT_WITHOUT_TIMESTAMPS": "true",
        "CVI_LOCAL_STT_CONDITION_ON_PREVIOUS_TEXT": "false",
        "CVI_LOCAL_STT_MAX_AUDIO_SECONDS": "12",
        "CVI_SPEECH_TTS_ENABLED": "false",
        "CVI_SPEECH_VAD_PROVIDER": "energy",
        "CVI_SPEECH_END_SILENCE_MS": "900",
        "CVI_SPEECH_MIN_SEGMENT_MS": "500",
        "CVI_SPEECH_VAD_THRESHOLD": "250",
        "CVI_SPEECH_EMIT_AUDIO_SEGMENTS": "false",
        "CVI_VOICE_STT_PROVIDER": "browser_speech",
        "CVI_VOICE_TTS_PROVIDER": "browser",
        "CVI_LLM_PROVIDER": "groq",
        "CVI_LOG_FORMAT": "json",
        "CVI_LOG_LEVEL": "info",
    }

    for name, value in defaults.items():
        os.environ.setdefault(name, value)

    # The API must enqueue durable jobs instead of using in-process executors.
    os.environ["CVI_WORKER_ENABLED"] = "1"


def _logger():
    from agent.observability import configure_logging, get_logger

    configure_logging()
    return get_logger(__name__)


def run_migrations() -> None:
    """Apply the schema before the API module configures its repository."""

    from scripts.migrate import run_migrations as apply_migrations

    # Do not log the return value: for PostgreSQL it is the full DSN.
    apply_migrations()


def start_speech_service(logger) -> threading.Thread:
    """Run the lightweight server-side VAD/STT speech bridge in a daemon thread."""

    import uvicorn

    host = os.getenv("CVI_SPEECH_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("CVI_SPEECH_BIND_PORT", "9000"))

    config = uvicorn.Config(
        "deploy.speech.app:app",
        host=host,
        port=port,
        log_level=os.getenv("CVI_LOG_LEVEL", "info").lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def serve() -> None:
        try:
            server.run()
        except Exception:
            logger.exception("render_speech_service_failed")

    thread = threading.Thread(
        target=serve,
        name="cvi-speech",
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if server.started:
            logger.info(
                "render_speech_service_ready",
                extra={"host": host, "port": port},
            )
            return thread

        if not thread.is_alive():
            raise RuntimeError("Speech service exited before becoming ready.")

        time.sleep(0.05)

    raise RuntimeError("Speech service did not become ready within 15 seconds.")


def start_worker(api, logger) -> threading.Thread:
    """Run the durable queue in the same process as the free web service."""

    from agent.jobs import service as job_service
    from scripts.worker import register_handlers

    api.WORKER_ENABLED = True
    register_handlers()

    worker = job_service.worker_from_environment(api.repository)

    def work() -> None:
        try:
            worker.run_forever()
        except Exception:
            logger.exception("render_worker_failed")

    thread = threading.Thread(
        target=work,
        name="cvi-worker",
        daemon=True,
    )
    thread.start()

    logger.info(
        "render_worker_started",
        extra={"worker_id": worker.worker_id},
    )
    return thread


def main() -> None:
    configure_runtime_environment()
    logger = _logger()

    logger.info("render_startup_begin")
    run_migrations()
    logger.info("render_migrations_complete")

    start_speech_service(logger)

    import api

    start_worker(api, logger)

    import uvicorn

    logger.info("render_api_starting")
    uvicorn.run(
        api.app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
