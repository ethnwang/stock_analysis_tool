from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from analysis.technical import compute_indicators, score_technical
from backtest.snapshots import DEFAULT_SNAPSHOT_PATH, SnapshotRecord, load_snapshots
from data.fetcher import fetch_price_history

logger = logging.getLogger(__name__)

# 12-1 momentum needs 252 bars of warm-up before the first scoreable date
# (SMA200 needs 200 — momentum is now the binding constraint)
_WARMUP_BARS = 252

# Cross-sectional statistics over fewer names than this are noise
_MIN_TICKERS_PER_DATE = 8

_QUINTILES = 5

_BENCHMARK_TICKER = "SPY"

# Pillar fields stored directly on SnapshotRecord; anything else is looked up
# in the record's components dict.
_PILLAR_SCORE_KEYS = ("composite", "technical", "fundamental", "sentiment")


@dataclass(frozen=True)
class ICStats:
    """Per-date information coefficient (rank IC) summarized across dates."""

    mean: float
    std: float
    t_stat: float
    n_dates: int
    hit_rate: float  # fraction of dates with IC > 0


@dataclass(frozen=True)
class BacktestResult:
    spearman_by_horizon: dict[int, float]  # horizon (bars) -> mean cross-sectional Spearman
    bucket_means: dict[int, list[float]]  # horizon -> mean fwd return per quintile (low..high)
    n_observations: int
    n_dates: int
    tickers: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    ic_by_horizon: dict[int, ICStats] = field(default_factory=dict)
    benchmark: str | None = None  # returns are excess of this ticker; None = raw returns


def _spearman(scores: pd.Series, returns: pd.Series) -> float:
    return float(scores.rank().corr(returns.rank()))


def _ic_stats(correlations: list[float]) -> ICStats:
    n = len(correlations)
    if n == 0:
        nan = float("nan")
        return ICStats(mean=nan, std=nan, t_stat=nan, n_dates=0, hit_rate=nan)
    series = pd.Series(correlations)
    mean = float(series.mean())
    hit_rate = float(sum(1 for c in correlations if c > 0)) / n
    if n < 2:
        nan = float("nan")
        return ICStats(mean=mean, std=nan, t_stat=nan, n_dates=n, hit_rate=hit_rate)
    std = float(series.std(ddof=1))
    t_stat = mean / (std / math.sqrt(n)) if std > 0 else float("nan")
    return ICStats(mean=mean, std=std, t_stat=t_stat, n_dates=n, hit_rate=hit_rate)


def _quintile_means(scores: pd.Series, returns: pd.Series) -> list[list[float]]:
    """Return per-quintile forward-return lists (quintile 0 = lowest scores)."""
    buckets: list[list[float]] = [[] for _ in range(_QUINTILES)]
    n = len(scores)
    ordinal = scores.rank(method="first")  # 1..n, ties broken by order
    for ticker in scores.index:
        q = min(int((ordinal[ticker] - 1) / n * _QUINTILES), _QUINTILES - 1)
        buckets[q].append(float(returns[ticker]))
    return buckets


def _quintile_date_means(scores: pd.Series, returns: pd.Series) -> list[float]:
    """Mean forward return per quintile for a single cross-sectional date."""
    return [
        float(pd.Series(bucket).mean()) if bucket else float("nan")
        for bucket in _quintile_means(scores, returns)
    ]


def _cross_section_stats(
    groups: list[tuple[pd.Series, pd.Series]],
) -> tuple[ICStats, list[float]]:
    """Summarize per-date (scores, returns) cross-sections.

    Quintile means are averaged per date first, then across dates — pooling
    all observations would let dates with more names dominate.
    """
    correlations: list[float] = []
    date_buckets: list[list[float]] = []
    for scores, returns in groups:
        if len(scores) < _MIN_TICKERS_PER_DATE:
            continue
        if scores.nunique() < 2:
            continue
        corr = _spearman(scores, returns)
        if not pd.isna(corr):
            correlations.append(corr)
        date_buckets.append(_quintile_date_means(scores, returns))

    ic = _ic_stats(correlations)
    if date_buckets:
        frame = pd.DataFrame(date_buckets)
        buckets = [float(frame[q].mean()) for q in range(_QUINTILES)]  # skips NaN
    else:
        buckets = [float("nan")] * _QUINTILES
    return ic, buckets


