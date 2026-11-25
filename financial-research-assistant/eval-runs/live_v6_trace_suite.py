"""Capture v6 tool-selection traces from a deployed API.

Run from the project root:
    python eval-runs/live_v6_trace_suite.py

Set API_BASE to override the default Render URL. No keys are needed locally;
the deployed service uses its own environment variables. Each response is
requested with include_trace=true so tool calls, sources and the deterministic
call ceiling can be recorded without exposing credentials.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API_BASE = os.getenv("API_BASE", "https://financial-analyst-api-fw98.onrender.com").rstrip("/")

CASES = {
    "v6_q1_sec_filings": {
        "question": "Show me Apple's most recent 10-K filings from SEC EDGAR.",
        "expected_tool": "get_sec_filings_tool",
        "expected_source": "SEC EDGAR",
        "source_tier": "primary_filing_index",
    },
    "v6_q2_sec_facts": {
        "question": "What is Microsoft's latest annual revenue from SEC XBRL company facts?",
        "expected_tool": "get_sec_company_facts_tool",
        "expected_source": "SEC EDGAR",
        "source_tier": "xbrl_company_facts",
    },
    "v6_q3_statement": {
        "question": "Pull Tesla's annual cash-flow statement for the last four periods.",
        "expected_tool": "get_financial_statement_tool",
        "expected_source": "OpenBB / FMP",
        "source_tier": "structured_statement",
    },
    "v6_q4_macro": {
        "question": "What is the latest US CPI reading from FRED?",
        "expected_tool": "get_macro_indicator_tool",
        "expected_source": "FRED",
        "source_tier": "macro_indicator",
    },
    "v6_q5_web": {
        "question": "Find recent Reuters or Bloomberg coverage about Nvidia before its earnings.",
        "expected_tool": "search_financial_web_tool",
        "expected_source": "Web search",
        "source_tier": "discovery",
    },
}


def ask(question):
    payload = json.dumps({"question": question, "include_trace": True}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


results = {}
for key, case in CASES.items():
    try:
        response = ask(case["question"])
        error = None
    except Exception as exc:
        response = {}
        error = f"{type(exc).__name__}: {exc}"

    tool_calls = response.get("tool_calls", [])
    tool_names = [call.get("name") for call in tool_calls]
    results[key] = {
        **case,
        "actual_tools": tool_names,
        "tool_call_count": len(tool_names),
        "call_ceiling": response.get("max_tool_calls"),
        "sources": response.get("sources", []),
        "answer": response.get("answer", ""),
        "error": error,
    }

    print(f"\n{'=' * 72}\n{key}: {case['question']}\n{'=' * 72}")
    print(f"EXPECTED TOOL: {case['expected_tool']}")
    print(f"ACTUAL TOOLS:  {tool_names}")
    print(f"CALLS / CEILING: {len(tool_names)} / {response.get('max_tool_calls')}")
    print(f"SOURCES: {response.get('sources', [])}")
    if error:
        print(f"ERROR: {error}")
    else:
        print("ANSWER:\n", response.get("answer", "")[:2000])

output_path = Path(__file__).resolve().parent / "live_v6_traces.json"
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(results, handle, indent=2, ensure_ascii=False)

print(f"\nSaved {output_path}")
