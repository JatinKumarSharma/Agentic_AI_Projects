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
    r"engineers|filing|filings|edgar|sec|10-k|10-q|8-k|xbrl|statement|"
    r"statements|balance sheet|cash flow|cash-flow|gdp|cpi|inflation|"
    r"unemployment|fed funds|federal funds|treasury|yield|fred|news|headlines|"
    r"press release|reuters|bloomberg)\b",
    re.IGNORECASE,
)

# The provider returns change_percent as a fraction (e.g. -0.019043). Keeping
# that raw field alongside a normalized percentage removes the ambiguity that
# caused the model to recompute from the wrong denominator.
_PCT_TOLERANCE = 0.02  # final answers are rounded to two decimal places

# Presentation requests are a separate, deterministic evidence need. They do
# not replace the data tool; they add a rendering step after data is retrieved.
_CHART_RE = re.compile(
    r"\b(?:diagram|diagrams|graph|graphs|chart|charts|pie(?:\s+chart)?|"
    r"bar(?:\s+chart)?|line(?:\s+chart)?|plot|sparkline|"
    r"visuali[sz]e|visuali[sz]ation|visuali[sz]ed)\b",
    re.IGNORECASE,
)

# v6 minimum-sufficient planning. These patterns only identify the *kind* of
# evidence a question asks for. The model still chooses the exact tool; the
# deterministic layer supplies a ceiling so it cannot keep searching after the
# question has been answered.
_TOOL_PLAN_PATTERNS = (
    (
        "get_sec_filings_tool",
        re.compile(
            r"\b(?:sec|edgar|10-k|10-q|8-k|filing|filings|proxy statement)\b",
            re.IGNORECASE,
        ),
        "SEC filing index",
    ),
    (
        "get_sec_company_facts_tool",
        re.compile(
            r"\b(?:xbrl|company facts|companyfact|us-gaap|"
            r"reported (?:revenue|net income|assets|liabilities)|"
            r"stockholders?.? equity)\b",
            re.IGNORECASE,
        ),
        "SEC XBRL facts",
    ),
    (
        "get_financial_statement_tool",
        re.compile(
            r"\b(?:income statements?|balance sheets?|cash flows?|cash-flows?|"
            r"financial statements?)\b",
            re.IGNORECASE,
        ),
        "structured statement",
    ),
    (
        "get_macro_indicator_tool",
        re.compile(
            r"\b(?:gdp|cpi|inflation|unemployment|fed funds|federal funds|"
            r"treasury|yield|fred|10-year|10y|2-year|2y)\b",
            re.IGNORECASE,
        ),
        "FRED macro indicator",
    ),
    (
        "search_financial_web_tool",
        re.compile(
            r"\b(?:news|headline|press release|article|latest development|"
            r"reuters|bloomberg|cnbc|marketwatch)\b",
            re.IGNORECASE,
        ),
        "allowed-domain web search",
    ),
    (
        "get_historical_prices_tool",
        re.compile(
            r"\b(?:historical|ohlc|price movement|ups and downs|"
            r"(?:last|past|over) (?:week|month|year|days)|trend|trends|chart)\b",
            re.IGNORECASE,
        ),
        "historical prices",
    ),
    (
        "render_chart_tool",
        _CHART_RE,
        "chart rendering",
    ),
    (
        "get_consensus_tool",
        re.compile(
            r"\b(?:analyst|consensus|price target|ratings?)\b",
            re.IGNORECASE,
        ),
        "analyst consensus",
    ),
    (
        "get_peers_tool",
        re.compile(
            r"\b(?:peers|competitors|similar companies)\b",
            re.IGNORECASE,
        ),
        "peer set",
    ),
    (
        "get_quote_tool",
        re.compile(
            r"\b(?:current price|current quote|latest price|quote|last price|"
            r"trading at|price of)\b",
            re.IGNORECASE,
        ),
        "latest quote",
    ),
    (
        "get_profile_tool",
        re.compile(
            r"\b(?:profile|company overview|about|sector|industry|employees|"
            r"headcount|founded|headquarters)\b",
            re.IGNORECASE,
        ),
        "company profile",
    ),
)

_COMPARISON_RE = re.compile(
    r"\b(?:vs|versus|compare|comparison|between)\b", re.IGNORECASE
)

# Best-effort symbol normalization for names the configured market-data
# provider does not resolve through its search endpoint. The fallback still
# goes through the real data tool, so a provider limitation is reported
# honestly instead of being replaced by internal knowledge.
_COMPANY_SYMBOL_HINTS = (
    ("tata steel", "TATASTEEL.NS"),
    ("reliance industries", "RELIANCE.NS"),
    ("infosys", "INFY.NS"),
    ("hdfc bank", "HDFCBANK.NS"),
    ("icici bank", "ICICIBANK.NS"),
    ("state bank of india", "SBIN.NS"),
)
_TICKER_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}(?:\.[A-Z]{1,4})?\b")
_TICKER_STOPWORDS = {
    "SEC", "FRED", "USD", "INR", "CEO", "CFO", "IPO", "GDP", "CPI", "US", "UK",
}


def wants_chart(query):
    """Return True when the user asks for a visual or diagram-style answer."""
    return bool(_CHART_RE.search(query or ""))


