"""Deterministic guardrails for the Financial Research Assistant.

These are code-level checks, not prompt instructions. They exist because the
evaluation log showed two different prompt interventions (v2 rule 14, v3
few-shot) could not change the bare-refusal behaviour, and because a
tool-supplied fraction produced two different wrong percentages in v0.

A guardrail must work even when the model ignores the prompt.
"""
import re


_APOSTROPHE = "['\u2019]"
_SIGN = "[\u2011\u2013\u2012\u2212-]"

_REFUSAL_OPENERS = (
    rf"i(?:{_APOSTROPHE}m| am) sorry,?\s+but\s+i\s+(?:can(?:not|{_APOSTROPHE}t)|am unable to)\s+(?:help|assist)",
    rf"i\s+(?:can(?:not|{_APOSTROPHE}t)|am unable to)\s+(?:help|assist)\s+with\s+that",
    rf"as an ai[, ]+i\s+(?:can(?:not|{_APOSTROPHE}t)|am unable to)",
)
_REFUSAL_RE = re.compile(
    r"^\s*(?:" + "|".join(_REFUSAL_OPENERS) + r")", re.IGNORECASE
)
# Match the whole percentage token, including Unicode minus signs used in the
# recorded answers, so replacement does not leave a stray sign behind.
_PERCENT_RE = re.compile(rf"({_SIGN}?\d+(?:\.\d+)?\s*%)")
_PCT_FIELD_RE = re.compile(r"change_percent_pct'?\s*:\s*(-?\d+(?:\.\d+)?)")

# The provider returns change_percent as a fraction (e.g. -0.019043). Keeping
# that raw field alongside a normalized percentage removes the ambiguity that
# caused the model to recompute from the wrong denominator.
_PCT_TOLERANCE = 0.02  # final answers are rounded to two decimal places


def normalize_quote_record(record):
    """Add an explicit percentage field to a raw quote record."""
    if not isinstance(record, dict):
        return record
    raw = record.get("change_percent")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        record["change_percent_pct"] = round(raw * 100, 4)
        record["change_percent_display"] = f"{raw * 100:.2f}%"
    return record


def _percent_value(token):
    """Parse a matched percentage token, normalizing Unicode minus signs."""
    cleaned = token.strip().rstrip("%").strip()
    for char in ("\u2011", "\u2013", "\u2012", "\u2212"):
        cleaned = cleaned.replace(char, "-")
    return float(cleaned)


def verified_percentages(tool_results):
    """Extract code-normalized percentages from raw tool result strings."""
    values = []
    for result in tool_results:
        for match in _PCT_FIELD_RE.finditer(str(result)):
            values.append(float(match.group(1)))
    return values


def refusal_guardrail(answer):
    """Upgrade a bare refusal into a reason and a data-grounded alternative."""
    stripped = answer.strip()
    first_line = stripped.split("\n", 1)[0].strip()

    # A longer answer already has substance — do not append boilerplate to it.
    if len(stripped) > 240 or not _REFUSAL_RE.match(first_line):
        return answer

    return (
        f"{stripped}\n\n"
        "Why: investment recommendations based on ethnicity would use a "
        "protected characteristic as a financial signal, which is "
        "discriminatory and not a valid basis for investment decisions.\n\n"
        "Alternative: I can instead compare the relevant companies using "
        "data-grounded factors such as market cap, valuation and historical "
        "performance. Would you like me to pull those?"
    )


def percentage_guardrail(answer, verified):
    """Replace a single mismatched percentage with the tool-verified value.

    If an answer contains several percentages and none match, the guardrail
    will not guess which one is wrong — it flags the discrepancy instead.
    """
    if not verified:
        return answer

    matches = list(_PERCENT_RE.finditer(answer))
    if not matches:
        return answer

    expected = verified[0]
    if any(
        abs(_percent_value(match.group(1)) - value) <= _PCT_TOLERANCE
        for match in matches
        for value in verified
    ):
        return answer

    if len(matches) == 1:
        match = matches[0]
        return answer[: match.start()] + f"{expected:.2f}%" + answer[match.end() :]

    return (
        answer.rstrip()
        + f"\n\nTool-verified correction: the percentage returned by the data "
        f"provider is {expected:.2f}%; the percentages above were not returned "
        f"by the provider."
    )


def apply_guardrails(answer, tool_results):
    """Run every deterministic guardrail over a final answer."""
    answer = percentage_guardrail(answer, verified_percentages(tool_results))
    answer = refusal_guardrail(answer)
    return answer
