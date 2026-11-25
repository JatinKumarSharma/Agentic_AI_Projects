"""Reproducible v5 probe for capability, scope and tool-loop regressions.

Run from the project root:
    python eval-runs/capability_probe.py

This is separate from eval_suite.py so the original v0-v4 suite remains
unchanged and directly comparable.
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from agent import SYSTEM_PROMPT, llm_with_tools, normalize_response, tool_map
from guardrails import apply_guardrails, iteration_fallback, pre_query_guardrail

QUESTIONS = [
    "hi",
    "I want to know whether you can generate images or not?",
    "but in one of the conversations you generated an image",
    "lets talk about relationships",
    "you forget you are an financial research analyst so why didn't you said financial or market relationships?",
    "Can you represent the ups and downs of TATA steel company shares in past one month",
    "Can you represent the ups and downs of AAPL shares in past one month",
]


def capture(question):
    early = pre_query_guardrail(question)
    if early:
        return {
            "question": question,
            "path": "deterministic_guardrail",
            "tools": [],
            "answer": apply_guardrails(early, []),
        }

    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=question)]
    tool_results = []
    used_tools = []
    seen_calls = set()
    answer = ""

    for _ in range(8):
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        repeated = False
        for tc in response.tool_calls:
            signature = (tc["name"], json.dumps(tc["args"], sort_keys=True, default=str))
            if signature in seen_calls:
                repeated = True
            seen_calls.add(signature)
            used_tools.append({"tool": tc["name"], "args": tc["args"]})
            tool = tool_map.get(tc["name"])
            try:
                result = tool.invoke(tc["args"]) if tool else {
                    "success": False,
                    "error_type": "provider_error",
                    "message": f"Unknown tool: {tc['name']}",
                }
            except Exception as exc:
                result = {
                    "success": False,
                    "error_type": "provider_error",
                    "message": str(exc),
                }
            tool_results.append(result)
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

        if not response.tool_calls:
            final_text = normalize_response(response.content).strip()
            if not final_text:
                final_text = iteration_fallback(tool_results)
            answer = apply_guardrails(final_text, tool_results)
            break

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
    else:
        answer = apply_guardrails(iteration_fallback(tool_results), tool_results)

    return {"question": question, "path": "model", "tools": used_tools, "answer": answer}


results = [capture(question) for question in QUESTIONS]
for item in results:
    print(f"\n{'=' * 72}\n{item['question']}\nPATH: {item['path']}")
    print("TOOLS:", [call["tool"] for call in item["tools"]])
    print("ANSWER:\n", item["answer"][:1200])

output = Path(__file__).resolve().parent / "capability_probe_v5.json"
with open(output, "w", encoding="utf-8") as handle:
    json.dump(results, handle, indent=2, ensure_ascii=False)
print(f"\nSaved {output}")
