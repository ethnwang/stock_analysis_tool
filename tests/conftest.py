from __future__ import annotations

import numpy as np
import pandas as pd

from config import Config
from data.models import StockData


def make_stock(
    ticker: str = "TEST",
    name: str = "Test Corp",
    sector: str = "Technology",
    pe: float = 20.0,
    eps_growth: float = 0.10,
    rev_growth: float = 0.10,
    de_ratio: float = 0.5,
    div_yield: float = 0.02,
) -> StockData:
    rng = np.random.default_rng(hash(ticker) % 2**32)
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
