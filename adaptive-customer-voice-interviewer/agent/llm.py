"""Provider-neutral language-model boundary for the research engine.

The graph nodes depend only on ``get_llm`` and ``get_structured_llm``. Provider
SDKs are imported lazily so deployments can choose Groq, Ollama, or another
adapter without changing research logic or unit tests.
"""

import os
from typing import Type

from pydantic import BaseModel


def _llm_provider() -> str:
    return os.getenv("CVI_LLM_PROVIDER", "groq").strip().lower()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")

    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc

    if value < 0:
        raise ValueError(f"{name} cannot be negative.")

    return value


PURPOSE_DEFAULT_TEMPERATURES = {
    "default": 0.0,
    "proposal": 0.3,
    "engagement": 0.4,
}

_PURPOSE_TEMPERATURE_ENV = {
    "proposal": "CVI_PROPOSAL_TEMPERATURE",
    "engagement": "CVI_ENGAGEMENT_TEMPERATURE",
}
_GLOBAL_TEMPERATURE_ENV = "CVI_LLM_TEMPERATURE"


def _parse_temperature(name: str, raw: str) -> float:
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc

    if not 0 <= value <= 2:
        raise ValueError(f"{name} must be between 0 and 2.")

    return value


def _temperature_for(purpose: str) -> float:
    if purpose not in PURPOSE_DEFAULT_TEMPERATURES:
        supported = ", ".join(sorted(PURPOSE_DEFAULT_TEMPERATURES))
        raise ValueError(
            f"Unknown LLM purpose {purpose!r}. Supported purposes: {supported}."
        )

    env_names = (
        _PURPOSE_TEMPERATURE_ENV.get(purpose),
        _GLOBAL_TEMPERATURE_ENV,
    )

    for name in env_names:
        if not name:
            continue
        raw = os.getenv(name)
        if raw and raw.strip():
            return _parse_temperature(name, raw)

    return PURPOSE_DEFAULT_TEMPERATURES[purpose]


def _ollama_base_url() -> str:
    """Return the Ollama endpoint, which differs between host and containers."""

    return (
        os.getenv("CVI_OLLAMA_BASE_URL", "http://localhost:11434").strip()
        or "http://localhost:11434"
    )


def _structured_method(provider: str) -> str:
    configured = os.getenv("CVI_LLM_STRUCTURED_METHOD", "").strip().lower()

    if configured:
        return configured

    # Groq's function-calling path handles the nested research schemas
    # reliably across models. Ollama's local JSON-schema mode remains the
    # default for local deployments.
    return "function_calling" if provider == "groq" else "json_schema"


def get_llm(model: str | None = None, *, purpose: str = "default"):
    """Return the configured chat model for a research purpose."""

    provider = _llm_provider()
    temperature = _temperature_for(purpose)

    if provider == "groq":
        from langchain_groq import ChatGroq

        model_name = (
            model
            or os.getenv(
                "GROQ_MODEL",
                "qwen/qwen3.8-27b",
            )
        ).strip()

        if not model_name:
            raise ValueError("GROQ_MODEL cannot be empty.")

        return ChatGroq(
            model=model_name,
            temperature=temperature,
            # Keep every request under common free-tier output ceilings.
            max_tokens=999,
            # Nested SDK retries can honour multi-minute Retry-After headers.
            # A live interview fails fast and uses the bounded application
            # retry policy instead.
            max_retries=_env_int("CVI_LLM_MAX_RETRIES", 0),
            timeout=_env_float("CVI_LLM_REQUEST_TIMEOUT", 12.0),
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        model_name = (
            model
            or os.getenv(
                "LOCAL_LLM_MODEL",
                "llama3.2:3b",
            )
        ).strip()

        if not model_name:
            raise ValueError("LOCAL_LLM_MODEL cannot be empty.")

        return ChatOllama(
            model=model_name,
            temperature=temperature,
            num_predict=999,
            # Reasoning models such as gpt-oss otherwise consume the output
            # budget with thinking tokens and can return empty structured
            # content. The interview engine needs schema-valid JSON quickly.
            reasoning=False,
            base_url=_ollama_base_url(),
        )

    raise ValueError(
        "CVI_LLM_PROVIDER must be one of: groq, ollama. "
        f"Got: {provider}"
    )


def invoke_output_limit(limit: int) -> dict:
    """Return provider-specific kwargs that cap one invocation's output.

    Ollama carries ``num_predict`` on the model from ``get_llm()``, so passing
    an output cap again at invoke time would reach its raw client and fail.
    """

    if _llm_provider() == "groq":
        return {"max_tokens": limit}

    return {}


def get_structured_llm(schema: Type[BaseModel]):
    """Return a model configured to produce the supplied schema."""

    provider = _llm_provider()
    kwargs = {"method": _structured_method(provider)}

    # langchain-ollama rejects every extra with_structured_output kwarg;
    # its output cap is already set as num_predict on the model itself.
    if provider == "groq":
        # Stay under common free-tier output-token ceilings.
        kwargs["max_tokens"] = 999

    return get_llm().with_structured_output(schema, **kwargs)


def get_evaluator_llm():
    """Return the model used for proposal evaluation.

    A deployment may select a cheaper/faster evaluator model without
    changing the research graph. When unset, the evaluator follows the
    primary provider configuration.
    """

    return get_llm(model=os.getenv("CVI_EVALUATOR_MODEL") or None)
