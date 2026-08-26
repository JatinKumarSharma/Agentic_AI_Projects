from pydantic import BaseModel
from agent import execute

from fastapi import FastAPI, HTTPException

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
    title="Financial Analyst API",
    description="Financial data API powered by OpenBB",
    version="1.0.0",
)


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
        "message": "Financial Analyst API is running",
        "docs": "/docs"
    }

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "financial-analyst-api",
        "version": "1.0.0"
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
