from __future__ import annotations

import logging
import os
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

import pandas as pd
from yahooquery import Ticker

if TYPE_CHECKING:
    from config import Config
    from data.models import StockData

logger = logging.getLogger(__name__)

_yf_env_initialized = False


def _ensure_yf_env() -> None:
    global _yf_env_initialized
    if not _yf_env_initialized:
        os.environ.setdefault("YF_SETUP_URL", "https://finance.yahoo.com/quote/AAPL")
        _yf_env_initialized = True


class RateLimiter:
    def __init__(self, max_calls: int = 55, window_seconds: float = 60.0) -> None:
        self._timestamps: deque[float] = deque()
        self._max_calls = max_calls
        self._window = window_seconds
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._timestamps and (now - self._timestamps[0]) >= self._window:
                self._timestamps.popleft()

            if len(self._timestamps) >= self._max_calls:
                sleep_for = self._window - (now - self._timestamps[0]) + 0.1
                if sleep_for > 0:
                    logger.warning("Rate limit: sleeping %.1fs...", sleep_for)
                    time.sleep(sleep_for)

            self._timestamps.append(time.monotonic())


class TickerInfo(NamedTuple):
    fundamentals: dict[str, float | None]
    name: str
    sector: str


# None = data unavailable (scorers skip the metric); 0.0 = a genuine zero.
# dividend_yield is the deliberate exception: Yahoo omits it for non-payers,
# so absence means "pays no dividend" and defaults to 0.0.
_EMPTY_FUNDAMENTALS: dict[str, float | None] = {
    "pe_ratio": None, "forward_pe": None, "eps_growth": None,
    "revenue_growth": None, "debt_to_equity": None, "dividend_yield": 0.0,
    "market_cap": None, "current_price": None,
    "gross_margin": None, "profit_margin": None, "roe": None,
    "fcf_yield": None, "beta": None,
}

_EMPTY_ETF_FUNDAMENTALS: dict[str, float | None] = {
    "expense_ratio": None, "total_assets": None, "top10_concentration": None,
    "one_year_return": None, "three_year_return": None, "five_year_return": None,
    "one_year_return_vs_cat": None, "dividend_yield": 0.0,
    "current_price": None, "market_cap": None, "is_etf": 1.0,
}


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_price_history(tickers: list[str], days: int) -> dict[str, pd.DataFrame]:
    _ensure_yf_env()
    logger.info("Downloading price history for %d tickers...", len(tickers))

    col_map = {"open": "Open", "high": "High", "low": "Low", "close": "Close",
               "volume": "Volume", "adjclose": "AdjClose"}

    result: dict[str, pd.DataFrame] = {}
    try:
        t = Ticker(tickers)
        end = datetime.now()
        start = end - timedelta(days=days)
        hist = t.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))

        if isinstance(hist, pd.DataFrame) and not hist.empty:
            grouped = hist.index.names if hasattr(hist.index, "names") else []
            ticker_list = tickers if "symbol" in grouped else [tickers[0]]
            for tkr in ticker_list:
                try:
                    df = hist.loc[tkr].copy() if "symbol" in grouped else hist.copy()
                    df = df.rename(columns=col_map)
                    if "AdjClose" in df.columns:
                        df["Close"] = df["AdjClose"]
                        df = df.drop(columns=["AdjClose"])
                    if "dividends" in df.columns:
                        df = df.drop(columns=["dividends"])
                    if len(df) >= 20:
                        result[tkr] = df
                except (KeyError, TypeError):
                    pass
    except (ConnectionError, TimeoutError, OSError) as exc:
        logger.error("Price download network error: %s", exc)
    except ValueError as exc:
        logger.error("Price download parse error: %s", exc)

    logger.info("Got price data for %d/%d tickers", len(result), len(tickers))
    return result


