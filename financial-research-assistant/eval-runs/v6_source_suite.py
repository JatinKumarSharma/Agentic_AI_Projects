"""Capture a fixed v6 source-expansion probe against the live agent.

Usage:
    python eval-runs/v6_source_suite.py

This is separate from eval_suite.py on purpose. The five historical questions
must remain identical across v0–v5, so v6 evidence is recorded in its own
suite. Each result includes the tool-call trace, source labels and the
deterministic call ceiling for manual 5-D scoring and efficiency review.

Requires valid provider keys in a `.env` file. It makes real source calls.
"""
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from agent import execute

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

results = {}
for key, case in CASES.items():
    try:
        trace = execute(case["question"], return_trace=True)
        trace["error"] = None
    except Exception as exc:
        trace = {
            "answer": "",
            "tool_calls": [],
            "sources": [],
            "max_tool_calls": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    tool_names = [call["name"] for call in trace["tool_calls"]]
    results[key] = {
        **case,
        "actual_tools": tool_names,
        "tool_call_count": len(tool_names),
        "call_ceiling": trace["max_tool_calls"],
        "sources": trace["sources"],
        "answer": trace["answer"],
        "error": trace["error"],
    }

    print(f"\n{'=' * 72}\n{key}: {case['question']}\n{'=' * 72}")
    print(f"EXPECTED TOOL: {case['expected_tool']}")
    print(f"ACTUAL TOOLS:  {tool_names}")
    print(f"CALLS / CEILING: {len(tool_names)} / {trace['max_tool_calls']}")
    print(f"SOURCES: {trace['sources']}")
    if trace["error"]:
        print(f"ERROR: {trace['error']}")
    else:
        print("ANSWER:\n", trace["answer"][:2000])

output_path = Path(__file__).resolve().parent / "v6_source_suite.json"
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(results, handle, indent=2, ensure_ascii=False)

print(f"\nSaved {output_path}")
