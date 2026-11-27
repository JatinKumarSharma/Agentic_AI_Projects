import os
import sys

from pydantic import BaseModel
from agent import execute
from source_tools import source_status

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from financial_tools import (
    search_stock,
    get_profile,
    get_peers,
    get_quote,
    get_performance,
    get_consensus,
    get_company_news,
)

class Question(BaseModel):
    question: str
    # Optional evaluation flag. Normal UI traffic stays unchanged; trace mode
    # returns deterministic tool-call evidence without exposing credentials.
    include_trace: bool = False


app = FastAPI(
    title="Financial Research Assistant",
    description=(
        "An agentic AI research assistant compatible with Groq, OpenAI, "
        "Google Gemini and Ollama models."
    ),
    version="1.3.3",
)

# Restrict browser access to the configured frontend origins. Add deployed UI
# domains to FRONTEND_ORIGINS as a comma-separated list, never a wildcard.
_allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_MODEL_ENV = {
    "groq": ("GROQ_MODEL", "openai/gpt-oss-20b"),
    "openai": ("OPENAI_MODEL", "gpt-4o-mini"),
    "google": ("GOOGLE_MODEL", "gemini-2.5-flash"),
    "ollama": ("OLLAMA_MODEL", "llama3.2:3b"),
}


def _active_model():
    provider = os.getenv("LLM_PROVIDER", "ollama")
    env_key, default = _MODEL_ENV.get(provider, ("", "unknown"))
    return provider, (os.getenv(env_key, default) if env_key else default)


@app.post("/ask")
def ask_financial_question(request: Question):

    try:
        # Always run in trace mode internally so deterministic chart specs can
        # be returned to the web UI. The normal response stays intentionally
        # small; only include_trace exposes the full evaluation evidence.
        trace = execute(request.question, return_trace=True)
        charts = trace.get("charts", [])

        if request.include_trace:
            return {
                "question": request.question,
                "answer": trace["answer"],
                "tool_calls": trace["tool_calls"],
                "sources": trace["sources"],
                "max_tool_calls": trace["max_tool_calls"],
                "charts": charts,
            }

        return {
            "question": request.question,
            "answer": trace["answer"],
            "charts": charts,
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/")
def home():
    return {
        "message": "Financial Research Assistant is running",
        "docs": "/docs"
    }

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "financial-analyst-api",
        "version": app.version
    }


@app.get("/version")
def version():
    """Diagnostic: which commit, model and code path are actually running."""
    provider, model = _active_model()
    status = source_status()
    return {
        "service_version": app.version,
        "git_commit": os.getenv("RENDER_GIT_COMMIT"),
        "guardrails_loaded": "guardrails" in sys.modules,
        "source_tools_loaded": "source_tools" in sys.modules,
        "fred_configured": status["fred"],
        "web_search_provider": status["web_search_provider"],
        "llm_provider": provider,
        "model": model,
    }


@app.get("/sources")
def sources():
    """Report configured evidence sources without exposing any credentials."""
    return source_status()

@app.get("/stock/search/{symbol}")
def stock_search(symbol: str):
    try:
        result = search_stock(symbol)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/stock/{symbol}/profile")
def stock_profile(symbol: str):
    try:
        result = get_profile(symbol)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/stock/{symbol}/peers")
def stock_peers(symbol: str):
    try:
        result = get_peers(symbol)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/stock/{symbol}/quote")
def stock_quote(symbol: str):
    try:
        result = get_quote(symbol)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/stock/{symbol}/performance")
def stock_performance(symbol: str):
    try:
        result = get_performance(symbol)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/stock/{symbol}/consensus")
def stock_consensus(symbol: str):
    try:
        result = get_consensus(symbol)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/news/{symbol}")
def company_news(symbol: str):
    try:
        result = get_company_news(symbol)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
