from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_render_blueprint_defines_single_free_docker_web_service():
    blueprint = _read("render.yaml")

    for marker in (
        "type: web",
        "runtime: docker",
        "plan: free",
        "dockerfilePath: ./Dockerfile.render",
        "healthCheckPath: /health/ready",
        "key: CVI_PERSISTENCE_BACKEND",
        "value: postgresql",
        "key: CVI_LLM_PROVIDER",
        "value: groq",
        "key: CVI_VOICE_STT_PROVIDER",
        "value: local",
        "key: CVI_VOICE_TTS_PROVIDER",
        "value: browser",
        "key: CVI_SPEECH_STT_ENGINE",
        "value: groq",
        "key: CVI_SPEECH_VAD_PROVIDER",
        "value: energy",
        "sync: false",
    ):
        assert marker in blueprint

    assert "databases:" not in blueprint


def test_render_dockerfile_includes_api_worker_and_speech_runtime():
    dockerfile = _read("Dockerfile.render")

    for marker in (
        "FROM base AS render",
        "COPY deploy ./deploy",
        "scripts.render_start",
        "ENV PORT=10000",
    ):
        assert marker in dockerfile


def test_render_start_runs_migrations_worker_speech_and_api():
    script = _read("scripts/render_start.py")

    for marker in (
        "run_migrations()",
        "register_handlers()",
        "worker_from_environment(api.repository)",
        "deploy.speech.app:app",
        "uvicorn.run(",
        "api.app,",
        'os.getenv("PORT", "10000")',
    ):
        assert marker in script


def test_render_docs_document_neon_and_tokenless_demo_url():
    docs = _read("docs/render.md")

    for marker in (
        "Neon",
        "onrender.com",
        "?demo=portfolio-demo",
        "GROQ_API_KEY",
        "RESEARCHER_ACCESS_CODE",
        "Do not paste secrets into chat",
    ):
        assert marker in docs
