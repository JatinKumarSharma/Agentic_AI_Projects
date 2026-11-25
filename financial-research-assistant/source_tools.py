"""Additional source adapters for the financial research assistant.

Every adapter returns a normalized dict with explicit failure types. Keys are
read only from the environment; no credentials or private data are stored in
source code. Web results are marked as discovery leads so guardrails can keep
them subordinate to primary or structured sources.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


_TIMEOUT = 15
_SEC_TICKER_CACHE = {"value": None, "updated_at": 0}
_SEC_FILINGS_CACHE = {}

_DEFAULT_WEB_DOMAINS = (
    "sec.gov",
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "cnbc.com",
    "marketwatch.com",
    "nasdaq.com",
    "finance.yahoo.com",
    "investing.com",
    "morningstar.com",
)

_FRED_SERIES = {
    "gdp": "GDP",
    "cpi": "CPIAUCSL",
    "inflation": "CPIAUCSL",
    "unemployment": "UNRATE",
    "fed funds": "FEDFUNDS",
    "fedfunds": "FEDFUNDS",
    "10y": "DGS10",
    "10 year treasury": "DGS10",
    "2y": "DGS2",
    "2 year treasury": "DGS2",
    "sp500": "SP500",
}

_FRED_LABELS = {
    "GDP": "Real Gross Domestic Product",
    "CPIAUCSL": "Consumer Price Index",
    "UNRATE": "Civilian Unemployment Rate",
    "FEDFUNDS": "Federal Funds Effective Rate",
    "DGS10": "10-Year Treasury Constant Maturity Rate",
    "DGS2": "2-Year Treasury Constant Maturity Rate",
    "SP500": "S&P 500 Index",
}

_STATEMENT_ENDPOINTS = {
    "income": "income",
    "income statement": "income",
    "balance": "balance",
    "balance sheet": "balance",
    # OpenBB 4.x registers the cash-flow router command as `cash`.
    "cash_flow": "cash",
    "cashflow": "cash",
    "cash flow": "cash",
}


def _retrieved_at():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_text(value, limit=400):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _number(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _domain(url):
    return (urlparse(str(url or "")).netloc or "").lower().removeprefix("www.")


def _request_json(url, headers=None, payload=None):
    data = None
    headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    request = Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    with urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _error(source, exc, symbol=None, error_type=None):
    message = str(exc)
    lowered = message.lower()
    if error_type is None:
        restricted = (
            "402" in message
            or "premium" in lowered
            or "upgrade your subscription" in lowered
        )
        credential_problem = (
            "401" in message
            or "403" in message
            or "api key" in lowered
            or "unauthorized" in lowered
            or "forbidden" in lowered
        )
        if restricted and str(source).startswith("OpenBB"):
            error_type = "subscription_restricted"
        elif restricted or credential_problem:
            error_type = "missing_credentials"
        else:
            error_type = "provider_error"
    result = {
        "success": False,
        "source": source,
        "error_type": error_type,
        "message": _clean_text(message, 400),
    }
    if symbol:
        result["symbol"] = str(symbol).upper()
    return result


def _no_results(source, message, symbol=None):
    result = {
        "success": False,
        "source": source,
        "error_type": "no_results",
        "message": message,
    }
    if symbol:
        result["symbol"] = str(symbol).upper()
    return result


def _missing_credentials(source, env_name, symbol=None):
    return _error(
        source,
        f"{env_name} is not configured for this deployment.",
        symbol=symbol,
        error_type="missing_credentials",
    )


def _sec_headers():
    agent = os.getenv("SEC_USER_AGENT", "FinancialResearchAssistant/1.2 (research prototype)")
    return {"User-Agent": agent, "Accept": "application/json"}


def _sec_ticker_map():
    now = time.time()
    cached = _SEC_TICKER_CACHE.get("value")
    if cached and now - _SEC_TICKER_CACHE.get("updated_at", 0) < 86400:
        return cached

    data = _request_json("https://www.sec.gov/files/company_tickers.json", _sec_headers())
    mapping = {}
    for row in data.values():
        ticker = str(row.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        mapping[ticker] = {
            "cik": str(row.get("cik_str", "")).zfill(10),
            "title": str(row.get("title", "")),
        }
    _SEC_TICKER_CACHE["value"] = mapping
    _SEC_TICKER_CACHE["updated_at"] = now
    return mapping


def _sec_company(symbol):
    mapping = _sec_ticker_map()
    return mapping.get(str(symbol or "").upper().strip())


def get_sec_filings(symbol: str, form: str = "10-K", limit: int = 5):
    """Return recent SEC filing metadata and source URLs for a ticker."""
    try:
        limit = max(1, min(int(limit or 5), 10))
    except (TypeError, ValueError):
        limit = 5

    requested_form = str(form or "10-K").upper().strip()
    try:
        company = _sec_company(symbol)
        if not company:
            return _no_results("SEC EDGAR", "The ticker was not found in the SEC company index.", symbol)

        cik = company["cik"]
        cache_key = cik
        cached = _SEC_FILINGS_CACHE.get(cache_key)
        if cached and time.time() - cached["updated_at"] < 3600:
            submissions = cached["value"]
        else:
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            submissions = _request_json(url, _sec_headers())
            _SEC_FILINGS_CACHE[cache_key] = {"value": submissions, "updated_at": time.time()}

        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        accessions = recent.get("accessionNumber", [])
        documents = recent.get("primaryDocument", [])

        filings = []
        for index, filing_form in enumerate(forms):
            if requested_form != "ALL" and str(filing_form).upper() != requested_form:
                continue
            accession = str(accessions[index])
            document = str(documents[index])
            normalized_accession = accession.replace("-", "")
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{normalized_accession}/{document}"
            )
            filings.append(
                {
                    "form": str(filing_form),
                    "filing_date": str(filing_dates[index]),
                    "report_date": str(report_dates[index]),
                    "accession_number": accession,
                    "primary_document": document,
                    "url": url,
                }
            )
            if len(filings) >= limit:
                break

        if not filings:
            return _no_results(
                "SEC EDGAR",
                f"No {requested_form} filings were returned for this ticker.",
                symbol,
            )

        return {
            "success": True,
            "source": "SEC EDGAR",
            "source_type": "primary_filing_index",
            "source_url": f"https://www.sec.gov/edgar/browse/?CIK={cik}",
            "retrieved_at": _retrieved_at(),
            "symbol": str(symbol).upper(),
            "cik": cik,
            "company": company["title"],
            "requested_form": requested_form,
            "filings": filings,
        }
    except Exception as exc:
        return _error("SEC EDGAR", exc, symbol)


_REVENUE_CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)


def _xbrl_concept_candidates(resolved: str):
    """Return equivalent us-gaap concepts, newest reporting standards first.

    Large issuers change their primary revenue tag over time. Microsoft, for
    example, reports recent revenue under
    RevenueFromContractWithCustomerExcludingAssessedTax while older filings use
    Revenues. Checking one canonical tag would silently return stale data.
    """
    if resolved.lower() == "revenues":
        return list(_REVENUE_CONCEPTS)
    return [resolved]


def get_sec_company_facts(symbol: str, concept: str = "Revenues", limit: int = 6):
    """Return recent SEC XBRL company facts for a standard us-gaap concept."""
    aliases = {
        "revenue": "Revenues",
        "revenues": "Revenues",
        "net income": "NetIncomeLoss",
        "netincome": "NetIncomeLoss",
        "assets": "Assets",
        "liabilities": "Liabilities",
        "cash": "CashAndCashEquivalentsAtCarryingValue",
        "equity": "StockholdersEquity",
    }
    requested = str(concept or "Revenues").strip()
    resolved = aliases.get(requested.lower(), requested)
    try:
        limit = max(1, min(int(limit or 6), 12))
    except (TypeError, ValueError):
        limit = 6

    try:
        company = _sec_company(symbol)
        if not company:
            return _no_results("SEC EDGAR", "The ticker was not found in the SEC company index.", symbol)

        cik = company["cik"]
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        data = _request_json(url, _sec_headers())
        gaap = data.get("facts", {}).get("us-gaap", {})

        candidates = _xbrl_concept_candidates(resolved)
        available = []
        for candidate in candidates:
            for name in gaap:
                if name.lower() == candidate.lower() and name not in available:
                    available.append(name)
                    break

        if not available:
            suggestions = [name for name in gaap if resolved.lower()[:5] in name.lower()][:12]
            return {
                "success": False,
                "source": "SEC EDGAR",
                "error_type": "no_results",
                "message": f"The XBRL concept '{resolved}' was not found for this company.",
                "symbol": str(symbol).upper(),
                "available_concepts": suggestions,
            }

        rows = []
        seen_periods = set()
        seen_values = set()
        concepts_used = []
        unit_name = None

        for actual in available:
            units = gaap[actual].get("units", {})
            current_unit = "USD" if "USD" in units else next(iter(units), None)
            if not current_unit:
                continue
            if unit_name is None:
                unit_name = current_unit

            for row in units[current_unit]:
                start = row.get("start")
                end = row.get("end")
                form = row.get("form")
                period_key = (start, end, form)
                if period_key in seen_periods:
                    continue
                value = _number(row.get("val"))
                value_key = (start, end, value, form)
                if value_key in seen_values:
                    continue
                seen_periods.add(period_key)
                seen_values.add(value_key)
                if actual not in concepts_used:
                    concepts_used.append(actual)
                rows.append(
                    {
                        "concept": actual,
                        "start": start,
                        "end": end,
                        "value": value,
                        "fy": row.get("fy"),
                        "fp": row.get("fp"),
                        "form": form,
                        "accession_number": row.get("accn"),
                    }
                )

        if not rows:
            return _no_results("SEC EDGAR", "The concept returned no usable facts.", symbol)

        rows.sort(key=lambda item: str(item.get("end") or ""), reverse=True)
        rows = rows[:limit]

        return {
            "success": True,
            "source": "SEC EDGAR",
            "source_type": "xbrl_company_facts",
            "source_url": url,
            "retrieved_at": _retrieved_at(),
            "symbol": str(symbol).upper(),
            "concept": rows[0]["concept"],
            "concepts_used": concepts_used,
            "unit": unit_name,
            "facts": rows,
        }
    except Exception as exc:
        return _error("SEC EDGAR", exc, symbol)


def get_macro_indicator(indicator: str, start_date: str = "", end_date: str = ""):
    """Return recent FRED observations for a supported macro indicator."""
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        return _missing_credentials("FRED", "FRED_API_KEY")

    requested = str(indicator or "").strip().lower()
    series_id = _FRED_SERIES.get(requested)
    if not series_id:
        candidate = str(indicator or "").strip().upper()
        if re.fullmatch(r"[A-Z0-9_]{1,30}", candidate):
            series_id = candidate
    if not series_id:
        return _no_results("FRED", "The indicator could not be mapped to a FRED series.")

    params = {"series_id": series_id, "api_key": api_key, "file_type": "json"}
    if start_date:
        params["observation_start"] = str(start_date)
    if end_date:
        params["observation_end"] = str(end_date)

    try:
        url = "https://api.stlouisfed.org/fred/series/observations?" + urlencode(params)
        data = _request_json(url)
        observations = []
        for row in data.get("observations", []):
            raw = row.get("value")
            if raw in (".", "", None):
                continue
            value = _number(raw)
            if value is None:
                continue
            observations.append({"date": row.get("date"), "value": value})

        if not observations:
            return _no_results("FRED", "FRED returned no observations for that series.")

        latest = observations[-1]
        previous = observations[-2] if len(observations) > 1 else None
        change = round(latest["value"] - previous["value"], 4) if previous else None
        change_pct = None
        if previous and previous["value"]:
            change_pct = round((change / previous["value"]) * 100, 2)

        return {
            "success": True,
            "source": "FRED",
            "source_type": "macro_indicator",
            "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
            "retrieved_at": _retrieved_at(),
            "series_id": series_id,
            "title": _FRED_LABELS.get(series_id, series_id),
            "latest": latest,
            "previous": previous,
            "change_from_previous": change,
            "change_from_previous_pct": change_pct,
            "observations": observations[-12:],
        }
    except Exception as exc:
        return _error("FRED", exc)


def _openbb():
    """Import OpenBB only when a statement tool is actually called."""
    from openbb import obb

    return obb


def _records_from_openbb(result):
    if result is None:
        return []
    try:
        frame = result.to_df()
        if frame.empty:
            return []
        # Statement dates are commonly stored in the index. Reset it so the
        # model receives period labels explicitly rather than losing them.
        frame = frame.reset_index()
        records = frame.to_dict(orient="records")
        return records if records else []
    except Exception:
        text = str(result)
        if "results=[]" in text or "results = []" in text:
            return []
        raise


def get_financial_statement(symbol: str, statement: str = "income", period: str = "annual", limit: int = 4):
    """Return structured income, balance-sheet, or cash-flow statement rows."""
    canonical = _STATEMENT_ENDPOINTS.get(str(statement or "").strip().lower())
    if not canonical:
        return _no_results(
            "OpenBB / FMP",
            "Use income, balance sheet, or cash flow as the statement type.",
            symbol,
        )

    try:
        limit = max(1, min(int(limit or 4), 8))
    except (TypeError, ValueError):
        limit = 4
    selected_period = "quarter" if str(period or "").lower() in ("quarter", "quarterly") else "annual"

    try:
        endpoint = getattr(_openbb().equity.fundamental, canonical, None)
        if endpoint is None:
            return {
                "success": False,
                "source": "OpenBB / FMP",
                "error_type": "provider_error",
                "message": f"The {canonical} endpoint is unavailable in this OpenBB build.",
                "symbol": str(symbol).upper(),
            }

        result = endpoint(symbol=symbol, period=selected_period, limit=limit)
        records = _records_from_openbb(result)
        if not records:
            return _no_results(
                "OpenBB / FMP",
                "The provider returned no statement records for this symbol.",
                symbol,
            )

        return {
            "success": True,
            "source": "OpenBB / FMP",
            "source_type": "structured_statement",
            "source_url": "https://openbb.co/",
            "retrieved_at": _retrieved_at(),
            "symbol": str(symbol).upper(),
            "statement": canonical,
            "period": selected_period,
            "records": records,
        }
    except Exception as exc:
        return _error("OpenBB / FMP", exc, symbol)


_WEB_PROVIDER_ENV = {
    "tavily": "TAVILY_API_KEY",
    "brave": "BRAVE_API_KEY",
    "serper": "SERPER_API_KEY",
}
_WEB_PROVIDER_ORDER = ("tavily", "brave", "serper")


def _web_provider_candidates():
    """Return configured providers in priority order, with keys available."""
    configured = os.getenv("WEB_SEARCH_PROVIDER", "").strip().lower()
    candidates = []
    if configured in _WEB_PROVIDER_ENV and os.getenv(_WEB_PROVIDER_ENV[configured]):
        candidates.append(configured)
    for provider in _WEB_PROVIDER_ORDER:
        if (
            provider not in candidates
            and os.getenv(_WEB_PROVIDER_ENV[provider])
        ):
            candidates.append(provider)
    return candidates


def _web_provider():
    candidates = _web_provider_candidates()
    if candidates:
        return candidates[0]
    configured = os.getenv("WEB_SEARCH_PROVIDER", "").strip().lower()
    return configured or None


def _allowed_web_domains():
    configured = os.getenv("WEB_SEARCH_ALLOWED_DOMAINS", "")
    if configured:
        return tuple(
            item.strip().lower().removeprefix("www.")
            for item in configured.split(",")
            if item.strip()
        )
    return _DEFAULT_WEB_DOMAINS


def _is_allowed_domain(url):
    domain = _domain(url)
    if not domain:
        return False
    return any(
        domain == allowed or domain.endswith("." + allowed)
        for allowed in _allowed_web_domains()
    )


def _tavily_results(query, limit, api_key):
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": min(limit * 2, 10),
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
    }
    data = _request_json("https://api.tavily.com/search", payload=payload)
    for item in data.get("results", []):
        yield {
            "title": _clean_text(item.get("title"), 200),
            "url": item.get("url"),
            "snippet": _clean_text(item.get("content"), 400),
            "published_date": item.get("published_date"),
        }


def _brave_results(query, limit, api_key):
    params = {"q": query, "count": min(limit * 2, 10)}
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(params)
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    data = _request_json(url, headers=headers)
    for item in data.get("web", {}).get("results", []):
        yield {
            "title": _clean_text(item.get("title"), 200),
            "url": item.get("url"),
            "snippet": _clean_text(item.get("description"), 400),
            "published_date": item.get("page_age"),
        }


def _serper_results(query, limit, api_key):
    payload = {"q": query, "num": min(limit * 2, 10)}
    headers = {"X-API-KEY": api_key}
    data = _request_json("https://google.serper.dev/search", headers=headers, payload=payload)
    for item in data.get("organic", []):
        yield {
            "title": _clean_text(item.get("title"), 200),
            "url": item.get("link"),
            "snippet": _clean_text(item.get("snippet"), 400),
            "published_date": item.get("date"),
        }


def search_financial_web(query: str, limit: int = 5):
    """Find financial news or research from allowed public web domains."""
    query = _clean_text(query, 300)
    if len(query) < 3:
        return _no_results("Web search", "Provide a more specific search query.")

    try:
        limit = max(1, min(int(limit or 5), 5))
    except (TypeError, ValueError):
        limit = 5

    candidates = _web_provider_candidates()
    if not candidates:
        return _missing_credentials(
            "Web search", "WEB_SEARCH_PROVIDER / search API key"
        )

    attempted = []
    last_failure = None

    # Bounded failover: one configured/available provider at a time. A provider
    # that errors or returns no allowlisted results hands off to the next one.
    for provider in candidates:
        api_key = os.getenv(_WEB_PROVIDER_ENV[provider])
        if not api_key:
            continue
        attempted.append(provider)

        try:
            if provider == "tavily":
                iterator = _tavily_results(query, limit, api_key)
            elif provider == "brave":
                iterator = _brave_results(query, limit, api_key)
            else:
                iterator = _serper_results(query, limit, api_key)

            results = []
            for item in iterator:
                url = str(item.get("url") or "")
                if not url.startswith("http://") and not url.startswith("https://"):
                    continue
                if not _is_allowed_domain(url):
                    continue
                results.append(
                    {
                        "title": item.get("title"),
                        "url": url,
                        "domain": _domain(url),
                        "snippet": item.get("snippet"),
                        "published_date": item.get("published_date"),
                    }
                )
                if len(results) >= limit:
                    break

            if not results:
                last_failure = _no_results(
                    f"Web search ({provider})",
                    "No results were returned from the allowed public source domains.",
                )
                continue

            search_base = {
                "tavily": "https://www.google.com/search?q=",
                "brave": "https://search.brave.com/search?q=",
                "serper": "https://www.google.com/search?q=",
            }.get(provider, "https://www.google.com/search?q=")

            return {
                "success": True,
                "source": f"Web search ({provider})",
                "source_type": "discovery",
                "source_tier": "discovery",
                "requires_corroboration": True,
                "source_url": search_base + urlencode({"q": query}),
                "retrieved_at": _retrieved_at(),
                "provider": provider,
                "attempted_providers": attempted,
                "query": query,
                "results": results,
            }
        except Exception as exc:
            last_failure = _error(f"Web search ({provider})", exc)

    if last_failure is None:
        last_failure = _no_results(
            "Web search", "No configured web-search provider returned a result."
        )
    last_failure["attempted_providers"] = attempted
    return last_failure


def source_status():
    """Return configured source capabilities without exposing any secrets."""
    return {
        "sec_edgar": True,
        "fred": bool(os.getenv("FRED_API_KEY")),
        "web_search_provider": _web_provider(),
        "web_search_domains": list(_allowed_web_domains()),
    }
