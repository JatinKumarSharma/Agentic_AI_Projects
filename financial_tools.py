from openbb import obb


def search_stock(symbol: str):
    """Search for a stock ticker and return matching companies."""

    result = obb.equity.search(symbol)

    try:
        return result.to_df().to_dict(orient="records")
    except Exception:
        return str(result)


def get_profile(symbol: str):
    """Get company profile information for a stock ticker."""

    result = obb.equity.profile(symbol)

    try:
        return result.to_df().to_dict(orient="records")
    except Exception:
        return str(result)


def get_peers(symbol: str):
    """Get peer companies for a stock ticker."""

    result = obb.equity.compare.peers(symbol)

    try:
        return result.to_df().to_dict(orient="records")
    except Exception:
        return str(result)


def get_quote(symbol: str):
    """Get the latest stock quote."""

    result = obb.equity.price.quote(symbol=symbol)

    try:
        return result.to_df().to_dict(orient="records")
    except Exception:
        return str(result)


def get_performance(symbol: str):
    """Get stock price performance."""

    result = obb.equity.price.performance(symbol=symbol)

    try:
        return result.to_df().to_dict(orient="records")
    except Exception:
        return str(result)


def get_consensus(symbol: str):
    """Get analyst consensus price target for a stock."""

    try:
        result = obb.equity.estimates.consensus(
            symbol=symbol
        )

        if not result.results:
            return {
                "success": False,
                "symbol": symbol,
                "message": "No consensus price target data was returned."
            }

        data = result.results[0]

        return {
            "success": True,
            "symbol": data.symbol,
            "target_high": data.target_high,
            "target_low": data.target_low,
            "target_consensus": data.target_consensus,
            "target_median": data.target_median,
        }

    except Exception as e:

        error_message = str(e)

        if "402" in error_message and "Premium Query Parameter" in error_message:
            return {
                "success": False,
                "symbol": symbol,
                "error_type": "provider_subscription",
                "message": (
                    "Consensus data for this symbol is unavailable "
                    "through the currently configured FMP subscription."
                )
            }

        return {
            "success": False,
            "symbol": symbol,
            "error_type": "provider_error",
            "message": error_message
        }


def get_company_news(symbol: str):
    """Get recent company news."""

    result = obb.news.company(symbol)

    try:
        return result.to_df().to_dict(orient="records")
    except Exception:
        return str(result)