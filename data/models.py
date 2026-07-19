from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

import pandas as pd


@dataclass(frozen=True)
class ScoreResult:
    score: float
    reasons: list[str]
    completeness: float = 1.0  # weight-fraction of inputs that were actually present (0..1)


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
    fundamentals: dict[str, float | None]
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
    is_etf: bool = False

    data_completeness: float = 1.0
    insufficient_data: bool = False

    # Annualized std of daily returns (last ~3 months); None when history is thin
    realized_vol: float | None = None

    # Raw sub-signal scores (momentum, quality, …) for snapshot evaluation
    components: dict[str, float] = field(default_factory=dict)
