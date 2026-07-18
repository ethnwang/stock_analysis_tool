from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from data.fetcher import (
    RateLimiter,
    TickerInfo,
    _extract_ticker_info,
    _fetch_news_finnhub,
    fetch_news,
)


class TestTickerInfoExtraction:
    def test_valid_data(self) -> None:
        sd = {"trailingPE": 25.0, "dividendYield": 0.02, "marketCap": 1_000_000_000}
        ks = {"earningsQuarterlyGrowth": 0.15}
        fd = {"revenueGrowth": 0.10, "debtToEquity": 50.0, "currentPrice": 150.0}
        profile = {"sector": "Technology"}
        qt = {"shortName": "Test Corp"}

        info = _extract_ticker_info("TEST", sd, ks, fd, profile, qt)

        assert isinstance(info, TickerInfo)
        assert info.name == "Test Corp"
        assert info.sector == "Technology"
        assert info.fundamentals["pe_ratio"] == 25.0
        assert info.fundamentals["current_price"] == 150.0
        assert info.fundamentals["debt_to_equity"] == 0.5

    def test_error_string_returns_empty(self) -> None:
        info = _extract_ticker_info("BAD", "No data found", {}, {}, {}, {})

        assert info.name == "BAD"
        assert info.sector == "Unknown"
        assert info.fundamentals["current_price"] is None

    def test_missing_fields_default_to_none(self) -> None:
        info = _extract_ticker_info("EMPTY", {}, {}, {}, {}, {})

        assert info.fundamentals["pe_ratio"] is None
        assert info.fundamentals["eps_growth"] is None
        # dividend_yield is the deliberate exception: absence means non-payer
        assert info.fundamentals["dividend_yield"] == 0.0
        assert info.name == "EMPTY"

    def test_forward_pe_fallback(self) -> None:
        sd = {"forwardPE": 30.0}
        info = _extract_ticker_info("FWD", sd, {}, {}, {}, {})
        assert info.fundamentals["pe_ratio"] == 30.0

    def test_name_fallback_to_long_name(self) -> None:
        qt = {"longName": "Long Name Corp"}
        info = _extract_ticker_info("LNG", {}, {}, {}, {}, qt)
        assert info.name == "Long Name Corp"

    def test_name_fallback_to_ticker(self) -> None:
        info = _extract_ticker_info("NONE", {}, {}, {}, {}, {})
        assert info.name == "NONE"


class TestRateLimiterThreadSafety:
    def test_concurrent_calls_dont_exceed_limit(self) -> None:
        limiter = RateLimiter(max_calls=10, window_seconds=60.0)
        call_count = [0]
        lock = threading.Lock()

        def call_limiter() -> None:
            limiter.wait()
            with lock:
                call_count[0] += 1

        threads = [threading.Thread(target=call_limiter) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert call_count[0] == 10

    def test_basic_rate_limiting(self) -> None:
        limiter = RateLimiter(max_calls=3, window_seconds=0.5)
        for _ in range(3):
            limiter.wait()

        start = time.monotonic()
        limiter.wait()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.3


def _make_finnhub_article(headline: str = "Test headline") -> dict:
    return {
        "headline": headline,
        "summary": "Test summary",
        "source": "Reuters",
        "datetime": 1717200000,
    }


class TestFetchNewsFinnhub:
    def test_returns_formatted_articles(self) -> None:
        client = MagicMock()
        client.company_news.return_value = [_make_finnhub_article("Big news")]
        limiter = RateLimiter()

        result = _fetch_news_finnhub("AAPL", client, limiter)

        assert len(result) == 1
        assert result[0]["headline"] == "Big news"
        assert result[0]["source"] == "Reuters"
        assert "summary" in result[0]
        assert "datetime" in result[0]

    def test_returns_empty_on_no_articles(self) -> None:
        client = MagicMock()
        client.company_news.return_value = None
        limiter = RateLimiter()

        result = _fetch_news_finnhub("AAPL", client, limiter)
        assert result == []

    def test_handles_api_error(self) -> None:
        client = MagicMock()
        client.company_news.side_effect = Exception("401 Invalid API key")
        limiter = RateLimiter()

        result = _fetch_news_finnhub("AAPL", client, limiter)
        assert result == []

    def test_limits_to_20_articles(self) -> None:
        client = MagicMock()
        client.company_news.return_value = [_make_finnhub_article(f"Article {i}") for i in range(30)]
        limiter = RateLimiter()

        result = _fetch_news_finnhub("AAPL", client, limiter)
        assert len(result) == 20


class TestFetchNews:
    def test_uses_finnhub_when_available(self) -> None:
        finnhub_limiter = RateLimiter()
        yahoo_limiter = RateLimiter()

        with patch("data.fetcher._fetch_news_finnhub") as mock_fh, \
             patch("data.fetcher._fetch_news_yahoo") as mock_yh:
            mock_fh.return_value = [{"headline": "Finnhub article"}]
            client = MagicMock()

            result = fetch_news("AAPL", client, finnhub_limiter, yahoo_limiter)

            assert result == [{"headline": "Finnhub article"}]
            mock_fh.assert_called_once()
            mock_yh.assert_not_called()

    def test_falls_back_to_yahoo_when_finnhub_empty(self) -> None:
        finnhub_limiter = RateLimiter()
        yahoo_limiter = RateLimiter()

        with patch("data.fetcher._fetch_news_finnhub") as mock_fh, \
             patch("data.fetcher._fetch_news_yahoo") as mock_yh:
            mock_fh.return_value = []
            mock_yh.return_value = [{"headline": "Yahoo article"}]
            client = MagicMock()

            result = fetch_news("AAPL", client, finnhub_limiter, yahoo_limiter)

            assert result == [{"headline": "Yahoo article"}]
            mock_fh.assert_called_once()
            mock_yh.assert_called_once()

    def test_skips_finnhub_when_no_client(self) -> None:
        finnhub_limiter = RateLimiter()
        yahoo_limiter = RateLimiter()

        with patch("data.fetcher._fetch_news_finnhub") as mock_fh, \
             patch("data.fetcher._fetch_news_yahoo") as mock_yh:
            mock_yh.return_value = [{"headline": "Yahoo article"}]

            result = fetch_news("AAPL", None, finnhub_limiter, yahoo_limiter)

            assert result == [{"headline": "Yahoo article"}]
            mock_fh.assert_not_called()
            mock_yh.assert_called_once()
