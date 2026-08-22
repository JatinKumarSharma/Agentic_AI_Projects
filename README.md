# Agentic Financial Analyst API

An agentic financial analysis API built with **FastAPI, LangChain,
Ollama, and OpenBB**. It allows users to ask natural-language financial
questions and uses financial-data tools to retrieve information, perform
multi-step analysis, and return a concise answer.

## Project Overview

The project demonstrates an agentic financial research workflow:

``` text
User Question
     ↓
FastAPI /ask
     ↓
LLM Agent (Ollama)
     ↓
Tool Selection
     ↓
OpenBB Financial Tools
     ↓
Financial Data
     ↓
Reasoning over Results
     ↓
Final Answer
```

For example, a question such as:

> Find TSLA peers, determine which peer has the highest market cap, and
> then get the consensus price target for that company.

can require multiple tool calls. The agent can retrieve TSLA peers,
compare their market capitalizations, identify the highest-market-cap
peer, and then attempt to retrieve its consensus price target.

## Architecture

``` text
                    ┌─────────────────────┐
                    │        User         │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │      FastAPI        │
                    │       /ask          │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Agent / LLM       │
                    │    Ollama           │
                    │   llama3.2:3b       │
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
             Tool Selection        Multi-step Reasoning
                    │                     │
                    └──────────┬──────────┘
                               ▼
                    ┌─────────────────────┐
                    │  Financial Tools    │
                    └──────────┬──────────┘
                               ▼
                    ┌─────────────────────┐
                    │       OpenBB        │
                    └──────────┬──────────┘
                               ▼
                    Financial Data Providers
```

## Tech Stack

  Component         Technology
  ----------------- --------------
  Language          Python
  API Framework     FastAPI
  Agent Framework   LangChain
  LLM Runtime       Ollama
  LLM Model         Llama 3.2 3B
  Financial Data    OpenBB
  Data Processing   Pandas
  Validation        Pydantic
  API Server        Uvicorn

## Project Structure

``` text
Agentic_AI_Projects/
├── .gitignore
├── agent.py
├── financial_tools.py
├── main.py
└── requirements.txt
```

### `main.py`

Defines the FastAPI application and exposes the HTTP API endpoints.

### `agent.py`

Contains the tool-enabled LLM agent, tool wrappers, system instructions,
and the multi-step tool execution loop.

### `financial_tools.py`

Contains functions that retrieve financial information through OpenBB.

### `requirements.txt`

Contains the Python dependencies required to run the project.

## API Endpoints

  Method   Endpoint                        Purpose
  -------- ------------------------------- ------------------------------------------
  POST     `/ask`                          Ask a financial question using the agent
  GET      `/`                             API health/home endpoint
  GET      `/stock/search/{symbol}`        Search for a stock
  GET      `/stock/{symbol}/profile`       Retrieve company profile
  GET      `/stock/{symbol}/peers`         Retrieve peer companies
  GET      `/stock/{symbol}/quote`         Retrieve latest quote
  GET      `/stock/{symbol}/performance`   Retrieve price performance
  GET      `/stock/{symbol}/consensus`     Retrieve analyst consensus
  GET      `/news/{symbol}`                Retrieve company news
  GET      `/news`                         World-news endpoint

## Example Request

### Ask a Financial Question

`POST /ask`

``` json
{
  "question": "What is the current price of TSLA?"
}
```

Example response:

``` json
{
  "question": "What is the current price of TSLA?",
  "answer": "The current price of TSLA (Tesla, Inc.) is $362.86 as of the last trade on August 21, 2026."
}
```

## Agent Capabilities

The agent currently has access to tools for:

-   Stock search
-   Company profiles
-   Peer companies
-   Stock quotes
-   Price performance
-   Analyst consensus
-   Company news

It can perform multiple tool calls when a question requires several
pieces of information.

Example workflow:

``` text
Find TSLA peers
      ↓
Compare market capitalizations
      ↓
Identify highest-market-cap peer
      ↓
Request consensus price target
      ↓
Return result or explain provider limitation
```

## Example Questions

``` text
What is the current price of TSLA?

Show me the profile of Tesla.

What are TSLA's peers?

How has TSLA performed over the last year?

What is the analyst consensus price target for TSLA?

Find TSLA peers, determine which peer has the highest market cap,
and then get the consensus price target for that company.
```

## Running Locally

### 1. Clone the repository

``` bash
git clone https://github.com/JatinKumarSharma/Agentic_AI_Projects.git
cd Agentic_AI_Projects
```

### 2. Create a virtual environment

Windows:

``` bash
python -m venv .venv
.venv\Scriptsctivate
```

### 3. Install dependencies

``` bash
pip install -r requirements.txt
```

### 4. Start Ollama

The current agent uses:

``` text
llama3.2:3b
```

Check installed models:

``` bash
ollama list
```

If required:

``` bash
ollama pull llama3.2:3b
```

Start the Ollama server if it is not already running:

``` bash
ollama serve
```

Keep this terminal running.

### 5. Start FastAPI

In another terminal:

``` bash
uvicorn main:app --reload
```

Open:

``` text
http://127.0.0.1:8000/docs
```

The interactive Swagger documentation can be used to test all available
endpoints.

## Design Principles

The agent is instructed to:

1.  Use financial tools whenever the question requires financial data.
2.  Never invent financial data.
3.  Prefer retrieved tool data over internal model knowledge.
4.  Use actual tool results when comparing companies.
5.  Clearly explain when requested data cannot be retrieved.
6.  Distinguish retrieved facts from reasoning.
7.  Perform multiple tool calls when required.
8.  Complete multi-step questions before producing the final answer.
9.  Avoid fabricating analyst names, ratings, dates, or price targets.
10. Explain provider errors using the information returned by the tool.

## Error Handling

The project includes basic handling for tool and provider failures.

For example, during testing, a consensus request can be unavailable
because of a provider subscription restriction. In that situation, the
agent reports the limitation instead of fabricating a result.

``` text
Tool succeeds
     ↓
Use retrieved data

Tool fails / provider restriction
     ↓
Explain limitation
     ↓
Do not fabricate data
```

## Current Limitations

This is an MVP/portfolio project. Known limitations include:

-   Financial-data availability depends on the configured OpenBB
    providers.
-   Some provider endpoints may require subscriptions or additional
    configuration.
-   Company-news retrieval may return an empty provider response for
    some requests.
-   The world-news endpoint currently requires a `get_world_news`
    implementation.
-   Local Ollama inference means response speed depends on local
    hardware.
-   The project focuses on financial research and data retrieval rather
    than investment advice.
-   Persistent conversation memory and portfolio management are not
    currently implemented.

## Project Status

**MVP --- Functional**

The core workflow has been tested with:

-   FastAPI API requests
-   OpenBB financial-data tools
-   Stock lookup
-   Company profiles
-   Peer comparison
-   Stock quotes
-   Performance data
-   Analyst consensus data
-   Multi-step agentic questions
-   Local Ollama inference
-   Tool/provider error handling

## Disclaimer

This project is intended for educational, research, and
software-engineering demonstration purposes. It does not provide
personalized investment advice and does not guarantee the accuracy,
completeness, or timeliness of financial data.

## Author

**Jatin Sharma**

Built as part of an ongoing portfolio of agentic AI and financial-data
projects.
