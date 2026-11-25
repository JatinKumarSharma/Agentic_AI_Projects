"""Offline normalization tests for the v6 source adapters.

Run from the project root:
    python eval-runs/source_tools_test.py

No model, provider key, or network call is required. Fake responses exercise
the exact normalization paths used by the real adapters.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import source_tools

results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")


# 1. Domain allowlisting is normalized and strict.
check(
    "www prefix and case are normalized",
    source_tools._domain("https://www.Reuters.com/article") == "reuters.com",
)
check(
    "allowed domain passes",
    source_tools._is_allowed_domain("https://www.reuters.com/article"),
)
check(
    "unknown domain is blocked",
    not source_tools._is_allowed_domain("https://evil.example/article"),
)

# 2. Failure classification is source-aware.
check(
    "auth failure becomes missing_credentials",
    source_tools._error("FRED", "HTTP 401 unauthorized")["error_type"]
    == "missing_credentials",
)
check(
    "FMP restriction stays subscription_restricted",
    source_tools._error(
        "OpenBB / FMP", "HTTP 402 premium query parameter"
    )["error_type"]
    == "subscription_restricted",
)

# 3. Missing FRED key is reported without a network call.
with patch.dict(os.environ, {}, clear=True):
    no_key = source_tools.get_macro_indicator("cpi")
check(
    "missing FRED key is explicit",
    no_key["success"] is False
    and no_key["error_type"] == "missing_credentials"
    and "FRED_API_KEY" in no_key["message"],
)

# 4. FRED aliases map to a real series and normalize observations.
fred_response = {
    "observations": [
        {"date": "2026-10-01", "value": "310.1"},
        {"date": "2026-11-01", "value": "."},
        {"date": "2026-11-02", "value": "311.4"},
    ]
}
with patch.dict(os.environ, {"FRED_API_KEY": "dummy-for-test"}, clear=False):
    with patch.object(source_tools, "_request_json", return_value=fred_response):
        macro = source_tools.get_macro_indicator("cpi")
check(
    "CPI alias resolves to CPIAUCSL",
    macro["success"] is True and macro["series_id"] == "CPIAUCSL",
)
check(
    "missing observations are skipped and change is computed",
    len(macro["observations"]) == 2
    and macro["latest"]["value"] == 311.4
    and macro["change_from_previous"] == 1.3,
)

# 5. SEC filing index returns a direct EDGAR document URL.
sec_company = {"cik": "0000320193", "title": "Apple Inc."}
submissions = {
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q", "8-K"],
            "filingDate": ["2025-10-31", "2026-02-01", "2026-08-01"],
            "reportDate": ["2025-09-27", "2025-12-27", "2026-07-30"],
            "accessionNumber": [
                "0000320193-25-000106",
                "0000320193-26-000010",
                "0000320193-26-000070",
            ],
            "primaryDocument": ["aapl-20250927.htm", "aapl-20251227.htm", "aapl-8k.htm"],
        }
    }
}
source_tools._SEC_FILINGS_CACHE.clear()
with patch.object(source_tools, "_sec_ticker_map", return_value={"AAPL": sec_company}):
    with patch.object(source_tools, "_request_json", return_value=submissions):
        filings = source_tools.get_sec_filings("AAPL", "10-K", 2)
check(
    "SEC filing result is a primary filing index",
    filings["success"] is True
    and filings["source"] == "SEC EDGAR"
    and filings["source_type"] == "primary_filing_index",
)
check(
    "SEC filing URL is built from accession and document",
    filings["filings"][0]["url"].startswith(
        "https://www.sec.gov/Archives/edgar/data/320193/"
    )
    and filings["filings"][0]["form"] == "10-K",
)

# 6. SEC XBRL facts are deduplicated and sorted by period end.
companyfacts = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "start": "2024-09-29",
                            "end": "2025-09-27",
                            "val": 416161000000,
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K",
                            "accn": "0000320193-25-000106",
                        },
                        {
                            "start": "2024-09-29",
                            "end": "2025-09-27",
                            "val": 416161000000,
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K",
                            "accn": "0000320193-25-000106",
                        },
                    ]
                }
            }
        }
    }
}
with patch.object(source_tools, "_sec_ticker_map", return_value={"AAPL": sec_company}):
    with patch.object(source_tools, "_request_json", return_value=companyfacts):
        facts = source_tools.get_sec_company_facts("AAPL", "revenue", 4)
check(
    "SEC company facts resolve the revenue alias",
    facts["success"] is True and facts["concept"] == "Revenues",
)
check(
    "duplicate XBRL facts are removed",
    len(facts["facts"]) == 1 and facts["facts"][0]["value"] == 416161000000.0,
)

# 6b. Revenue falls forward to the newer us-gaap concept used by MSFT.
msft_company = {"cik": "0000789019", "title": "Microsoft Corporation"}
mixed_facts = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "start": "2010-07-01",
                            "end": "2011-06-30",
                            "val": 62484000000,
                            "fy": 2011,
                            "fp": "FY",
                            "form": "10-K",
                            "accn": "old",
                        }
                    ]
                }
            },
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        {
                            "start": "2024-07-01",
                            "end": "2025-06-30",
                            "val": 281724000000,
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K",
                            "accn": "new",
                        }
                    ]
                }
            },
        }
    }
}
with patch.object(source_tools, "_sec_ticker_map", return_value={"MSFT": msft_company}):
    with patch.object(source_tools, "_request_json", return_value=mixed_facts):
        mixed = source_tools.get_sec_company_facts("MSFT", "revenue", 4)
check(
    "revenue uses the newest available SEC concept",
    mixed["success"] is True
    and mixed["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    and mixed["facts"][0]["value"] == 281724000000.0,
)
check(
    "mixed concepts are disclosed in the result",
    len(mixed["concepts_used"]) == 2 and "Revenues" in mixed["concepts_used"],
)

# 7. Statement rows keep their period labels.
class FakeFrame:
    empty = False

    def reset_index(self):
        return self

    def to_dict(self, orient):
        return [{"date": "2025-09-27", "revenue": 416161000000}]


class FakeStatement:
    def to_df(self):
        return FakeFrame()


class FakeFundamental:
    def income(self, symbol, period, limit):
        return FakeStatement()

    def cash(self, symbol, period, limit):
        return FakeStatement()


class FakeEquity:
    fundamental = FakeFundamental()


class FakeObb:
    equity = FakeEquity()


with patch.object(source_tools, "_openbb", return_value=FakeObb()):
    statement = source_tools.get_financial_statement(
        "AAPL", "income statement", "annual", 2
    )
check(
    "statement rows are normalized with source metadata",
    statement["success"] is True
    and statement["statement"] == "income"
    and statement["records"][0]["revenue"] == 416161000000,
)
with patch.object(source_tools, "_openbb", return_value=FakeObb()):
    cash_flow = source_tools.get_financial_statement("AAPL", "cash flow", "annual", 2)
check(
    "cash flow alias resolves to OpenBB cash endpoint",
    cash_flow["success"] is True and cash_flow["statement"] == "cash",
)
check(
    "invalid statement type fails explicitly",
    source_tools.get_financial_statement("AAPL", "random")["error_type"]
    == "no_results",
)

# 8. Web results are filtered to the allowlist and marked as discovery.
web_response = {
    "results": [
        {
            "title": "Nvidia earnings",
            "url": "https://www.reuters.com/business/nvidia",
            "content": "A short Reuters snippet.",
            "published_date": "2026-11-24",
        },
        {
            "title": "Untrusted",
            "url": "https://evil.example/nvidia",
            "content": "Should be filtered out.",
        },
    ]
}
with patch.dict(
    os.environ,
    {"WEB_SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "dummy-for-test"},
    clear=False,
):
    with patch.object(source_tools, "_request_json", return_value=web_response):
        web = source_tools.search_financial_web("Nvidia earnings", 3)
check(
    "web tool returns only allowed domains",
    web["success"] is True
    and len(web["results"]) == 1
    and web["results"][0]["domain"] == "reuters.com",
)
check(
    "web results are marked as discovery leads",
    web["source_type"] == "discovery"
    and web["requires_corroboration"] is True
    and "google.com" in web["source_url"],
)

# 8b. A failed provider hands off to the next configured provider.
brave_response = {
    "web": {
        "results": [
            {
                "title": "Nvidia earnings",
                "url": "https://www.reuters.com/business/nvidia",
                "description": "A short Reuters snippet.",
                "page_age": "2026-11-24",
            }
        ]
    }
}


def fake_provider_request(url, headers=None, payload=None):
    if "api.tavily.com" in url:
        raise RuntimeError("HTTP 503 service unavailable")
    if "api.search.brave.com" in url:
        return brave_response
    return web_response


with patch.dict(
    os.environ,
    {
        "WEB_SEARCH_PROVIDER": "tavily",
        "TAVILY_API_KEY": "dummy-for-test",
        "BRAVE_API_KEY": "dummy-for-test",
    },
    clear=False,
):
    with patch.object(source_tools, "_request_json", side_effect=fake_provider_request):
        failed_over = source_tools.search_financial_web("Nvidia earnings", 3)
check(
    "web provider fails over when the first provider errors",
    failed_over["success"] is True
    and failed_over["provider"] == "brave"
    and failed_over["attempted_providers"] == ["tavily", "brave"],
)

# 9. Status reports configuration without printing key material.
with patch.dict(
    os.environ,
    {"FRED_API_KEY": "dummy-for-test", "WEB_SEARCH_PROVIDER": "tavily"},
    clear=False,
):
    status = source_tools.source_status()
check(
    "source status exposes booleans and provider names only",
    status["sec_edgar"] is True
    and status["fred"] is True
    and status["web_search_provider"] == "tavily"
    and all("dummy-for-test" not in str(value) for value in status.values()),
)

failed = [name for name, passed, _ in results if not passed]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
