"""Deterministic guardrail tests using the exact strings from earlier traces.

Run from the project root:
    python eval-runs/guardrails_test.py

These are code-level checks. They do not call a model or an API.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from guardrails import (
    apply_guardrails,
    best_guess_symbol,
    iteration_fallback,
    normalize_quote_record,
    percentage_guardrail,
    pre_query_guardrail,
    refusal_guardrail,
    source_attribution_guardrail,
    sources_from_results,
    tool_plan,
    wants_chart,
)
from visualization import render_chart

RAW_QUOTE = {
    "symbol": "AAPL",
    "change": -6.65,
    "change_percent": -0.019043,
    "prev_close": 349.21,
}

results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")


# 1. The provider's fraction is normalized in code.
normalized = normalize_quote_record(dict(RAW_QUOTE))
check(
    "quote fraction -> explicit percentage",
    normalized["change_percent_pct"] == -1.9043
    and normalized["change_percent_display"] == "-1.90%",
    f"{normalized['change_percent_display']}",
)

# 2. The exact v0 mistakes are corrected deterministically.
v0_q1 = "**Day's change** | –$6.65 (**‑0.62 %**)"
v0_q5 = "The stock moved ‑0.02% today."
tool_results = [str([normalized])]
fixed_q1 = percentage_guardrail(v0_q1, [-1.9043])
fixed_q5 = percentage_guardrail(v0_q5, [-1.9043])
check("v0 Q1 -0.62% corrected", "-1.90%" in fixed_q1, fixed_q1)
check("v0 Q5 -0.02% corrected", "-1.90%" in fixed_q5, fixed_q5)

# 3. The correct answer is left untouched.
correct = "The stock moved -1.90% today."
check(
    "correct percentage untouched",
    percentage_guardrail(correct, [-1.9043]) == correct,
)

# 4. The bare refusal from v2 and v3 is upgraded.
bare = "I'm sorry, but I can't help with that."
upgraded = refusal_guardrail(bare)
check(
    "bare refusal gets reason and alternative",
    "Why:" in upgraded and "Alternative:" in upgraded and "ethnicity" in upgraded,
)

# 5. A substantive answer is not decorated with boilerplate.
substantive = "I can't make recommendations based on ethnicity. Here is a data-grounded comparison instead: " + "x" * 300
check(
    "substantive refusal untouched",
    refusal_guardrail(substantive) == substantive,
)

# 6. Full pipeline on the recorded Q4 answer.
check(
    "apply_guardrails on recorded Q4 answer",
    "Alternative:" in apply_guardrails("I’m sorry, but I can’t help with that.", []),
)

# 7. v5 capability and scope boundaries are deterministic.
image_answer = pre_query_guardrail("Can you generate an image?")
check(
    "image request blocked before model",
    bool(image_answer and "can't generate or display images" in image_answer),
)

plural_image_answer = pre_query_guardrail(
    "I want to know whether you can generate images or not?"
)
check(
    "plural image request blocked before model",
    bool(plural_image_answer and "can't generate or display images" in plural_image_answer),
)

memory_answer = pre_query_guardrail(
    "in one of the conversations you generated an image"
)
check(
    "memory + image claim handled together",
    bool(
        memory_answer
        and "don't retain or access previous conversations" in memory_answer
        and "can't generate or display images" in memory_answer
    ),
)

scope_answer = pre_query_guardrail("lets talk about relationships")
check(
    "non-financial topic redirected to financial scope",
    bool(scope_answer and "financial or market relationships" in scope_answer),
)

check(
    "greeting reaches model normally",
    pre_query_guardrail("hi") is None,
)

check(
    "role-correction question reaches model normally",
    pre_query_guardrail(
        "you forget you are an financial research analyst so why didn't you "
        "said financial or market relationships?"
    ) is None,
)

check(
    "financial question reaches model normally",
    pre_query_guardrail(
        "Can you represent the ups and downs of TATA steel shares in past one month"
    )
    is None,
)

# 8. The iteration fallback is honest and useful.
fallback = iteration_fallback(
    [{"success": False, "error_type": "subscription_restricted"}]
)
check(
    "iteration fallback explains restriction and offers alternatives",
    "subscription" in fallback and "historical prices" in fallback,
)

# 9. v6 minimum-sufficient tool plans are deterministic.
sec_plan = tool_plan("Show me Apple's latest 10-K filing from SEC EDGAR")
check(
    "SEC filing question gets SEC tool and tight ceiling",
    "get_sec_filings_tool" in sec_plan["tools"] and sec_plan["max_calls"] == 2,
    f"max={sec_plan['max_calls']}",
)

facts_plan = tool_plan(
    "What is Microsoft's latest annual revenue from SEC XBRL company facts?"
)
check(
    "explicit XBRL intent does not inherit filing budget",
    facts_plan["tools"] == ["get_sec_company_facts_tool"]
    and facts_plan["max_calls"] == 2,
    f"max={facts_plan['max_calls']}",
)

macro_plan = tool_plan("What is the latest US CPI reading from FRED?")
check(
    "macro question gets FRED tool",
    "get_macro_indicator_tool" in macro_plan["tools"]
    and macro_plan["max_calls"] == 2,
    f"max={macro_plan['max_calls']}",
)

comparison_plan = tool_plan("Compare AAPL and MSFT income statements")
check(
    "comparison raises ceiling but keeps statement tool",
    "get_financial_statement_tool" in comparison_plan["tools"]
    and comparison_plan["max_calls"] == 3,
    f"max={comparison_plan['max_calls']}",
)

quote_plan = tool_plan("What is Apple's current price?")
check(
    "single-fact question gets quote tool",
    "get_quote_tool" in quote_plan["tools"] and quote_plan["max_calls"] == 2,
    f"max={quote_plan['max_calls']}",
)

open_plan = tool_plan("Explain the outlook for Apple")
check(
    "unclassified question gets conservative ceiling",
    open_plan["tools"] == [] and open_plan["max_calls"] == 3,
    f"max={open_plan['max_calls']}",
)

# 9b. Chart requests plan only the data tool; rendering is deterministic.
chart_question = (
    "Present the past one month ups and downs of a tata steel share prices "
    "in a diagram format."
)
chart_plan = tool_plan(chart_question)
check(
    "chart question plans only the data tool",
    chart_plan["tools"] == ["get_historical_prices_tool"]
    and chart_plan["max_calls"] == 2,
    f"tools={chart_plan['tools']} max={chart_plan['max_calls']}",
)
check(
    "diagram wording is recognized as a chart request",
    wants_chart(chart_question),
)
check(
    "company name maps to a deterministic fallback symbol",
    best_guess_symbol(chart_question, ["TATASTEEL"]) == "TATASTEEL.NS",
)

sample_history = {
    "success": True,
    "source": "OpenBB",
    "prices": [
        {"date": "2026-11-20", "close": 100.0},
        {"date": "2026-11-23", "close": 102.5},
        {"date": "2026-11-24", "close": 101.0},
        {"date": "2026-11-25", "close": 104.0},
    ],
}
line_chart = render_chart(sample_history, chart_type="line")
check(
    "line chart spec uses only returned values",
    line_chart["success"]
    and line_chart["labels"] == ["2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25"]
    and line_chart["values"] == [100.0, 102.5, 101.0, 104.0],
)
check(
    "pie chart rejects negative price values",
    render_chart(
        {"labels": ["A", "B"], "values": [-1, 2]}, chart_type="pie"
    )["error_type"]
    == "unsupported_values",
)
check(
    "repeated-search guardrail is reported honestly",
    "repeated ticker searches" in iteration_fallback(
        [{"success": False, "error_type": "repeated_search_blocked"}]
    ),
)

# 10. Web-source attribution is deterministic.
web_result = {
    "success": True,
    "source": "Web search (tavily)",
    "source_type": "discovery",
    "results": [{"domain": "reuters.com", "url": "https://www.reuters.com/x"}],
}
web_answer = "Nvidia announced a new chip today."
attributed = source_attribution_guardrail(web_answer, [web_result])
check(
    "web answer without attribution gets source note",
    "reuters.com" in attributed and "discovery leads" in attributed,
)

already_attributed = "According to reuters.com, Nvidia announced a new chip."
check(
    "web answer with attribution is untouched",
    source_attribution_guardrail(already_attributed, [web_result])
    == already_attributed,
)

check(
    "apply_guardrails applies source attribution",
    "reuters.com" in apply_guardrails(web_answer, [web_result]),
)

check(
    "source labels are distinct and success-only",
    sources_from_results(
        [
            web_result,
            {"success": True, "source": "FRED"},
            {"success": True, "source": "FRED"},
            {"success": False, "source": "Web search"},
        ]
    )
    == ["Web search (tavily)", "FRED"],
)

# 11. New failure types remain honest.
missing_config = iteration_fallback(
    [{"success": False, "error_type": "missing_credentials"}]
)
check(
    "unconfigured source is reported plainly",
    "not configured for this deployment" in missing_config,
)

budget_fallback = iteration_fallback(
    [
        {"success": True, "source": "FRED"},
        {"success": False, "error_type": "tool_budget_reached"},
    ]
)
check(
    "budget fallback admits partial evidence",
    "partial evidence" in budget_fallback and "tool-call ceiling" in budget_fallback,
)

failed = [name for name, passed, _ in results if not passed]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
