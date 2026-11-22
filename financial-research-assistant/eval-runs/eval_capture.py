"""Capture a full agent trace: question, tool calls, tool results, final answer.

Drop this file into the Agentic_AI_Projects repo root and run:
    python eval_capture.py
"""
import json
import sys
from dotenv import load_dotenv

# Windows consoles default to cp1252; force UTF-8 so tool output prints safely.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from agent import llm_with_tools, tool_map, SYSTEM_PROMPT, normalize_response
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

# ------------------------------------------------------------------
# CHANGE THIS between runs
# ------------------------------------------------------------------
QUESTION = "Give me the company profile and analyst consensus price target for the ticker ZZZZ."
OUTPUT_FILE = "agent_run_trace_run2.json"
# ------------------------------------------------------------------

messages = [
    SystemMessage(content=SYSTEM_PROMPT),
    HumanMessage(content=QUESTION),
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
        messages.append(
            ToolMessage(content=str(result), tool_call_id=tc["id"])
        )

    trace.append({
        "step": step + 1,
        "model_text": normalize_response(response.content),
        "tool_calls": tool_calls,
    })

    if not response.tool_calls:
        final_answer = normalize_response(response.content)
        break

payload = {
    "question": QUESTION,
    "provider_trace": True,
    "trace": trace,
    "final_answer": final_answer,
}

with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, ensure_ascii=False)

print(json.dumps(payload, indent=2, ensure_ascii=False))
print(f"\nSaved to {OUTPUT_FILE}")
