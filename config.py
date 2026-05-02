from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)


@dataclass(frozen=True)
class Config:
    finnhub_api_key: str = ""
    universe: str = "watchlist"
    top_n: int = 10
    lookback_days: int = 365
    weight_technical: float = 0.45
    weight_fundamental: float = 0.45
    weight_sentiment: float = 0.10
    min_price: float = 5.0
    min_market_cap: float = 300_000_000
    risk_profile: str = "moderate"
    verbose: bool = False
    custom_tickers: list[str] = field(default_factory=list)

    schwab_client_id: str = ""
    schwab_client_secret: str = ""
    schwab_refresh_token: str = ""

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"
    plaid_access_token_chase: str = ""
    plaid_access_token_fidelity: str = ""

    @property
    def has_finnhub(self) -> bool:
        return bool(self.finnhub_api_key) and self.finnhub_api_key != "your_key_here"

    @property
    def has_schwab(self) -> bool:
        return bool(self.schwab_client_id and self.schwab_client_secret and self.schwab_refresh_token)

    @property
    def has_plaid(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret)


def load_config(
    *,
    universe: str | None = None,
    top_n: int | None = None,
    verbose: bool = False,
    tickers: list[str] | None = None,
    risk_profile: str | None = None,
) -> Config:
    _load_env()

    weights = (
        float(os.environ.get("WEIGHT_TECHNICAL", "0.45")),
        float(os.environ.get("WEIGHT_FUNDAMENTAL", "0.45")),
        float(os.environ.get("WEIGHT_SENTIMENT", "0.10")),
    )
    total = sum(weights)
    if total <= 0:
        weights = (0.45, 0.45, 0.10)
    elif abs(total - 1.0) > 0.01:
        weights = tuple(w / total for w in weights)

    return Config(
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY", ""),
        universe=universe or os.environ.get("UNIVERSE", "watchlist"),
        top_n=top_n if top_n is not None else int(os.environ.get("TOP_N", "10")),
        lookback_days=int(os.environ.get("LOOKBACK_DAYS", "365")),
        weight_technical=weights[0],
        weight_fundamental=weights[1],
        weight_sentiment=weights[2],
        min_price=float(os.environ.get("MIN_PRICE", "5.0")),
        min_market_cap=float(os.environ.get("MIN_MARKET_CAP", "300000000")),
        risk_profile=risk_profile or os.environ.get("RISK_PROFILE", "moderate"),
        verbose=verbose,
        custom_tickers=tickers or [],
        schwab_client_id=os.environ.get("SCHWAB_CLIENT_ID", ""),
        schwab_client_secret=os.environ.get("SCHWAB_CLIENT_SECRET", ""),
        schwab_refresh_token=os.environ.get("SCHWAB_REFRESH_TOKEN", ""),
        plaid_client_id=os.environ.get("PLAID_CLIENT_ID", ""),
        plaid_secret=os.environ.get("PLAID_SECRET", ""),
        plaid_env=os.environ.get("PLAID_ENV", "sandbox"),
        plaid_access_token_chase=os.environ.get("PLAID_ACCESS_TOKEN_CHASE", ""),
        plaid_access_token_fidelity=os.environ.get("PLAID_ACCESS_TOKEN_FIDELITY", ""),
    )
