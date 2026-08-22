from langchain_ollama import ChatOllama
from langchain_core.tools import tool

from financial_tools import (
    search_stock,
    get_profile,
    get_peers,
    get_quote,
    get_performance,
    get_consensus,
    get_company_news,
)


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
    get_consensus_tool,
    get_company_news_tool,
]


tool_map = {
    tool.name: tool
    for tool in tools
}


llm = ChatOllama(
    model="llama3.2:3b",
    temperature=0
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

When a tool returns an error, use the tool's error_type and message to
explain the limitation accurately.
"""


def execute(query: str):
    """
    Execute a financial research question using the tool-enabled LLM.
    """

    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", query),
    ]

    max_iterations = 8

    for _ in range(max_iterations):

        response = llm_with_tools.invoke(messages)

        messages.append(response)

        # The model has finished answering.
        if not response.tool_calls:
            return response.content

        # Execute every tool requested by the model.
        for tool_call in response.tool_calls:

            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            selected_tool = tool_map.get(tool_name)

            if selected_tool is None:
                tool_result = {
                    "error": f"Unknown tool: {tool_name}"
                }
            else:
                try:
                    tool_result = selected_tool.invoke(tool_args)
                except Exception as e:
                    tool_result = {
                        "error": str(e)
                    }

            messages.append(
                {
                    "role": "tool",
                    "content": str(tool_result),
                    "tool_call_id": tool_call["id"],
                }
            )

    return "The agent reached the maximum number of tool iterations."