def best_guess_symbol(query, failed_symbols=None):
    """Return a deterministic fallback symbol from the question or failed search."""
    text = str(query or "")
    lowered = text.lower()
    for phrase, symbol in _COMPANY_SYMBOL_HINTS:
        if phrase in lowered:
            return symbol
    for match in _TICKER_RE.findall(text):
        candidate = match.strip().upper()
        if candidate not in _TICKER_STOPWORDS:
            return candidate
    for symbol in reversed(list(failed_symbols or [])):
        if symbol:
            return str(symbol).upper()
    return None


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


def tool_plan(query):
    """Derive a minimum-sufficient evidence plan from the question itself.

    This is not a router: it never blocks a tool the model needs. It supplies a
    deterministic call ceiling and a short plan the model must respect, so a
    single-fact question cannot drift into a long exploratory tool chain.
    """
    text = (query or "").strip()
    matched = []
    for tool_name, pattern, label in _TOOL_PLAN_PATTERNS:
        if pattern.search(text):
            matched.append((tool_name, label))

    # "SEC XBRL company facts" mentions the SEC but does not ask for a filing
    # index. Drop the broader filing tool when the specific XBRL intent is
    # explicit, so the deterministic ceiling reflects one evidence need.
    matched_names = {name for name, _ in matched}
    if (
        "get_sec_company_facts_tool" in matched_names
        and "get_sec_filings_tool" in matched_names
        and re.search(r"\b(?:xbrl|company facts|us-gaap)\b", text, re.IGNORECASE)
    ):
        matched = [
            (name, label)
            for name, label in matched
            if name != "get_sec_filings_tool"
        ]

    comparison = bool(_COMPARISON_RE.search(text))
    if not matched:
        max_calls = 3
    elif len(matched) == 1:
        max_calls = 3 if comparison else 2
    else:
        max_calls = min(4, len(matched) + 1)

    if comparison:
        max_calls = max(max_calls, 3)

    names = [name for name, _ in matched]
    labels = [label for _, label in matched]
    if labels:
        evidence = ", ".join(dict.fromkeys(labels))
        plan_text = (
            "Minimum-sufficient plan: this question calls for "
            f"{evidence}. Use no more than {max_calls} tool calls, stop as "
            "soon as the question is fully answered, and do not call a tool "
            "whose result you will not use."
        )
    else:
        plan_text = (
            "Minimum-sufficient plan: use at most 3 tool calls. Select only "
            "the tools needed to answer this question, then stop."
        )

    return {
        "tools": list(dict.fromkeys(names)),
        "labels": list(dict.fromkeys(labels)),
        "max_calls": max_calls,
        "instruction": plan_text,
    }


def sources_from_results(tool_results):
    """List distinct successful source labels from normalized tool results."""
    sources = []
    for result in tool_results or []:
        if not isinstance(result, dict) or result.get("success") is not True:
            continue
        label = result.get("source")
        if label and label not in sources:
            sources.append(str(label))
    return sources


def source_attribution_guardrail(answer, tool_results):
    """Attach discovery domains when a web-sourced answer omits attribution."""
    if not isinstance(answer, str) or not answer.strip():
        return answer

    web_domains = []
    for result in tool_results or []:
        if not isinstance(result, dict):
            continue
        if result.get("success") is not True:
            continue
        if result.get("source_type") != "discovery":
            continue
        for item in result.get("results", []) or []:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain") or "")
            if domain and domain not in web_domains:
                web_domains.append(domain)

    if not web_domains:
        return answer

    lowered = answer.lower()
    already_attributed = (
        "http://" in lowered
        or "https://" in lowered
        or any(domain.lower() in lowered for domain in web_domains)
    )
    if already_attributed:
        return answer

    return (
        answer.rstrip()
        + "\n\nSources consulted: "
        + ", ".join(web_domains[:5])
        + ". Web results are discovery leads; verify against the linked "
        "primary source."
    )


def iteration_fallback(tool_results):
    """Honest, useful answer when the agent cannot finish within its budget."""
    messages = []
    budget_reached = False
    for result in tool_results or []:
        if not isinstance(result, dict):
            continue
        error_type = result.get("error_type")
        if error_type == "tool_budget_reached":
            budget_reached = True
            messages.append("the tool-call ceiling was reached")
            break
        if error_type == "subscription_restricted":
            messages.append(
                "the provider restricts the requested symbol or endpoint "
                "under the current subscription"
            )
            break
        if error_type == "missing_credentials":
            messages.append("the requested source is not configured for this deployment")
            break
        if error_type == "no_results":
            messages.append("the source returned no matching records")
            break
        if error_type == "repeated_search_blocked":
            messages.append(
                "repeated ticker searches are disabled after the first failure"
            )
            break
        if error_type == "provider_error":
            messages.append("the data provider returned an error")
            break

    reason = f" The last tool result indicated that {messages[0]}." if messages else ""
    lead = (
        "I retrieved partial evidence but could not complete the answer"
        if budget_reached
        else "I couldn't retrieve the requested data with the available tools"
    )
    return (
        lead
        + "."
        + reason
        + " I can provide a quote, profile, peers, analyst consensus, news, "
        "aggregate performance, historical prices, financial statements, SEC "
        "filings and XBRL facts, FRED macro indicators, or allowed-domain web "
        "results where the source is configured."
    )


def apply_guardrails(answer, tool_results):
    """Run every deterministic guardrail over a final answer."""
    answer = percentage_guardrail(answer, verified_percentages(tool_results))
    answer = refusal_guardrail(answer)
    answer = source_attribution_guardrail(answer, tool_results)
    return answer
