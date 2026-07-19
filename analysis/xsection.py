"""Cross-sectional factor normalization.

Absolute band scores ("P/E < 15 is cheap") ignore market regime and compress
composites toward the middle. This module builds universe-wide decile
breakpoints per factor and blends each band score with the stock's percentile
in the cross-section. Small batches (the 48-name watchlist) can't estimate
deciles, so a fresh cache built from the last full-universe (sp500) run is
preferred; with no cache, bands stand alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from data.models import StockData

logger = logging.getLogger(__name__)

FACTOR_STATS_CACHE_PATH = Path(__file__).parent.parent / "data" / "cache" / "factor_stats.json"
_STATS_STALE_DAYS = 30

# Deciles 10th..90th — the interpolation grid for percentile scores
_DECILES = tuple(range(10, 100, 10))

# Factor -> higher-is-better. P/E is inverted (cheap = good); only positive
# P/Es enter the breakpoints (negative P/E means unprofitable, not cheap).
FACTOR_DIRECTIONS: dict[str, bool] = {
    "mom_12_1": True,
    "pe_ratio": False,
    "roe": True,
    "gross_margin": True,
    "fcf_yield": True,
}

# Blend ramp: batches at/below the floor can't estimate deciles (bands only);
# at/above the ceiling the percentile leg gets its full half share.
_BLEND_N_FLOOR = 20
_BLEND_N_CEILING = 100
_PERCENTILE_MAX_SHARE = 0.5

# 12-1 momentum window (kept here so both compute_indicators and factor-stats
# construction share one definition without a circular import)
MOMENTUM_LOOKBACK_BARS = 252
MOMENTUM_SKIP_BARS = 21


def compute_momentum_12_1(close: pd.Series) -> float | None:
    """Return from 12 months ago to 1 month ago (skip-month convention)."""
    if len(close) < MOMENTUM_LOOKBACK_BARS:
        return None
    base_price = float(close.iloc[-MOMENTUM_LOOKBACK_BARS])
    if base_price <= 0:
        return None
    return float(close.iloc[-MOMENTUM_SKIP_BARS]) / base_price - 1.0


@dataclass(frozen=True)
class FactorStats:
    breakpoints: dict[str, list[float]]  # factor -> decile values (10th..90th)
    n: int  # number of stocks the stats were built from
    as_of: str  # ISO date

    def has(self, factor: str) -> bool:
        return factor in self.breakpoints


def build_factor_stats(stocks: list[StockData]) -> FactorStats:
    values: dict[str, list[float]] = {f: [] for f in FACTOR_DIRECTIONS}
    n_stocks = 0
    for stock in stocks:
        if (stock.fundamentals.get("is_etf") or 0.0) > 0:
            continue
        n_stocks += 1
        for factor in ("pe_ratio", "roe", "gross_margin", "fcf_yield"):
            value = stock.fundamentals.get(factor)
            if value is None:
                continue
            if factor == "pe_ratio" and value <= 0:
                continue
            values[factor].append(float(value))
        if len(stock.price_history) > 0 and "Close" in stock.price_history:
            mom = compute_momentum_12_1(stock.price_history["Close"])
            if mom is not None:
                values["mom_12_1"].append(mom)

    breakpoints: dict[str, list[float]] = {}
    for factor, vals in values.items():
        if len(vals) >= _BLEND_N_FLOOR:
            breakpoints[factor] = [
                float(np.percentile(vals, p)) for p in _DECILES
            ]
    return FactorStats(
        breakpoints=breakpoints, n=n_stocks, as_of=date.today().isoformat(),
    )


def percentile_score(value: float, breakpoints: list[float], higher_is_better: bool) -> float:
    """Map a value to 10..90 by linear interpolation across decile breakpoints."""
    score = float(np.interp(value, breakpoints, _DECILES))
    return score if higher_is_better else 100.0 - score


def _percentile_share(n: int) -> float:
    if n <= _BLEND_N_FLOOR:
        return 0.0
    ramp = min((n - _BLEND_N_FLOOR) / (_BLEND_N_CEILING - _BLEND_N_FLOOR), 1.0)
    return _PERCENTILE_MAX_SHARE * ramp


def blend_with_percentile(
    band_score: float,
    factor: str,
    value: float,
    stats: FactorStats | None,
) -> float:
    """Blend an absolute band score with the value's cross-sectional percentile.

    Falls back to the band score alone when stats are missing, the factor has
    no breakpoints, or the batch was too small to estimate deciles.
    """
    if stats is None or not stats.has(factor):
        return band_score
    share = _percentile_share(stats.n)
    if share <= 0:
        return band_score
    pct = percentile_score(value, stats.breakpoints[factor], FACTOR_DIRECTIONS[factor])
    return (1.0 - share) * band_score + share * pct


def sentiment_offset(sentiment_scores: list[float]) -> float:
    """Additive recentering that maps the batch median sentiment to 50.

    The keyword lexicon skews positive (observed medians ~70), which silently
    lifts every composite; recentering makes sentiment relative to peers.
    """
    if not sentiment_scores:
        return 0.0
    return 50.0 - float(np.median(sentiment_scores))


def save_factor_stats(stats: FactorStats, path: Path | None = None) -> None:
    # Resolved at call time so tests can repoint the module-level constant
    path = path if path is not None else FACTOR_STATS_CACHE_PATH
    if not stats.breakpoints:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(stats)), encoding="utf-8")
        logger.info("Saved factor stats (n=%d) to %s", stats.n, path)
    except OSError as exc:
        logger.warning("Could not save factor stats: %s", exc)


def load_factor_stats(path: Path | None = None) -> FactorStats | None:
    """Load cached universe stats; None when missing, malformed, or stale."""
    path = path if path is not None else FACTOR_STATS_CACHE_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stats = FactorStats(
            breakpoints={k: [float(x) for x in v] for k, v in data["breakpoints"].items()},
            n=int(data["n"]),
            as_of=str(data["as_of"]),
        )
        age_days = (date.today() - datetime.fromisoformat(stats.as_of).date()).days
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
        logger.warning("Ignoring unreadable factor-stats cache: %s", exc)
        return None
    if age_days > _STATS_STALE_DAYS:
        logger.info("Factor-stats cache is %d days old (> %d) — ignoring", age_days, _STATS_STALE_DAYS)
        return None
    return stats
