import os
import sys

from pydantic import BaseModel
from agent import execute

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


app = FastAPI(
    title="Financial Research Assistant",
    description=(
        "An agentic AI research assistant compatible with Groq, OpenAI, "
        "Google Gemini and Ollama models."
    ),
    version="1.1.0",
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
        answer = execute(request.question)

        return {
            "question": request.question,
            "answer": answer
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
        "version": "1.1.0"
    }


@app.get("/version")
def version():
    """Diagnostic: which commit, model and code path are actually running."""
    provider, model = _active_model()
    return {
        "service_version": "1.1.0",
        "git_commit": os.getenv("RENDER_GIT_COMMIT"),
        "guardrails_loaded": "guardrails" in sys.modules,
        "llm_provider": provider,
        "model": model,
    }

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