def _extract_ticker_info(
    ticker: str,
    sd: dict[str, Any],
    ks: dict[str, Any],
    fd: dict[str, Any],
    profile: dict[str, Any],
    qt: dict[str, Any],
) -> TickerInfo:
    if isinstance(sd, str) or isinstance(ks, str) or isinstance(fd, str):
        return TickerInfo(_EMPTY_FUNDAMENTALS.copy(), ticker, "Unknown")

    de_raw = _opt_float(fd.get("debtToEquity"))
    market_cap = _opt_float(sd.get("marketCap"))
    fcf = _opt_float(fd.get("freeCashflow"))
    fundamentals: dict[str, float | None] = {
        # Trailing only — mixing trailing and forward bases across peers would
        # corrupt the sector medians. Scorers fall back to forward_pe explicitly.
        "pe_ratio": _opt_float(sd.get("trailingPE")),
        "forward_pe": _opt_float(sd.get("forwardPE")),
        "eps_growth": _opt_float(ks.get("earningsQuarterlyGrowth")),
        "revenue_growth": _opt_float(fd.get("revenueGrowth")),
        "debt_to_equity": None if de_raw is None else de_raw / 100.0,
        "dividend_yield": _opt_float(sd.get("dividendYield")) or 0.0,
        "market_cap": market_cap,
        "current_price": _opt_float(fd.get("currentPrice")) or _opt_float(sd.get("regularMarketPrice")),
        "gross_margin": _opt_float(fd.get("grossMargins")),
        "profit_margin": _opt_float(fd.get("profitMargins")),
        "roe": _opt_float(fd.get("returnOnEquity")),
        "fcf_yield": (
            fcf / market_cap
            if fcf is not None and market_cap is not None and market_cap > 0
            else None
        ),
        "beta": _opt_float(sd.get("beta")) or _opt_float(ks.get("beta")),
    }
    name = qt.get("shortName") or qt.get("longName") or ticker
    sector = profile.get("sector", "Unknown")
    return TickerInfo(fundamentals, str(name), str(sector))


def _extract_etf_info(
    ticker: str,
    sd: dict[str, Any],
    ks: dict[str, Any],
    fp: dict[str, Any],
    fperf: dict[str, Any],
    fhi: dict[str, Any],
    qt: dict[str, Any],
    price_data: dict[str, Any],
) -> TickerInfo:
    if isinstance(price_data, str):
        return TickerInfo(_EMPTY_ETF_FUNDAMENTALS.copy(), ticker, "Unknown")

    perf_overview = {}
    cat_overview = {}
    if isinstance(fperf, dict):
        perf_overview = fperf.get("performanceOverview", {}) or {}
        cat_overview = fperf.get("performanceOverviewCat", {}) or {}

    one_yr = _opt_float(perf_overview.get("oneYearTotalReturn"))
    three_yr = _opt_float(perf_overview.get("threeYearTotalReturn"))
    five_yr = _opt_float(perf_overview.get("fiveYearTotalReturn"))
    cat_one_yr = _opt_float(cat_overview.get("oneYearTotalReturn"))

    expense_ratio: float | None = None
    category = "Unknown"
    if isinstance(fp, dict):
        fees = fp.get("feesExpensesInvestment", {}) or {}
        expense_ratio = _opt_float(fees.get("annualReportExpenseRatio"))
        category = str(fp.get("categoryName", "Unknown") or "Unknown")

    top10: float | None = None
    if isinstance(fhi, dict):
        holdings_list = fhi.get("holdings", []) or []
        if holdings_list:
            top10 = sum(
                float(h.get("holdingPercent", 0) or 0) for h in holdings_list[:10]
            )

    current_price = _opt_float(price_data.get("regularMarketPrice"))
    total_assets = (
        (_opt_float(ks.get("totalAssets")) if isinstance(ks, dict) else None) or
        (_opt_float(sd.get("totalAssets")) if isinstance(sd, dict) else None)
    )
    dividend_yield = (
        _opt_float(sd.get("trailingAnnualDividendYield")) if isinstance(sd, dict) else None
    ) or 0.0

    fundamentals: dict[str, float | None] = {
        "expense_ratio": expense_ratio,
        "total_assets": total_assets,
        "top10_concentration": top10,
        "one_year_return": one_yr,
        "three_year_return": three_yr,
        "five_year_return": five_yr,
        "one_year_return_vs_cat": (
            one_yr - cat_one_yr if one_yr is not None and cat_one_yr is not None else None
        ),
        "dividend_yield": dividend_yield,
        "current_price": current_price,
        "market_cap": None,
        "is_etf": 1.0,
    }
    name = qt.get("shortName") or qt.get("longName") or ticker if isinstance(qt, dict) else ticker
    return TickerInfo(fundamentals, str(name), str(category))


