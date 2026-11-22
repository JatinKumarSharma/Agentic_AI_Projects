"""Run both eval questions for an iteration and save traces with labelled output."""
import json
import sys
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv()

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from agent import llm_with_tools, tool_map, SYSTEM_PROMPT, normalize_response

QUESTIONS = {
    "runA": "What is Apple's (AAPL) current quote and who are its main peers?",
    "runB": "Give me the company profile and analyst consensus price target for the ticker ZZZZ.",
}
ITERATION = sys.argv[1] if len(sys.argv) > 1 else "1"


def capture(question):
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=question),
    ]
    trace = []
    final_answer = ""
    for step in range(8):
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        tool_calls = []
        for tc in response.tool_calls:
            entry = {"tool": tc["name"], "args": tc["args"]}
            tool = tool_map.get(tc["name"])
            if tool is None:
                result = {"error": f"Unknown tool: {tc['name']}"}
            else:
                try:
                    result = tool.invoke(tc["args"])
                except Exception as exc:
                    result = {"error": str(exc)}
            entry["result"] = str(result)
            tool_calls.append(entry)
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
        trace.append({
            "step": step + 1,
            "model_text": normalize_response(response.content),
            "tool_calls": tool_calls,
        })
        if not response.tool_calls:
            final_answer = normalize_response(response.content)
            break
    return {"question": question, "trace": trace, "final_answer": final_answer}


for key, question in QUESTIONS.items():
    result = capture(question)
    path = f"iter{ITERATION}_{key}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
    print(f"\n{'=' * 70}\n{key} — {question}\n{'=' * 70}")
    print("TOOLS:", [tc["tool"] for s in result["trace"] for tc in s["tool_calls"]])
    print("FINAL ANSWER:\n", result["final_answer"][:2500])
    print(f"\nSaved to {path}")
