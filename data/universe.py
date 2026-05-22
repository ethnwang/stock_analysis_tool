from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from config import Config

DEFAULT_WATCHLIST: list[str] = [
    # Tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSM", "AVGO", "CRM", "AMD",
    # Finance
    "JPM", "V", "MA", "BAC", "GS", "BLK", "AXP", "MS",
    # Healthcare
    "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO",
    # Consumer
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "PG",
    # Energy
    "XOM", "CVX", "COP", "SLB",
    # Industrial
    "CAT", "DE", "HON", "UNP", "GE",
    # Other
    "DIS", "NFLX", "TSLA", "BRK-B", "LMT", "NEE", "AMT",
]

# Hardcoded S&P 500 fallback (top ~50 by market cap, used when Finnhub is unavailable)
_SP500_FALLBACK: list[str] = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "BRK-B", "LLY", "AVGO", "JPM",
    "TSLA", "UNH", "V", "XOM", "MA", "PG", "COST", "JNJ", "HD", "ABBV",
    "MRK", "WMT", "NFLX", "CRM", "BAC", "CVX", "AMD", "KO", "PEP", "LIN",
    "TMO", "ORCL", "MCD", "CSCO", "ADBE", "ABT", "ACN", "WFC", "GE", "PM",
    "IBM", "NOW", "TXN", "DHR", "ISRG", "CAT", "GS", "QCOM", "INTU", "AMGN",
    "VZ", "T", "BKNG", "BLK", "AXP", "SPGI", "NEE", "MS", "LOW", "PFE",
    "RTX", "HON", "SYK", "AMAT", "UNP", "ELV", "DE", "SCHW", "COP", "LRCX",
    "BA", "ADP", "LMT", "MDLZ", "CB", "ADI", "PLD", "GILD", "VRTX", "FI",
    "REGN", "MMC", "SLB", "SO", "DUK", "BDX", "CME", "PYPL", "BSX", "CL",
    "AMT", "CI", "ICE", "EQIX", "MO", "PGR", "SHW", "WM", "ZTS", "NOC",
]


DEFAULT_ETF_WATCHLIST: list[str] = [
    # Broad US Market
    "VOO", "VTI", "SPY", "IVV", "QQQ",
    # International
    "VXUS", "VEA", "VWO", "EFA", "IEMG",
    # Sector
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLC", "XLRE",
    # Bonds
    "BND", "AGG", "TLT", "VCIT", "VCSH",
    # Dividend / Income
    "VYM", "SCHD", "DVY",
    # Growth / Value
    "VUG", "VTV", "MTUM",
    # Thematic / Specialty
    "ARKK", "ICLN", "GLD", "SLV",
    # Small / Mid Cap
    "VB", "VO", "IJR",
]


def get_sp500_tickers(finnhub_api_key: str = "") -> list[str]:
    if finnhub_api_key:
        try:
            import finnhub
            client = finnhub.Client(api_key=finnhub_api_key)
            result = client.indices_const(symbol="^GSPC")
            if result and "constituents" in result:
                tickers = result["constituents"]
                if len(tickers) > 400:
                    return sorted(tickers)
        except Exception as exc:
            logger.warning("Finnhub S&P 500 fetch failed (%s), using fallback list", exc)

    return list(_SP500_FALLBACK)


def get_universe(config: Config) -> list[str]:
    if config.custom_tickers:
        return config.custom_tickers

    if config.universe == "sp500":
        return get_sp500_tickers(config.finnhub_api_key if config.has_finnhub else "")

    if config.universe == "etf":
        return list(DEFAULT_ETF_WATCHLIST)

    return list(DEFAULT_WATCHLIST)
