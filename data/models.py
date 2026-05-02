from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

import pandas as pd


class SwapSuggestion(NamedTuple):
    sell_ticker: str
    sell_name: str
    sell_score: float
    buy_ticker: str
    buy_name: str
    buy_score: float
    reason: str


@dataclass
class StockData:
    ticker: str
    name: str
    sector: str
    price_history: pd.DataFrame
    fundamentals: dict[str, float]
    news: list[dict[str, str]]
    quote: dict[str, float]


@dataclass
class ScoredStock:
    ticker: str
    name: str
    sector: str
    current_price: float
    technical_score: float
    fundamental_score: float
    sentiment_score: float
    composite_score: float
    recommendation: str
    reasoning: list[str] = field(default_factory=list)

    is_held: bool = False
    held_shares: float = 0.0
    held_accounts: list[str] = field(default_factory=list)
    overlap_penalty: float = 0.0

    sector_penalty: float = 0.0

    suggested_amount: float = 0.0
    suggested_shares: float = 0.0
    suggested_account: str = ""
    suggested_account_reason: str = ""

    eps_growth: float = 0.0
    dividend_yield: float = 0.0
