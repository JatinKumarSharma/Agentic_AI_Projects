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
    iteration_fallback,
    normalize_quote_record,
    percentage_guardrail,
    pre_query_guardrail,
    refusal_guardrail,
)

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

failed = [name for name, passed, _ in results if not passed]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