def _normalize_bar_date(index_value: object) -> pd.Timestamp | None:
    """Coerce a price-frame index value to a naive normalized Timestamp."""
    try:
        ts = pd.Timestamp(index_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


def _load_benchmark_closes(days: int) -> pd.Series | None:
    """Fetch the benchmark's close series indexed by normalized date."""
    bench_df = fetch_price_history([_BENCHMARK_TICKER], days).get(_BENCHMARK_TICKER)
    if bench_df is None or len(bench_df) == 0:
        return None
    dates = [_normalize_bar_date(x) for x in bench_df.index]
    if any(d is None for d in dates):
        return None
    series = pd.Series(bench_df["Close"].to_numpy(), index=pd.DatetimeIndex(dates))
    return series.sort_index()


def _benchmark_fwd_return(
    bench_closes: pd.Series | None,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> float | None:
    if bench_closes is None or start is None or end is None:
        return None
    p0 = bench_closes.asof(start)
    p1 = bench_closes.asof(end)
    if pd.isna(p0) or pd.isna(p1) or float(p0) <= 0:
        return None
    return float(p1) / float(p0) - 1.0


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

    Forward returns are excess of SPY over the same window when SPY data is
    available (raw otherwise). Within a date the subtraction is a constant, so
    the IC is unchanged — the benefit is honest quintile returns that don't
    ride a trending market.
    """
    price_data = fetch_price_history(tickers, days)
    if not price_data:
        return BacktestResult({}, {}, 0, 0, tickers, ["No price data retrieved"])

    bench_closes = _load_benchmark_closes(days)

    max_horizon = max(horizons)
    # cross-sections: {horizon: {calendar_date: {ticker: (score, fwd_return)}}}
    per_date: dict[int, dict[object, dict[str, tuple[float, float]]]] = {
        h: {} for h in horizons
    }
    n_observations = 0
    bench_misses = 0

    for ticker, df in price_data.items():
        closes = df["Close"]
        n = len(df)
        if n <= _WARMUP_BARS + max_horizon:
            logger.info(
                "Skipping %s: %d bars < %d needed", ticker, n, _WARMUP_BARS + max_horizon,
            )
            continue
        bar_dates = [_normalize_bar_date(x) for x in df.index]
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
            # Cross-sections must be keyed by calendar date: bar index only
            # aligns tickers whose histories start on the same day.
            date_key: object = bar_dates[i] if bar_dates[i] is not None else i
            for h in horizons:
                fwd = float(closes.iloc[i + h]) / base - 1.0
                bench_fwd = _benchmark_fwd_return(
                    bench_closes, bar_dates[i], bar_dates[i + h]
                )
                if bench_fwd is not None:
                    fwd -= bench_fwd
                elif bench_closes is not None:
                    bench_misses += 1
                per_date[h].setdefault(date_key, {})[ticker] = (result.score, fwd)
            n_observations += 1

    spearman_by_horizon: dict[int, float] = {}
    ic_by_horizon: dict[int, ICStats] = {}
    bucket_means: dict[int, list[float]] = {}
    n_dates = 0

    for h in horizons:
        groups = [
            (
                pd.Series({t: sr[0] for t, sr in ticker_map.items()}),
                pd.Series({t: sr[1] for t, sr in ticker_map.items()}),
            )
            for _, ticker_map in sorted(per_date[h].items(), key=lambda kv: str(kv[0]))
        ]
        ic, buckets = _cross_section_stats(groups)
        ic_by_horizon[h] = ic
        spearman_by_horizon[h] = ic.mean
        bucket_means[h] = buckets
        n_dates = max(n_dates, ic.n_dates)

    caveats = [
        "Technical pillar only — fundamentals/sentiment aren't available as-of past dates",
        "Universe is today's constituents — delisted names are absent, so results "
        "are survivorship-biased; `backtest --eval-snapshots` is the "
        "survivorship-free path going forward",
    ]
    if step < max_horizon:
        caveats.append(
            "Forward windows overlap (step < horizon), so per-date ICs are "
            "autocorrelated — treat the t-stat as an upper bound"
        )
    if bench_closes is None:
        caveats.append(
            f"{_BENCHMARK_TICKER} data unavailable — returns are raw, not market-relative"
        )
    if bench_misses:
        caveats.append(
            f"{bench_misses} observations lacked {_BENCHMARK_TICKER} bars for their "
            "window and fell back to raw returns"
        )
    return BacktestResult(
        spearman_by_horizon=spearman_by_horizon,
        bucket_means=bucket_means,
        n_observations=n_observations,
        n_dates=n_dates,
        tickers=sorted(price_data.keys()),
        caveats=caveats,
        ic_by_horizon=ic_by_horizon,
        benchmark=_BENCHMARK_TICKER if bench_closes is not None else None,
    )


def _record_score(record: SnapshotRecord, score_key: str) -> float | None:
    """Resolve a pillar field or a components entry from a snapshot record."""
    if score_key in _PILLAR_SCORE_KEYS:
        return float(getattr(record, score_key))
    value = record.components.get(score_key)
    return None if value is None else float(value)


def _load_aged_snapshots(path: Path, min_age_days: int) -> list[SnapshotRecord]:
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
    return aged


def _no_snapshots_result(path: Path, min_age_days: int) -> BacktestResult:
    return BacktestResult(
        {}, {}, 0, 0, [],
        [f"No snapshots older than {min_age_days} days in {path} — keep running "
         "`analyze --snapshot` and re-evaluate later"],
    )


def _fetch_current_prices(aged: list[SnapshotRecord]) -> dict[str, float]:
    tickers = sorted({r.ticker for r in aged} | {_BENCHMARK_TICKER})
    # fetch_price_history drops tickers with <20 bars; 30 calendar days is only
    # ~21 trading days and dips below 20 around holidays — 45 is safe
    price_data = fetch_price_history(tickers, days=45)
    return {
        t: float(df["Close"].iloc[-1]) for t, df in price_data.items() if len(df) > 0
    }


def _evaluate_records(
    aged: list[SnapshotRecord],
    current_prices: dict[str, float],
    score_key: str,
) -> BacktestResult:
    tickers = sorted({r.ticker for r in aged})
    bench_now = current_prices.get(_BENCHMARK_TICKER)

    # Snapshotted tickers with no price today are likely delisted/renamed —
    # dropping them silently would re-introduce survivorship bias, so count them.
    dropped = sorted({r.ticker for r in aged} - set(current_prices))

    # group by snapshot date so the correlation stays cross-sectional
    by_date: dict[str, list[SnapshotRecord]] = {}
    for r in aged:
        if r.ticker in current_prices:
            by_date.setdefault(r.date, []).append(r)

    groups: list[tuple[pd.Series, pd.Series]] = []
    n_observations = 0
    raw_fallbacks = 0
    used_benchmark = False
    for _, recs in sorted(by_date.items()):
        scores: dict[str, float] = {}
        returns: dict[str, float] = {}
        for r in recs:
            score = _record_score(r, score_key)
            if score is None:
                continue
            fwd = current_prices[r.ticker] / r.price - 1.0
            if bench_now is not None and r.benchmark_price > 0:
                fwd -= bench_now / r.benchmark_price - 1.0
                used_benchmark = True
            else:
                raw_fallbacks += 1
            scores[r.ticker] = score
            returns[r.ticker] = fwd
        if len(scores) < _MIN_TICKERS_PER_DATE:
            continue
        groups.append((pd.Series(scores), pd.Series(returns)))
        n_observations += len(scores)

    ic, buckets = _cross_section_stats(groups)

    caveats = [f"Snapshot evaluation of '{score_key}': realized return from snapshot date to now"]
    if dropped:
        caveats.append(
            f"{len(dropped)} snapshotted tickers have no current price "
            f"({', '.join(dropped[:5])}{'…' if len(dropped) > 5 else ''}) — possible "
            "delistings excluded, so results are slightly survivorship-biased"
        )
    if used_benchmark and raw_fallbacks:
        caveats.append(
            f"{raw_fallbacks} records predate benchmark stamping and use raw returns"
        )
    if not used_benchmark:
        caveats.append(
            "No benchmark prices available — returns are raw, not market-relative"
        )
    if ic.n_dates == 0:
        caveats.append(
            f"No snapshot date had >= {_MIN_TICKERS_PER_DATE} tickers with "
            f"'{score_key}' — snapshot the full universe (`analyze --snapshot`) "
            "for meaningful cross-sections"
        )
    return BacktestResult(
        spearman_by_horizon={0: ic.mean},
        bucket_means={0: buckets},
        n_observations=n_observations,
        n_dates=ic.n_dates,
        tickers=tickers,
        caveats=caveats,
        ic_by_horizon={0: ic},
        benchmark=_BENCHMARK_TICKER if used_benchmark else None,
    )


def evaluate_snapshots(
    path: Path = DEFAULT_SNAPSHOT_PATH,
    min_age_days: int = 21,
    score_key: str = "composite",
) -> BacktestResult:
    """Compare past score snapshots against realized returns since.

    `score_key` selects which recorded signal to evaluate: a pillar field
    ("composite", "technical", "fundamental", "sentiment") or any key stored
    in the record's components dict (e.g. "momentum").
    """
    aged = _load_aged_snapshots(path, min_age_days)
    if not aged:
        return _no_snapshots_result(path, min_age_days)
    current_prices = _fetch_current_prices(aged)
    return _evaluate_records(aged, current_prices, score_key)


def evaluate_snapshot_components(
    path: Path = DEFAULT_SNAPSHOT_PATH,
    min_age_days: int = 21,
) -> dict[str, BacktestResult]:
    """Evaluate every recorded signal — pillars plus all components — against
    realized returns, fetching current prices only once. This is the IC table
    that justifies (or vetoes) any future weight changes."""
    aged = _load_aged_snapshots(path, min_age_days)
    if not aged:
        return {"composite": _no_snapshots_result(path, min_age_days)}
    current_prices = _fetch_current_prices(aged)

    component_keys = sorted({key for r in aged for key in r.components})
    keys = [*_PILLAR_SCORE_KEYS, *component_keys]
    return {key: _evaluate_records(aged, current_prices, key) for key in keys}
