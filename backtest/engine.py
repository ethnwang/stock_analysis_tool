from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from analysis.technical import compute_indicators, score_technical
from backtest.snapshots import DEFAULT_SNAPSHOT_PATH, SnapshotRecord, load_snapshots
from data.fetcher import fetch_price_history

logger = logging.getLogger(__name__)

# SMA200 needs 200 bars of warm-up before the first scoreable date
_WARMUP_BARS = 200

# Cross-sectional statistics over fewer names than this are noise
_MIN_TICKERS_PER_DATE = 8

_QUINTILES = 5


@dataclass(frozen=True)
class BacktestResult:
    spearman_by_horizon: dict[int, float]  # horizon (bars) -> mean cross-sectional Spearman
    bucket_means: dict[int, list[float]]  # horizon -> mean fwd return per quintile (low..high)
    n_observations: int
    n_dates: int
    tickers: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def _spearman(scores: pd.Series, returns: pd.Series) -> float:
    return float(scores.rank().corr(returns.rank()))


def _quintile_means(scores: pd.Series, returns: pd.Series) -> list[list[float]]:
    """Return per-quintile forward-return lists (quintile 0 = lowest scores)."""
    buckets: list[list[float]] = [[] for _ in range(_QUINTILES)]
    n = len(scores)
    ordinal = scores.rank(method="first")  # 1..n, ties broken by order
    for ticker in scores.index:
        q = min(int((ordinal[ticker] - 1) / n * _QUINTILES), _QUINTILES - 1)
        buckets[q].append(float(returns[ticker]))
    return buckets


def run_technical_backtest(
    tickers: list[str],
    days: int = 1095,
    step: int = 5,
    horizons: tuple[int, ...] = (21, 63),
) -> BacktestResult:
    """As-of technical backtest: score each ticker using only bars up to date T,
    then measure returns over the following `horizons` trading days.

    No lookahead by construction — the indicator input frame is sliced to end
    at the as-of bar. Only the technical pillar is testable historically;
    fundamentals and sentiment aren't available as-of past dates.
    """
    price_data = fetch_price_history(tickers, days)
    if not price_data:
        return BacktestResult({}, {}, 0, 0, tickers, ["No price data retrieved"])

    max_horizon = max(horizons)
    # per-date frames: {horizon: {date_idx: {ticker: (score, fwd_return)}}}
    per_date: dict[int, dict[int, dict[str, tuple[float, float]]]] = {
        h: {} for h in horizons
    }
    n_observations = 0

    for ticker, df in price_data.items():
        closes = df["Close"]
        n = len(df)
        if n <= _WARMUP_BARS + max_horizon:
            logger.info(
                "Skipping %s: %d bars < %d needed", ticker, n, _WARMUP_BARS + max_horizon,
            )
            continue
        for i in range(_WARMUP_BARS, n - max_horizon, step):
            as_of = df.iloc[: i + 1]
            try:
                result = score_technical(compute_indicators(as_of))
            except (KeyError, ValueError, IndexError, TypeError) as exc:
                logger.debug("Score failed for %s at bar %d: %s", ticker, i, exc)
                continue
            if result.completeness < 0.5:
                continue
            base = float(closes.iloc[i])
            if base <= 0:
                continue
            for h in horizons:
                fwd = float(closes.iloc[i + h]) / base - 1.0
                per_date[h].setdefault(i, {})[ticker] = (result.score, fwd)
            n_observations += 1

    spearman_by_horizon: dict[int, float] = {}
    bucket_means: dict[int, list[float]] = {}
    n_dates = 0

    for h in horizons:
        correlations: list[float] = []
        quintile_returns: list[list[float]] = [[] for _ in range(_QUINTILES)]
        for _, ticker_map in sorted(per_date[h].items()):
            if len(ticker_map) < _MIN_TICKERS_PER_DATE:
                continue
            scores = pd.Series({t: sr[0] for t, sr in ticker_map.items()})
            returns = pd.Series({t: sr[1] for t, sr in ticker_map.items()})
            if scores.nunique() < 2:
                continue
            corr = _spearman(scores, returns)
            if not pd.isna(corr):
                correlations.append(corr)
            for q, bucket in enumerate(_quintile_means(scores, returns)):
                quintile_returns[q].extend(bucket)
        n_dates = max(n_dates, len(correlations))
        spearman_by_horizon[h] = (
            float(pd.Series(correlations).mean()) if correlations else float("nan")
        )
        bucket_means[h] = [
            float(pd.Series(b).mean()) if b else float("nan") for b in quintile_returns
        ]

    caveats = [
        "Technical pillar only — fundamentals/sentiment aren't available as-of past dates",
        "Composite validation accrues via `analyze --snapshot` history over time",
    ]
    return BacktestResult(
        spearman_by_horizon=spearman_by_horizon,
        bucket_means=bucket_means,
        n_observations=n_observations,
        n_dates=n_dates,
        tickers=sorted(price_data.keys()),
        caveats=caveats,
    )


