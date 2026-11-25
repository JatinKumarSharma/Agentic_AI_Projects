import os
from datetime import date, timedelta

from openbb import obb

from guardrails import normalize_quote_record


FMP_API_KEY = os.getenv("FMP_API_KEY")

if FMP_API_KEY:
    obb.user.credentials.fmp_api_key = FMP_API_KEY


def _no_results(symbol):
    return {
        "success": False,
        "symbol": symbol,
        "error_type": "no_results",
        "message": "The provider returned no matching records for this request.",
    }


def _error_result(exc, symbol):
    """Normalize provider exceptions into a stable shape for the model."""
    message = str(exc)
    lowered = message.lower()
    if (
        "402" in message
        or "premium query parameter" in lowered
        or "unauthorized" in lowered
    ):
        return {
            "success": False,
            "symbol": symbol,
            "error_type": "subscription_restricted",
            "message": (
                "The provider restricts this symbol or endpoint under the "
                "current subscription."
            ),
        }
    return {
        "success": False,
        "symbol": symbol,
        "error_type": "provider_error",
        "message": message[:400],
    }


def _records_or_error(result, symbol):
    """Convert an OpenBB result to records, or return a structured failure."""
    if result is None:
        return _no_results(symbol)

    try:
        frame = result.to_df()
        if frame.empty:
            return _no_results(symbol)
        if frame.index.name:
            frame = frame.reset_index()
        records = frame.to_dict(orient="records")
    except Exception:
        text = str(result)
        if "results=[]" in text or "results = []" in text:
            return _no_results(symbol)
        return {
            "success": False,
            "symbol": symbol,
            "error_type": "provider_error",
            "message": text[:400],
        }

    return records if records else _no_results(symbol)


def _number(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def search_stock(symbol: str):
    """Search for a stock ticker and return matching companies."""
    try:
        result = obb.equity.search(symbol)
    except Exception as exc:
        return _error_result(exc, symbol)
    return _records_or_error(result, symbol)


def get_profile(symbol: str):
    """Get company profile information for a stock ticker."""
    try:
        result = obb.equity.profile(symbol)
    except Exception as exc:
        return _error_result(exc, symbol)
    return _records_or_error(result, symbol)


def get_peers(symbol: str):
    """Get peer companies for a stock ticker."""
    try:
        result = obb.equity.compare.peers(symbol)
    except Exception as exc:
        return _error_result(exc, symbol)
    return _records_or_error(result, symbol)


def get_quote(symbol: str):
    """Get the latest stock quote.

    change_percent is normalized in code before the model sees it — the
    provider returns a fraction, which previously led to two different wrong
    percentages in the final answers.
    """
    try:
        result = obb.equity.price.quote(symbol=symbol)
    except Exception as exc:
        return _error_result(exc, symbol)

    records = _records_or_error(result, symbol)
    if isinstance(records, dict) and records.get("success") is False:
        return records
    return [normalize_quote_record(record) for record in records]


def get_performance(symbol: str):
    """Get stock price performance."""
    try:
        result = obb.equity.price.performance(symbol=symbol)
    except Exception as exc:
        return _error_result(exc, symbol)
    return _records_or_error(result, symbol)


def get_historical_prices(symbol: str, days: int = 30):
    """Get daily OHLC prices for roughly the last N calendar days.

    The result is a compact summary plus the individual price rows. The
    provider's own per-day change_percent is deliberately omitted so the model
    cannot confuse it with the explicitly computed period_change_pct.
    """
    try:
        days = max(1, min(int(days or 30), 365))
    except (TypeError, ValueError):
        days = 30

    end = date.today()
    start = end - timedelta(days=days + 10)

    try:
        result = obb.equity.price.historical(
            symbol=symbol,
            start_date=str(start),
            end_date=str(end),
        )
    except Exception as exc:
        return _error_result(exc, symbol)

    records = _records_or_error(result, symbol)
    if isinstance(records, dict) and records.get("success") is False:
        return records

    prices = []
    for record in records:
        row = {
            "date": str(record.get("date", ""))[:10],
            "open": _number(record.get("open")),
            "high": _number(record.get("high")),
            "low": _number(record.get("low")),
            "close": _number(record.get("close")),
            "volume": _number(record.get("volume")),
        }
        if row["close"] is not None:
            prices.append(row)

    if not prices:
        return _no_results(symbol)

    prices = prices[-days:]
    closes = [row["close"] for row in prices]
    lows = [row["low"] for row in prices if row["low"] is not None]
    highs = [row["high"] for row in prices if row["high"] is not None]
    first, last = closes[0], closes[-1]
    change = round(((last - first) / first) * 100, 2) if first else None

    return {
        "success": True,
        "symbol": symbol,
        "observations": len(prices),
        "period_start": prices[0]["date"],
        "period_end": prices[-1]["date"],
        "first_close": first,
        "last_close": last,
        "period_change_pct": change,
        "period_low": min(lows) if lows else None,
        "period_high": max(highs) if highs else None,
        "prices": prices,
    }


def get_consensus(symbol: str):
    """Get analyst consensus price target."""
    try:
        result = obb.equity.estimates.consensus(symbol=symbol)
        if not result.results:
            return _no_results(symbol)
        data = result.results[0]
        return {
            "success": True,
            "symbol": data.symbol,
            "target_high": _number(data.target_high),
            "target_low": _number(data.target_low),
            "target_consensus": _number(data.target_consensus),
            "target_median": _number(data.target_median),
        }
    except Exception as exc:
        return _error_result(exc, symbol)


def get_company_news(symbol: str):
    """Get recent company news."""
    try:
        result = obb.news.company(symbol)
    except Exception as exc:
        return _error_result(exc, symbol)
    return _records_or_error(result, symbol)