def fetch_fundamentals_batch(tickers: list[str]) -> dict[str, TickerInfo]:
    _ensure_yf_env()
    if not tickers:
        return {}

    logger.info("Fetching fundamentals for %d tickers (batched)...", len(tickers))

    try:
        t = Ticker(tickers)
        all_sd = t.summary_detail
        all_ks = t.key_stats
        all_fd = t.financial_data
        all_profile = t.asset_profile
        all_qt = t.quote_type
        all_fp = t.fund_profile
        all_fperf = t.fund_performance
        all_fhi = t.fund_holding_info
        all_price = t.price
    except (ConnectionError, TimeoutError, OSError) as exc:
        logger.error("Fundamentals batch download error: %s", exc)
        return {}
    except ValueError as exc:
        logger.error("Fundamentals batch parse error: %s", exc)
        return {}

    results: dict[str, TickerInfo] = {}
    for ticker in tickers:
        sd = all_sd.get(ticker, {}) if isinstance(all_sd, dict) else {}
        ks = all_ks.get(ticker, {}) if isinstance(all_ks, dict) else {}
        fd = all_fd.get(ticker, {}) if isinstance(all_fd, dict) else {}
        profile = all_profile.get(ticker, {}) if isinstance(all_profile, dict) else {}
        qt = all_qt.get(ticker, {}) if isinstance(all_qt, dict) else {}

        quote_type = qt.get("quoteType", "EQUITY") if isinstance(qt, dict) else "EQUITY"
        if quote_type == "ETF":
            fp = all_fp.get(ticker, {}) if isinstance(all_fp, dict) else {}
            fperf = all_fperf.get(ticker, {}) if isinstance(all_fperf, dict) else {}
            fhi = all_fhi.get(ticker, {}) if isinstance(all_fhi, dict) else {}
            price = all_price.get(ticker, {}) if isinstance(all_price, dict) else {}
            results[ticker] = _extract_etf_info(ticker, sd, ks, fp, fperf, fhi, qt, price)
        else:
            results[ticker] = _extract_ticker_info(ticker, sd, ks, fd, profile, qt)

    return results


def fetch_fundamentals(ticker: str) -> TickerInfo:
    result = fetch_fundamentals_batch([ticker])
    return result.get(ticker, TickerInfo(_EMPTY_FUNDAMENTALS.copy(), ticker, "Unknown"))


def _fetch_news_finnhub(
    ticker: str, client: Any, limiter: RateLimiter,
) -> list[dict[str, str]]:
    try:
        limiter.wait()
        today = datetime.now()
        week_ago = today - timedelta(days=7)
        articles = client.company_news(
            ticker,
            _from=week_ago.strftime("%Y-%m-%d"),
            to=today.strftime("%Y-%m-%d"),
        )
        return [
            {
                "headline": a.get("headline", ""),
                "summary": a.get("summary", ""),
                "source": a.get("source", ""),
                "datetime": str(a.get("datetime", "")),
            }
            for a in (articles or [])[:20]
        ]
    except Exception as exc:
        logger.warning("Finnhub news error for %s: %s", ticker, exc)
        return []


