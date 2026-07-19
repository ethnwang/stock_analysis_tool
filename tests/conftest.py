from __future__ import annotations

import zlib

import numpy as np
import pandas as pd
import pytest

from config import Config
from data.models import StockData


@pytest.fixture(autouse=True)
def _isolate_factor_stats_cache(tmp_path, monkeypatch) -> None:
    """Keep unit tests hermetic: never read/write the real factor-stats cache."""
    monkeypatch.setattr(
        "analysis.xsection.FACTOR_STATS_CACHE_PATH", tmp_path / "factor_stats.json",
    )


def make_stock(
    ticker: str = "TEST",
    name: str = "Test Corp",
    sector: str = "Technology",
    pe: float = 20.0,
    eps_growth: float = 0.10,
    rev_growth: float = 0.10,
    de_ratio: float = 0.5,
    div_yield: float = 0.02,
    roe: float = 0.18,
    gross_margin: float = 0.45,
    profit_margin: float = 0.12,
    fcf_yield: float = 0.04,
) -> StockData:
    rng = np.random.default_rng(zlib.crc32(ticker.encode()))  # hash() is salted per process — tests must be deterministic
    n = 300
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0.0005, 0.01)))

    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="B")
    df = pd.DataFrame(
        {
            "Open": [p * 0.99 for p in prices],
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.98 for p in prices],
            "Close": prices,
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )

    return StockData(
        ticker=ticker,
        name=name,
        sector=sector,
        price_history=df,
        fundamentals={
            "pe_ratio": pe,
            "eps_growth": eps_growth,
            "revenue_growth": rev_growth,
            "debt_to_equity": de_ratio,
            "dividend_yield": div_yield,
            "roe": roe,
            "gross_margin": gross_margin,
            "profit_margin": profit_margin,
            "fcf_yield": fcf_yield,
        },
        news=[],
        quote={"price": prices[-1]},
    )


def make_etf(
    ticker: str = "VETF",
    name: str = "Test ETF",
    category: str = "Large Blend",
    expense_ratio: float = 0.0003,
    total_assets: float = 100_000_000_000.0,
    one_year_return: float = 0.15,
    three_year_return: float = 0.12,
    five_year_return: float = 0.10,
    top10_concentration: float = 0.35,
    dividend_yield: float = 0.01,
) -> StockData:
    rng = np.random.default_rng(zlib.crc32(ticker.encode()))  # hash() is salted per process — tests must be deterministic
    n = 300
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0.0005, 0.01)))

    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="B")
    df = pd.DataFrame(
        {
            "Open": [p * 0.99 for p in prices],
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.98 for p in prices],
            "Close": prices,
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )

    return StockData(
        ticker=ticker,
        name=name,
        sector=category,
        price_history=df,
        fundamentals={
            "expense_ratio": expense_ratio,
            "total_assets": total_assets,
            "top10_concentration": top10_concentration,
            "one_year_return": one_year_return,
            "three_year_return": three_year_return,
            "five_year_return": five_year_return,
            "one_year_return_vs_cat": 0.0,
            "dividend_yield": dividend_yield,
            "current_price": prices[-1],
            "market_cap": 0.0,
            "is_etf": 1.0,
        },
        news=[],
        quote={"price": prices[-1]},
    )


def default_config(**overrides) -> Config:
    defaults = {
        "weight_technical": 0.45,
        "weight_fundamental": 0.45,
        "weight_sentiment": 0.10,
        "top_n": 10,
        "verbose": False,
    }
    defaults.update(overrides)
    return Config(**defaults)
