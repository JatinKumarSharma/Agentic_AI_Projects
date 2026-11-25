import json
import os
from dotenv import load_dotenv

load_dotenv()

from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool

from langchain_core.messages import (
    SystemMessage,
    HumanMessage,
    ToolMessage,
)

from financial_tools import (
    search_stock,
    get_profile,
    get_peers,
    get_quote,
    get_performance,
    get_historical_prices,
    get_consensus,
    get_company_news,
)
from guardrails import apply_guardrails, iteration_fallback, pre_query_guardrail


@tool
def search_stock_tool(symbol: str):
    """Search for a stock ticker and return matching companies."""
    return search_stock(symbol)


@tool
def get_profile_tool(symbol: str):
    """Get company profile information."""
    return get_profile(symbol)


@tool
def get_peers_tool(symbol: str):
    """Get peer companies for a stock."""
    return get_peers(symbol)


@tool
def get_quote_tool(symbol: str):
    """Get the latest stock quote."""
    return get_quote(symbol)


@tool
def get_performance_tool(symbol: str):
    """Get stock price performance."""
    return get_performance(symbol)


@tool
def get_historical_prices_tool(symbol: str, days: int = 30):
    """Get daily OHLC prices for roughly the last N calendar days.

    Use this for questions about price movement, ups and downs, trends, recent
    swings, or a one-month chart. Report period_change_pct directly.
    """
    return get_historical_prices(symbol, days)


@tool
def get_consensus_tool(symbol: str):
    """Get analyst consensus price target."""
    return get_consensus(symbol)


@tool
def get_company_news_tool(symbol: str):
    """Get recent company news."""
    return get_company_news(symbol)


tools = [
    search_stock_tool,
    get_profile_tool,
    get_peers_tool,
    get_quote_tool,
    get_performance_tool,
    get_historical_prices_tool,
    get_consensus_tool,
    get_company_news_tool,
]


tool_map = {
    tool.name: tool
    for tool in tools
}


LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

if LLM_PROVIDER == "ollama":

    llm = ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        temperature=0
    )

elif LLM_PROVIDER == "openai":

    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0
    )

elif LLM_PROVIDER == "google":

    llm = ChatGoogleGenerativeAI(
        model=os.getenv("GOOGLE_MODEL", "gemini-2.5-flash"),
        temperature=0
    )

elif LLM_PROVIDER == "groq":

    llm = ChatGroq(
        model=os.getenv(
            "GROQ_MODEL",
            "openai/gpt-oss-20b"
        ),
        temperature=0
    )

else:

    raise ValueError(
        f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}"
    )

llm_with_tools = llm.bind_tools(tools)


SYSTEM_PROMPT = """
You are an expert financial research assistant.

You have access to financial-data tools.

RULES:

1. Use the tools whenever the question requires financial data.
2. Never invent financial data.
3. Never rely on your internal knowledge when the requested information
   can be obtained from a tool.
4. When comparing companies, use the actual tool results.
5. When a tool returns success=false, do not invent missing data.
6. Clearly explain when requested data could not be retrieved.
7. Never claim that data is unavailable because of a premium subscription
   unless the tool explicitly reports a provider subscription restriction.
8. Distinguish retrieved facts from your reasoning.
9. You may perform multiple tool calls to answer a question.
10. For multi-step questions, complete each step before producing the
    final answer.
11. Do not fabricate analyst names, ratings, dates, or price targets.
12. When a tool returns a percentage field (for example change_percent),
    use that value directly. Never recompute a percentage from other fields.
13. If a search returns no results, try at least one alternative tool or a
    broader query before concluding that data is unavailable.
14. When declining a request for ethical, privacy or fairness reasons,
    explain the reason in one sentence and offer a legitimate,
    data-grounded alternative. Never give a bare refusal.
15. For historical prices, recent price movement, ups and downs, trends, or
    chart-style requests, use get_historical_prices_tool and report its
    period_change_pct directly. Do not recompute it.
16. If no tool can provide the requested data, say so in one sentence and
    list what you can provide. Do not keep calling tools for the same
    missing data.
17. Stay in the financial research role. Do not claim to generate images or
    to remember previous conversations.
18. If a ticker search returns no results, do not repeat the same search.
    Use the most likely symbol with the relevant data tool once, then report
    the outcome.

When a tool returns an error, use the tool's error_type and message to
explain the limitation accurately.
"""

def normalize_response(content):

    if isinstance(content, str):
        return content

    if isinstance(content, list):

        text_parts = []

        for item in content:

            if isinstance(item, dict):
                text = item.get("text")

                if text:
                    text_parts.append(text)

            elif hasattr(item, "text"):
                text_parts.append(item.text)

            else:
                text_parts.append(str(item))

        return "\n".join(text_parts)

    return str(content)

def execute(query: str):
    """
    Execute a financial research question using the tool-enabled LLM.
    """
    # Capability and scope boundaries are deterministic. They do not depend on
    # the model remembering a prompt rule.
    early_answer = pre_query_guardrail(query)
    if early_answer:
        return apply_guardrails(early_answer, [])

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=query),
    ]

    max_iterations = 8
    tool_results = []
    seen_calls = set()

    for _ in range(max_iterations):

        response = llm_with_tools.invoke(messages)

        messages.append(response)

        # The model has finished answering. Deterministic guardrails run on
        # the final text regardless of what the model was instructed to do.
        if not response.tool_calls:
            answer = normalize_response(response.content).strip()
            if not answer:
                answer = iteration_fallback(tool_results)
            return apply_guardrails(answer, tool_results)

        repeated = False

        # Execute every tool requested by the model.
        for tool_call in response.tool_calls:

            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            signature = (tool_name, json.dumps(tool_args, sort_keys=True, default=str))
            if signature in seen_calls:
                repeated = True
            seen_calls.add(signature)

            selected_tool = tool_map.get(tool_name)

            if selected_tool is None:
                tool_result = {
                    "success": False,
                    "error_type": "provider_error",
                    "message": f"Unknown tool: {tool_name}",
                }
            else:
                try:
                    tool_result = selected_tool.invoke(tool_args)
                except Exception as e:
                    tool_result = {
                        "success": False,
                        "error_type": "provider_error",
                        "message": str(e),
                    }

            tool_results.append(tool_result)

            messages.append(
                ToolMessage(
                    content=str(tool_result),
                    tool_call_id=tool_call["id"],
                )
            )

        # A repeated call with no new information is a deterministic stop
        # signal. Force the next model turn to conclude instead of looping.
        if repeated:
            messages.append(
                SystemMessage(
                    content=(
                        "A repeated tool call returned no new information. "
                        "If the question concerns historical prices, call "
                        "get_historical_prices_tool once with the most likely "
                        "symbol. Otherwise answer now using only the results "
                        "already retrieved, or state the limitation."
                    )
                )
            )

    return apply_guardrails(iteration_fallback(tool_results), tool_results)
