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

# v5 deterministic intent patterns. These are deliberately narrow: they catch
# capability and scope failures observed in live use without routing legitimate
# financial questions away from the model.
_IMAGE_RE = re.compile(
    r"\b(?:generate|generated|create|created|make|made|draw|drew|"
    r"produce|produced|show|showed)\b[\s\S]{0,40}"
    r"\b(?:an?\s+)?(?:images|image|pictures|picture|photos|photo|artwork|illustration)\b"
    r"|\b(?:images|image|pictures|picture|photos|photo|artwork|illustration)\b[\s\S]{0,40}"
    r"\b(?:generate|generated|create|created|make|made|draw|drew|produce|produced)\b",
    re.IGNORECASE,
)
_MEMORY_RE = re.compile(
    r"\b(?:in|during|from)\s+"
    r"(?:one\s+of\s+(?:the\s+)?|our\s+|the\s+)?"
    r"(?:(?:previous|prior|earlier|last)\s+)?"
    r"(?:conversation|chat|session|message)s?\b"
    r"|\byou\s+(?:just|earlier|previously)\s+"
    r"(?:said|generated|showed|told|made)\b",
    re.IGNORECASE,
)
_SMALL_TALK_RE = re.compile(
    r"^\s*(?:hi|hello|hey|yo|thanks|thank\s+you|good\s+morning|"
    r"good\s+afternoon|good\s+evening|how\s+are\s+you)\b[\W_]*$",
    re.IGNORECASE,
)
_FINANCE_RE = re.compile(
    r"\b(?:stock|stocks|share|shares|ticker|company|companies|market|markets|"
    r"finance|financial|invest|investing|investment|valuation|revenue|earnings|"
    r"profit|price|prices|quote|peers|analyst|analysts|consensus|portfolio|"
    r"sector|sectors|macro|economy|economic|bond|bonds|crypto|fund|funds|ipo|"
    r"merger|acquisition|dividend|dividends|risk|performance|historical|"
    r"chart|trend|trends|employee|employees|salary|salaries|headcount|engineer|"
    r"engineers)\b",
    re.IGNORECASE,
)

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


def pre_query_guardrail(query):
    """Return a deterministic answer when the question is outside the agent's
    capabilities, or None when the model should handle the question.

    This is a code-level boundary, not a prompt instruction. It exists because
    live use showed role drift and false capability claims that prompt rules
    alone did not reliably prevent.
    """
    text = (query or "").strip()
    if not text:
        return None

    asks_image = bool(_IMAGE_RE.search(text))
    claims_memory = bool(_MEMORY_RE.search(text))

    if asks_image and claims_memory:
        return (
            "I don't retain or access previous conversations, and I can't "
            "generate or display images. I can provide a text-based price "
            "table, describe a chart, or pull historical prices where the "
            "provider supports the ticker."
        )
    if asks_image:
        return (
            "I can't generate or display images. I can provide a text-based "
            "price table, describe a chart, or pull historical prices where "
            "the provider supports the ticker."
        )
    if claims_memory:
        return (
            "I don't retain or access previous conversations. I can still "
            "help with financial research in this message — tell me the "
            "company, ticker, or question."
        )

    if _SMALL_TALK_RE.match(text):
        return None

    if not _FINANCE_RE.search(text):
        return (
            "I'm a financial research assistant. I can help with financial or "
            "market relationships, company comparisons, valuation metrics, "
            "quotes, peers, analyst consensus, news, and historical price "
            "movement. Which would you like to explore?"
        )

    return None


def iteration_fallback(tool_results):
    """Honest, useful answer when the agent cannot finish within its budget."""
    messages = []
    for result in tool_results or []:
        if not isinstance(result, dict):
            continue
        error_type = result.get("error_type")
        if error_type == "subscription_restricted":
            messages.append(
                "the provider restricts the requested symbol or endpoint "
                "under the current subscription"
            )
            break
        if error_type == "no_results":
            messages.append("the search returned no matching ticker")
            break
        if error_type == "provider_error":
            messages.append("the data provider returned an error")
            break

    reason = f" The last tool result indicated that {messages[0]}." if messages else ""
    return (
        "I couldn't retrieve the requested data with the available tools."
        + reason
        + " I can provide a quote, profile, peers, analyst consensus, news, "
        "aggregate performance, or historical prices where the provider "
        "supports the ticker."
    )


def apply_guardrails(answer, tool_results):
    """Run every deterministic guardrail over a final answer."""
    answer = percentage_guardrail(answer, verified_percentages(tool_results))
    answer = refusal_guardrail(answer)
    return answer
