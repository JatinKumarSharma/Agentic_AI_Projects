"""One-command bootstrap for the paid-API-free local model stack.

The script only talks to the model boundaries documented in
``docs/offline.md``:

* Ollama's ``/api/pull`` endpoint for the research model.
* Plain HTTPS downloads for the Piper voice files used by the local speech
  service.
* The speech service's ``/admin/warmup`` endpoint so faster-whisper fetches its
  model before the participant link is used.

No model SDK is imported here. Every network call goes through ``urllib`` so
unit tests can replace the boundary without network access.
"""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_LOCAL_LLM_MODEL = "qwen2.5:7b-instruct"
DEFAULT_SPEECH_BASE_URL = "http://localhost:9000"
DEFAULT_MODEL_DIR = "data/models"
DEFAULT_TTS_VOICE = "en_US-amy-medium"

PIPER_VOICE_BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    "/en/en_US/amy/medium"
)
DEFAULT_TTS_MODEL_URL = (
    f"{PIPER_VOICE_BASE_URL}/{DEFAULT_TTS_VOICE}.onnx?download=1"
)
DEFAULT_TTS_CONFIG_URL = (
    f"{PIPER_VOICE_BASE_URL}/{DEFAULT_TTS_VOICE}.onnx.json?download=1"
)


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")

    return [
        item.strip()
        for item in raw.split(",")
        if item.strip()
    ]


def _base_url(raw: str | None, default: str) -> str:
    return (raw or default).strip().rstrip("/") or default


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def ollama_model_names() -> list[str]:
    """Return unique local LLM tags to warm in Ollama."""

    configured = _env_list("CVI_LOCAL_PULL_MODELS")

    if configured:
        return list(dict.fromkeys(configured))

    names = [
        (
            os.getenv("LOCAL_LLM_MODEL", DEFAULT_LOCAL_LLM_MODEL).strip()
            or DEFAULT_LOCAL_LLM_MODEL
        )
    ]

    evaluator = os.getenv("CVI_EVALUATOR_MODEL", "").strip()

    # Groq-style identifiers contain a slash. They are not Ollama tags and
    # must not be pulled into the local store.
    if evaluator and "/" not in evaluator:
        names.append(evaluator)

    return list(dict.fromkeys(names))


def ollama_pull_url(base_url: str | None = None) -> str:
    return _base_url(base_url, DEFAULT_OLLAMA_BASE_URL) + "/api/pull"


def pull_ollama_model(
    model: str,
    *,
    base_url: str | None = None,
    opener=urlopen,
    timeout: float = 1800.0,
) -> dict:
    """Ask Ollama to download one model and return its final status."""

    payload = json.dumps({"name": model, "stream": False}).encode("utf-8")
    request = Request(
        ollama_pull_url(base_url),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with opener(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()[:300]
        raise RuntimeError(
            f"Ollama pull failed for {model}: HTTP {exc.code} "
            f"{detail or exc.reason}"
        ) from exc
    except (URLError, OSError, ValueError) as exc:
        raise RuntimeError(
            f"Ollama pull failed for {model}: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError(f"Ollama returned an unexpected payload for {model}.")

    error = result.get("error")

    if error:
        raise RuntimeError(f"Ollama pull failed for {model}: {error}")

    return result


def download_model(
    url: str,
    destination: str | Path,
    *,
    opener=urlopen,
    force: bool = False,
    timeout: float = 900.0,
) -> str:
    """Stream one model file into place and return ``downloaded`` or ``cached``."""

    path = Path(destination)

    if path.exists() and path.stat().st_size > 0 and not force:
        return "cached"

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    request = Request(url, method="GET")

    try:
        with opener(request, timeout=timeout) as response, open(
            partial, "wb"
        ) as output:
            while True:
                chunk = response.read(65536)

                if not chunk:
                    break

                output.write(chunk)
    except (HTTPError, URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"Model download failed for {url}: {type(exc).__name__}: {exc}"
        ) from exc

    if partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Model download returned no data for {url}.")

    partial.replace(path)

    return "downloaded"


def warm_speech_url(base_url: str | None = None) -> str:
    return _base_url(base_url, DEFAULT_SPEECH_BASE_URL) + "/admin/warmup"


def warm_speech_service(
    *,
    base_url: str | None = None,
    opener=urlopen,
    timeout: float = 900.0,
) -> dict:
    """Trigger local STT/TTS model loading through the speech service."""

    request = Request(
        warm_speech_url(base_url),
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with opener(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()[:300]
        raise RuntimeError(
            f"Speech warmup failed: HTTP {exc.code} "
            f"{detail or exc.reason}"
        ) from exc
    except (URLError, OSError, ValueError) as exc:
        raise RuntimeError(
            f"Speech warmup failed: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError("Speech service returned an unexpected payload.")

    return result


def local_speech_downloads() -> list[tuple[str, Path]]:
    """Return the configured Piper voice URL/path pairs."""

    model_dir = Path(
        os.getenv("CVI_LOCAL_MODEL_DIR", DEFAULT_MODEL_DIR)
    )
    model_path = Path(
        os.getenv("CVI_LOCAL_TTS_MODEL")
        or model_dir / "tts" / f"{DEFAULT_TTS_VOICE}.onnx"
    )
    config_path = Path(
        os.getenv("CVI_LOCAL_TTS_CONFIG")
        or model_path.with_suffix(".onnx.json")
    )

    return [
        (
            os.getenv("CVI_LOCAL_TTS_MODEL_URL", DEFAULT_TTS_MODEL_URL),
            model_path,
        ),
        (
            os.getenv("CVI_LOCAL_TTS_CONFIG_URL", DEFAULT_TTS_CONFIG_URL),
            config_path,
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-ollama",
        action="store_true",
        help="Do not pull Ollama models.",
    )
    parser.add_argument(
        "--skip-speech",
        action="store_true",
        help="Do not download speech model files or warm the speech service.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download model files even when they already exist.",
    )
    args = parser.parse_args(argv)

    skip_ollama = args.skip_ollama or _env_enabled("CVI_SKIP_OLLAMA_PULL")

    if not skip_ollama:
        base_url = os.getenv("CVI_OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)

        for model in ollama_model_names():
            print(f"Pulling Ollama model: {model}")
            pull_ollama_model(model, base_url=base_url)

    if not args.skip_speech:
        for url, destination in local_speech_downloads():
            outcome = download_model(
                url,
                destination,
                force=args.force,
            )
            print(f"Speech model {outcome}: {destination}")

        speech_url = os.getenv("CVI_SPEECH_URL")

        if speech_url:
            # Speech models load lazily on first use, so a slow download must
            # not fail the bootstrap and block the API's depends_on condition.
            try:
                status = warm_speech_service(base_url=speech_url)
            except RuntimeError as exc:
                print(f"Warning: speech warmup deferred to first use: {exc}")
            else:
                print(f"Speech service warmed: {status}")
        else:
            print(
                "Speech service warmup skipped; "
                "set CVI_SPEECH_URL to warm it automatically."
            )

    print("Local model bootstrap complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