def evaluate_snapshots(
    path: Path = DEFAULT_SNAPSHOT_PATH,
    min_age_days: int = 21,
) -> BacktestResult:
    """Compare past composite-score snapshots against realized returns since."""
    records = load_snapshots(path)
    now = datetime.now(timezone.utc)

    aged: list[SnapshotRecord] = []
    for r in records:
        try:
            snap_date = datetime.fromisoformat(r.date)
        except ValueError:
            continue
        if snap_date.tzinfo is None:
            snap_date = snap_date.replace(tzinfo=timezone.utc)
        if (now - snap_date).days >= min_age_days and r.price > 0:
            aged.append(r)

    if not aged:
        return BacktestResult(
            {}, {}, 0, 0, [],
            [f"No snapshots older than {min_age_days} days in {path} — keep running "
             "`analyze --snapshot` and re-evaluate later"],
        )

    tickers = sorted({r.ticker for r in aged})
    price_data = fetch_price_history(tickers, days=30)
    current_prices = {
        t: float(df["Close"].iloc[-1]) for t, df in price_data.items() if len(df) > 0
    }

    # group by snapshot date so the correlation stays cross-sectional
    by_date: dict[str, list[SnapshotRecord]] = {}
    for r in aged:
        if r.ticker in current_prices:
            by_date.setdefault(r.date, []).append(r)

    correlations: list[float] = []
    quintile_returns: list[list[float]] = [[] for _ in range(_QUINTILES)]
    n_observations = 0
    for _, recs in sorted(by_date.items()):
        if len(recs) < _MIN_TICKERS_PER_DATE:
            continue
        scores = pd.Series({r.ticker: r.composite for r in recs})
        returns = pd.Series(
            {r.ticker: current_prices[r.ticker] / r.price - 1.0 for r in recs}
        )
        if scores.nunique() < 2:
            continue
        corr = _spearman(scores, returns)
        if not pd.isna(corr):
            correlations.append(corr)
        for q, bucket in enumerate(_quintile_means(scores, returns)):
            quintile_returns[q].extend(bucket)
        n_observations += len(recs)

    mean_corr = float(pd.Series(correlations).mean()) if correlations else float("nan")
    caveats = ["Snapshot evaluation: realized return from snapshot date to now"]
    if not correlations:
        caveats.append(
            f"No snapshot date had >= {_MIN_TICKERS_PER_DATE} tickers — snapshot the "
            "full universe (`analyze --snapshot`) for meaningful cross-sections"
        )
    return BacktestResult(
        spearman_by_horizon={0: mean_corr},
        bucket_means={0: [
            float(pd.Series(b).mean()) if b else float("nan") for b in quintile_returns
        ]},
        n_observations=n_observations,
        n_dates=len(correlations),
        tickers=tickers,
        caveats=caveats,
    )
