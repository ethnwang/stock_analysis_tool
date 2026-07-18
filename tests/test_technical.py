from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.technical import compute_indicators, score_technical


def _make_ohlcv(
    prices: list[float],
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    n = len(prices)
    if volumes is None:
        volumes = [1_000_000.0] * n

    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": [p * 0.99 for p in prices],
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.98 for p in prices],
            "Close": prices,
            "Volume": volumes,
        },
        index=dates,
    )


def _uptrend(n: int = 300, start: float = 100.0) -> list[float]:
    rng = np.random.default_rng(42)
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0.001, 0.005)))
    return prices


def _downtrend(n: int = 300, start: float = 200.0) -> list[float]:
    rng = np.random.default_rng(42)
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 - rng.normal(0.001, 0.005)))
    return prices


class TestComputeIndicators:
    def test_returns_expected_keys(self) -> None:
        df = _make_ohlcv(_uptrend())
        indicators = compute_indicators(df)

        expected_keys = {
            "rsi", "macd_line", "macd_signal", "macd_hist", "macd_direction",
            "bb_pct", "bb_upper", "bb_lower", "sma50", "sma200",
            "adx", "volume_ratio", "current_price",
        }
        assert set(indicators.keys()) == expected_keys

    def test_rsi_in_valid_range(self) -> None:
        df = _make_ohlcv(_uptrend())
        indicators = compute_indicators(df)
        assert 0 <= indicators["rsi"] <= 100

    def test_sma_values_positive(self) -> None:
        df = _make_ohlcv(_uptrend())
        indicators = compute_indicators(df)
        assert indicators["sma50"] > 0
        assert indicators["sma200"] > 0


class TestScoreTechnical:
    def test_uptrend_scores_higher_than_downtrend(self) -> None:
        up_df = _make_ohlcv(_uptrend())
        down_df = _make_ohlcv(_downtrend())

        up_indicators = compute_indicators(up_df)
        down_indicators = compute_indicators(down_df)

        assert score_technical(up_indicators).score > score_technical(down_indicators).score

    def test_score_in_range(self) -> None:
        df = _make_ohlcv(_uptrend())
        indicators = compute_indicators(df)
        result = score_technical(indicators)

        assert 0 <= result.score <= 100
        assert isinstance(result.reasons, list)

    def test_returns_reasoning(self) -> None:
        df = _make_ohlcv(_uptrend())
        indicators = compute_indicators(df)
        result = score_technical(indicators)

        assert len(result.reasons) > 0
        assert all(isinstance(r, str) for r in result.reasons)

    def test_oversold_rsi_scores_high(self) -> None:
        indicators = {
            "rsi": 25.0, "macd_line": 1.0, "macd_signal": 0.5,
            "macd_direction": "bullish", "bb_pct": 0.1,
            "sma50": 110.0, "sma200": 100.0, "adx": 30.0,
            "volume_ratio": 1.3, "current_price": 150.0,
            "macd_hist": 0.5, "bb_upper": 160.0, "bb_lower": 140.0,
        }
        result = score_technical(indicators)
        assert result.score >= 70
        assert any("oversold" in r.lower() for r in result.reasons)

    def test_overbought_rsi_scores_low(self) -> None:
        indicators = {
            "rsi": 80.0, "macd_line": -1.0, "macd_signal": 0.5,
            "macd_direction": "bearish", "bb_pct": 0.95,
            "sma50": 90.0, "sma200": 100.0, "adx": 15.0,
            "volume_ratio": 0.4, "current_price": 150.0,
            "macd_hist": -0.5, "bb_upper": 160.0, "bb_lower": 140.0,
        }
        result = score_technical(indicators)
        assert result.score <= 30