def _fetch_news_yahoo(ticker: str, limiter: RateLimiter) -> list[dict[str, str]]:
    import xml.etree.ElementTree as ET

    import requests

    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        limiter.wait()
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        results = []
        for item in root.findall(".//item")[:20]:
            pub_date = item.findtext("pubDate", "")
            results.append({
                "headline": item.findtext("title", ""),
                "summary": item.findtext("description", ""),
                "source": "Yahoo Finance",
                "datetime": pub_date,
            })
        return results
    except (ConnectionError, TimeoutError, OSError, requests.RequestException) as exc:
        logger.warning("Yahoo news error for %s: %s", ticker, exc)
        return []
    except ET.ParseError as exc:
        logger.warning("Yahoo news XML parse error for %s: %s", ticker, exc)
        return []


def fetch_news(
    ticker: str,
    finnhub_client: Any | None,
    finnhub_limiter: RateLimiter,
    yahoo_limiter: RateLimiter,
) -> list[dict[str, str]]:
    if finnhub_client is not None:
        articles = _fetch_news_finnhub(ticker, finnhub_client, finnhub_limiter)
        if articles:
            return articles

    return _fetch_news_yahoo(ticker, yahoo_limiter)


def fetch_all(tickers: list[str], config: Config) -> list[StockData]:
    from data.models import StockData

    price_data = fetch_price_history(tickers, config.lookback_days)

    valid_tickers = [t for t in tickers if t in price_data]
    if not valid_tickers:
        logger.error("No valid price data retrieved for any ticker.")
        return []

    all_fund = fetch_fundamentals_batch(valid_tickers)

    filtered: list[tuple[str, TickerInfo]] = []
    cap_filter_skipped: list[str] = []
    for ticker in valid_tickers:
        info = all_fund.get(ticker, TickerInfo(_EMPTY_FUNDAMENTALS.copy(), ticker, "Unknown"))
        price = info.fundamentals["current_price"]
        if price is None:
            # Quote fetch failed; use last close so the price filter still applies.
            price = float(price_data[ticker]["Close"].iloc[-1])
            info.fundamentals["current_price"] = price
        if price < config.min_price:
            logger.debug("Filtered %s: price $%.2f < min $%.2f", ticker, price, config.min_price)
            continue
        mc = info.fundamentals["market_cap"]
        is_etf = (info.fundamentals.get("is_etf") or 0.0) > 0
        if mc is None:
            if not is_etf:
                cap_filter_skipped.append(ticker)
        elif mc < config.min_market_cap:
            logger.debug("Filtered %s: market cap $%.0f < min $%.0f", ticker, mc, config.min_market_cap)
            continue
        filtered.append((ticker, info))

    if cap_filter_skipped:
        logger.info(
            "Market cap unknown for %d tickers (min-market-cap filter not applied): %s",
            len(cap_filter_skipped), ", ".join(sorted(cap_filter_skipped)),
        )

    logger.info("Fetching news for %d stocks...", len(filtered))
    finnhub_limiter = RateLimiter(max_calls=55, window_seconds=60.0)
    yahoo_limiter = RateLimiter(max_calls=120, window_seconds=60.0)

    finnhub_client = None
    if config.has_finnhub:
        try:
            import finnhub
            finnhub_client = finnhub.Client(api_key=config.finnhub_api_key)
        except ImportError:
            logger.warning("finnhub-python not installed; using Yahoo news fallback")

    def _fetch_one_news(item: tuple[str, TickerInfo]) -> tuple[str, TickerInfo, list[dict[str, str]]]:
        tkr, info = item
        news = fetch_news(tkr, finnhub_client, finnhub_limiter, yahoo_limiter)
        return tkr, info, news

    with ThreadPoolExecutor(max_workers=4) as executor:
        news_results = list(executor.map(_fetch_one_news, filtered))

    results: list[StockData] = []
    for ticker, info, news in news_results:
        results.append(StockData(
            ticker=ticker,
            name=info.name,
            sector=info.sector,
            price_history=price_data[ticker],
            fundamentals=info.fundamentals,
            news=news,
            quote={"price": info.fundamentals.get("current_price") or 0.0},
        ))

    logger.info("Fetched complete data for %d stocks", len(results))
    return results
