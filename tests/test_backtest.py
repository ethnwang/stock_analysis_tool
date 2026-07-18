from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.technical import compute_indicators, score_technical
from backtest.engine import _quintile_means, _spearman
from backtest.snapshots import SnapshotRecord, append_snapshots, load_snapshots


def _make_ohlcv(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0.0005, 0.01)))
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": [p * 0.99 for p in prices],
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.98 for p in prices],
            "Close": prices,
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )


class TestSnapshotRoundTrip:
    def _record(self, ticker: str = "TEST") -> SnapshotRecord:
        return SnapshotRecord(
            date="2026-07-18", ticker=ticker, composite=72.5, technical=80.0,
            fundamental=65.0, sentiment=70.0, completeness=0.95, price=150.0,
            universe="watchlist", risk_profile="moderate",
        )

    def test_append_and_load(self, tmp_path) -> None:
        path = tmp_path / "scores.jsonl"
        append_snapshots([self._record("AAA"), self._record("BBB")], path)
        append_snapshots([self._record("CCC")], path)

        loaded = load_snapshots(path)
        assert [r.ticker for r in loaded] == ["AAA", "BBB", "CCC"]
        assert loaded[0] == self._record("AAA")

    def test_load_missing_file_returns_empty(self, tmp_path) -> None:
        assert load_snapshots(tmp_path / "nope.jsonl") == []

    def test_malformed_lines_skipped(self, tmp_path) -> None:
        path = tmp_path / "scores.jsonl"
        append_snapshots([self._record()], path)
        with path.open("a") as f:
            f.write("not json\n")
        append_snapshots([self._record("GOOD")], path)

        loaded = load_snapshots(path)
        assert [r.ticker for r in loaded] == ["TEST", "GOOD"]


class TestNoLookahead:
    def test_future_bars_do_not_change_as_of_score(self) -> None:
        df = _make_ohlcv(400)
        i = 300
        as_of = df.iloc[: i + 1]
        baseline = score_technical(compute_indicators(as_of)).score

        mutated = df.copy()
        mutated.iloc[i + 1 :, mutated.columns.get_loc("Close")] = 1.0
        as_of_mutated = mutated.iloc[: i + 1]
        assert score_technical(compute_indicators(as_of_mutated)).score == baseline


class TestStatistics:
    def test_spearman_monotonic_is_one(self) -> None:
        scores = pd.Series({"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0})
        returns = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04})
        assert _spearman(scores, returns) == pytest.approx(1.0)

    def test_spearman_reversed_is_minus_one(self) -> None:
        scores = pd.Series({"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0})
        returns = pd.Series({"A": 0.04, "B": 0.03, "C": 0.02, "D": 0.01})
        assert _spearman(scores, returns) == pytest.approx(-1.0)

    def test_quintile_assignment(self) -> None:
        scores = pd.Series({f"S{i}": float(i) for i in range(10)})
        returns = pd.Series({f"S{i}": float(i) / 100 for i in range(10)})
        buckets = _quintile_means(scores, returns)
        assert len(buckets) == 5
        assert all(len(b) == 2 for b in buckets)
        # highest-score bucket holds the highest returns
        assert min(buckets[4]) > max(buckets[0])


class TestEvaluateSnapshots:
    def test_no_aged_snapshots_returns_caveat(self, tmp_path, monkeypatch) -> None:
        from backtest import engine

        path = tmp_path / "scores.jsonl"
        append_snapshots(
            [SnapshotRecord(
                date="2099-01-01", ticker="AAA", composite=70.0, technical=70.0,
                fundamental=70.0, sentiment=70.0, completeness=1.0, price=100.0,
                universe="watchlist", risk_profile="moderate",
            )],
            path,
        )
        result = engine.evaluate_snapshots(path, min_age_days=21)
        assert result.n_observations == 0
        assert any("No snapshots older" in c for c in result.caveats)
