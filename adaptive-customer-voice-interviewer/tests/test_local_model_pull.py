import io
import json

from scripts import pull_local_models


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_local_model_names_dedupe_and_ignore_cloud_evaluator(monkeypatch):
    monkeypatch.delenv("CVI_LOCAL_PULL_MODELS", raising=False)
    monkeypatch.setenv("LOCAL_LLM_MODEL", "llama3.2:3b")
    monkeypatch.setenv("CVI_EVALUATOR_MODEL", "qwen/qwen3.8-27b")

    assert pull_local_models.ollama_model_names() == ["llama3.2:3b"]

    monkeypatch.setenv("CVI_EVALUATOR_MODEL", "llama3.2:1b")
    assert pull_local_models.ollama_model_names() == [
        "llama3.2:3b",
        "llama3.2:1b",
    ]

    monkeypatch.setenv(
        "CVI_LOCAL_PULL_MODELS",
        "qwen2.5:7b, llama3.2:3b ,qwen2.5:7b",
    )
    assert pull_local_models.ollama_model_names() == [
        "qwen2.5:7b",
        "llama3.2:3b",
    ]


def test_pull_ollama_model_posts_expected_payload():
    captured = {}

    def opener(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"status": "success"}).encode())

    result = pull_local_models.pull_ollama_model(
        "llama3.2:3b",
        base_url="http://ollama:11434/",
        opener=opener,
        timeout=123,
    )

    assert result["status"] == "success"
    assert captured["url"] == "http://ollama:11434/api/pull"
    assert captured["method"] == "POST"
    assert captured["body"] == {"name": "llama3.2:3b", "stream": False}
    assert captured["timeout"] == 123


def test_download_model_writes_atomically_and_skips_cached_file(tmp_path):
    destination = tmp_path / "tts" / "voice.onnx"
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(b"model-bytes")

    assert pull_local_models.download_model(
        "https://example.test/voice.onnx",
        destination,
        opener=opener,
    ) == "downloaded"
    assert destination.read_bytes() == b"model-bytes"
    assert not destination.with_suffix(".onnx.part").exists()

    assert pull_local_models.download_model(
        "https://example.test/voice.onnx",
        destination,
        opener=opener,
    ) == "cached"
    assert calls == ["https://example.test/voice.onnx"]


def test_warm_speech_service_posts_warmup():
    captured = {}

    def opener(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        return FakeResponse(
            json.dumps({"stt": "ready", "tts": "ready"}).encode()
        )

    result = pull_local_models.warm_speech_service(
        base_url="http://speech:9000",
        opener=opener,
    )

    assert result == {"stt": "ready", "tts": "ready"}
    assert captured["url"] == "http://speech:9000/admin/warmup"
    assert captured["method"] == "POST"


def test_main_can_skip_ollama_pull_from_environment(monkeypatch, capsys):
    monkeypatch.setenv("CVI_SKIP_OLLAMA_PULL", "1")
    monkeypatch.setenv("CVI_SPEECH_URL", "")
    monkeypatch.setattr(
        pull_local_models,
        "ollama_model_names",
        lambda: ["qwen2.5:7b-instruct"],
    )

    def fail_pull(*args, **kwargs):
        raise AssertionError("Ollama pull should have been skipped")

    monkeypatch.setattr(pull_local_models, "pull_ollama_model", fail_pull)
    monkeypatch.setattr(pull_local_models, "local_speech_downloads", lambda: [])

    assert pull_local_models.main([]) == 0
    assert "Pulling Ollama model" not in capsys.readouterr().out


def test_main_defers_when_speech_warmup_times_out(monkeypatch, capsys):
    monkeypatch.setattr(
        pull_local_models,
        "ollama_model_names",
        lambda: [],
    )
    monkeypatch.setattr(
        pull_local_models,
        "local_speech_downloads",
        lambda: [],
    )
    monkeypatch.setenv("CVI_SPEECH_URL", "http://speech:9000")

    def fail_warmup(**kwargs):
        raise RuntimeError("Speech warmup failed: TimeoutError: timed out")

    monkeypatch.setattr(
        pull_local_models,
        "warm_speech_service",
        fail_warmup,
    )

    assert pull_local_models.main([]) == 0
    assert "deferred to first use" in capsys.readouterr().out
