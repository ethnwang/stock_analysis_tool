from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from config import Config

_SP500_CACHE_PATH = Path(__file__).parent / "cache" / "sp500.json"
_CACHE_STALE_DAYS = 90

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


_WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _fetch_sp500_wikipedia() -> list[str] | None:
    """Constituents from Wikipedia's S&P 500 table — the free fallback, since
    Finnhub's index-constituents endpoint is premium-tier."""
    import io

    import pandas as pd
    import requests

    try:
        resp = requests.get(
            _WIKIPEDIA_SP500_URL,
            timeout=30,
            headers={"User-Agent": "stockbot/0.1 (personal research tool)"},
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
    except (requests.RequestException, ValueError, ImportError) as exc:
        logger.warning("Wikipedia S&P 500 fetch failed: %s", exc)
        return None

    for table in tables:
        if "Symbol" not in table.columns:
            continue
        tickers = sorted({
            # Wikipedia uses BRK.B style; Yahoo wants BRK-B
            str(s).strip().replace(".", "-")
            for s in table["Symbol"].tolist()
            if isinstance(s, str) and s.strip()
        })
        if len(tickers) > 400:
            return tickers
    logger.warning("Wikipedia S&P 500 page had no parseable Symbol table")
    return None


def _save_cached_sp500(tickers: list[str]) -> None:
    try:
        _SP500_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched": date.today().isoformat(), "tickers": tickers}
        _SP500_CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write S&P 500 cache: %s", exc)


def _load_cached_sp500() -> list[str] | None:
    try:
        payload = json.loads(_SP500_CACHE_PATH.read_text(encoding="utf-8"))
        tickers = payload.get("tickers", [])
        fetched = payload.get("fetched", "")
        if not isinstance(tickers, list) or len(tickers) <= 400:
            return None
        age_days = (date.today() - datetime.fromisoformat(fetched).date()).days
        if age_days > _CACHE_STALE_DAYS:
            logger.warning(
                "Cached S&P 500 constituents are %d days old — membership may have drifted",
                age_days,
            )
        return [str(t) for t in tickers]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not read S&P 500 cache: %s", exc)
        return None


def get_sp500_tickers(finnhub_api_key: str = "") -> list[str]:
    if finnhub_api_key:
        try:
            import finnhub
        except ImportError:
            finnhub = None
            logger.warning("finnhub-python not installed — cannot fetch S&P 500 constituents")
        if finnhub is not None:
            try:
                client = finnhub.Client(api_key=finnhub_api_key)
                result = client.indices_const(symbol="^GSPC")
                if result and "constituents" in result:
                    tickers = result["constituents"]
                    if len(tickers) > 400:
                        tickers = sorted(tickers)
                        _save_cached_sp500(tickers)
                        return tickers
            except (
                ConnectionError, TimeoutError, OSError, ValueError, KeyError,
                finnhub.FinnhubAPIException, finnhub.FinnhubRequestException,
            ) as exc:
                logger.warning("Finnhub S&P 500 fetch failed: %s", exc)

    wiki = _fetch_sp500_wikipedia()
    if wiki is not None:
        logger.info("Using Wikipedia S&P 500 constituents (%d tickers)", len(wiki))
        _save_cached_sp500(wiki)
        return wiki

    cached = _load_cached_sp500()
    if cached is not None:
        logger.info("Using cached S&P 500 constituents (%d tickers)", len(cached))
        return cached

    logger.warning(
        "WARNING: S&P 500 constituents unavailable — falling back to a hardcoded "
        "~100-name mega-cap list. Results are NOT the full S&P 500."
    )
    return list(_SP500_FALLBACK)


def get_universe(config: Config) -> list[str]:
    if config.custom_tickers:
        return config.custom_tickers

    if config.universe == "sp500":
        return get_sp500_tickers(config.finnhub_api_key if config.has_finnhub else "")

    if config.universe == "etf":
        return list(DEFAULT_ETF_WATCHLIST)

    return list(DEFAULT_WATCHLIST)
