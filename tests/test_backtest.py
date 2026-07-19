from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.technical import compute_indicators, score_technical
from backtest.engine import (
    _benchmark_fwd_return,
    _cross_section_stats,
    _ic_stats,
    _quintile_means,
    _spearman,
)
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


class TestICStats:
    def test_mean_std_tstat_hit_rate(self) -> None:
        ic = _ic_stats([0.1, 0.2, 0.3])
        assert ic.mean == pytest.approx(0.2)
        assert ic.std == pytest.approx(0.1)
        assert ic.t_stat == pytest.approx(0.2 / (0.1 / np.sqrt(3)))
        assert ic.hit_rate == pytest.approx(1.0)
        assert ic.n_dates == 3

    def test_single_observation_has_no_std_or_tstat(self) -> None:
        ic = _ic_stats([-0.5])
        assert ic.mean == pytest.approx(-0.5)
        assert np.isnan(ic.std)
        assert np.isnan(ic.t_stat)
        assert ic.hit_rate == pytest.approx(0.0)
        assert ic.n_dates == 1

    def test_empty_is_all_nan(self) -> None:
        ic = _ic_stats([])
        assert np.isnan(ic.mean)
        assert ic.n_dates == 0


class TestCrossSectionStats:
    def test_quintiles_average_per_date_not_pooled(self) -> None:
        # date A: 10 names all returning 10%; date B: 20 names all returning 40%.
        # Per-date-then-average = 25% per bucket; pooling would give 30%.
        date_a = (
            pd.Series({f"A{i}": float(i) for i in range(10)}),
            pd.Series({f"A{i}": 0.10 for i in range(10)}),
        )
        date_b = (
            pd.Series({f"B{i}": float(i) for i in range(20)}),
            pd.Series({f"B{i}": 0.40 for i in range(20)}),
        )
        _, buckets = _cross_section_stats([date_a, date_b])
        assert all(b == pytest.approx(0.25) for b in buckets)

    def test_small_dates_are_skipped(self) -> None:
        tiny = (
            pd.Series({"A": 1.0, "B": 2.0}),
            pd.Series({"A": 0.01, "B": 0.02}),
        )
        ic, buckets = _cross_section_stats([tiny])
        assert ic.n_dates == 0
        assert all(np.isnan(b) for b in buckets)

    def test_monotonic_date_yields_ic_one(self) -> None:
        date = (
            pd.Series({f"S{i}": float(i) for i in range(10)}),
            pd.Series({f"S{i}": float(i) / 100 for i in range(10)}),
        )
        ic, buckets = _cross_section_stats([date, date])
        assert ic.mean == pytest.approx(1.0)
        assert buckets[4] > buckets[0]


class TestBenchmarkReturns:
    def _spy(self) -> pd.Series:
        dates = pd.date_range("2026-01-01", periods=10, freq="B")
        return pd.Series([100.0 + i for i in range(10)], index=dates)

    def test_excess_window_math(self) -> None:
        spy = self._spy()
        ret = _benchmark_fwd_return(spy, spy.index[0], spy.index[5])
        assert ret == pytest.approx(105.0 / 100.0 - 1.0)

    def test_none_inputs_return_none(self) -> None:
        spy = self._spy()
        assert _benchmark_fwd_return(None, spy.index[0], spy.index[5]) is None
        assert _benchmark_fwd_return(spy, None, spy.index[5]) is None
        assert _benchmark_fwd_return(spy, spy.index[0], None) is None

    def test_date_before_series_returns_none(self) -> None:
        spy = self._spy()
        early = spy.index[0] - pd.Timedelta(days=30)
        assert _benchmark_fwd_return(spy, early, spy.index[5]) is None


class TestSnapshotSchemaCompat:
    def test_old_format_line_loads_with_defaults(self, tmp_path) -> None:
        path = tmp_path / "scores.jsonl"
        old = ('{"date": "2026-07-18", "ticker": "AAA", "composite": 70.0, '
               '"technical": 70.0, "fundamental": 70.0, "sentiment": 70.0, '
               '"completeness": 1.0, "price": 100.0, "universe": "watchlist", '
               '"risk_profile": "moderate"}')
        path.write_text(old + "\n")
        loaded = load_snapshots(path)
        assert len(loaded) == 1
        assert loaded[0].benchmark_price == 0.0
        assert loaded[0].components == {}

    def test_unknown_keys_are_ignored(self, tmp_path) -> None:
        path = tmp_path / "scores.jsonl"
        future = ('{"date": "2026-07-18", "ticker": "AAA", "composite": 70.0, '
                  '"technical": 70.0, "fundamental": 70.0, "sentiment": 70.0, '
                  '"completeness": 1.0, "price": 100.0, "universe": "watchlist", '
                  '"risk_profile": "moderate", "some_future_field": 42}')
        path.write_text(future + "\n")
        loaded = load_snapshots(path)
        assert len(loaded) == 1
        assert loaded[0].ticker == "AAA"

    def test_components_round_trip(self, tmp_path) -> None:
        path = tmp_path / "scores.jsonl"
        record = SnapshotRecord(
            date="2026-07-18", ticker="AAA", composite=70.0, technical=70.0,
            fundamental=70.0, sentiment=70.0, completeness=1.0, price=100.0,
            universe="watchlist", risk_profile="moderate",
            benchmark_price=500.0, components={"momentum": 82.5},
        )
        append_snapshots([record], path)
        loaded = load_snapshots(path)
        assert loaded[0].benchmark_price == 500.0
        assert loaded[0].components == {"momentum": 82.5}


def _price_frame(price: float) -> pd.DataFrame:
    dates = pd.date_range(end=pd.Timestamp.now(), periods=5, freq="B")
    return pd.DataFrame({"Close": [price] * 5}, index=dates)


class TestBenchmarkStamp:
    def test_fetch_window_covers_min_bar_requirement(self, monkeypatch) -> None:
        # fetch_price_history silently drops tickers with <20 bars, so any
        # window near 20 trading days (~30 calendar days) loses SPY entirely
        import main as main_mod
        from data import fetcher

        captured: dict[str, int] = {}

        def fake_fetch(tickers, days):
            captured["days"] = days
            return {"SPY": _price_frame(743.0)}

        monkeypatch.setattr(fetcher, "fetch_price_history", fake_fetch)
        assert main_mod._fetch_benchmark_close() == 743.0
        assert captured["days"] >= 45

    def test_returns_zero_when_spy_missing(self, monkeypatch) -> None:
        import main as main_mod
        from data import fetcher

        monkeypatch.setattr(fetcher, "fetch_price_history", lambda t, days: {})
        assert main_mod._fetch_benchmark_close() == 0.0


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

    def _aged_records(self, n: int = 9) -> list[SnapshotRecord]:
        return [
            SnapshotRecord(
                date="2026-01-02", ticker=f"T{i}", composite=10.0 * i,
                technical=50.0, fundamental=50.0, sentiment=50.0,
                completeness=1.0, price=100.0, universe="watchlist",
                risk_profile="moderate", benchmark_price=500.0,
                components={"momentum": 5.0 * i},
            )
            for i in range(n)
        ]

    def test_excess_returns_and_dropped_ticker_caveat(self, tmp_path, monkeypatch) -> None:
        from backtest import engine

        path = tmp_path / "scores.jsonl"
        append_snapshots(self._aged_records(9), path)

        # Current prices for T0..T7 rise with the snapshot score; T8 is missing
        # (delisted). SPY at 510 makes the benchmark return +2%.
        prices = {f"T{i}": _price_frame(100.0 * (1 + 0.01 * i)) for i in range(8)}
        prices["SPY"] = _price_frame(510.0)
        monkeypatch.setattr(engine, "fetch_price_history", lambda tickers, days: prices)

        result = engine.evaluate_snapshots(path, min_age_days=21)
        assert result.n_dates == 1
        assert result.n_observations == 8
        assert result.benchmark == "SPY"
        # Monotonic scores vs returns — excess shift doesn't change ranks
        assert result.ic_by_horizon[0].mean == pytest.approx(1.0)
        # Lowest bucket holds T0 (excess -2%) and T1 (excess -1%)
        assert result.bucket_means[0][0] == pytest.approx(-0.015, abs=1e-9)
        assert any("no current price" in c for c in result.caveats)

    def test_component_score_key(self, tmp_path, monkeypatch) -> None:
        from backtest import engine

        path = tmp_path / "scores.jsonl"
        records = self._aged_records(8)
        # One record without the component — must be skipped, not crash
        records.append(SnapshotRecord(
            date="2026-01-02", ticker="T8", composite=90.0, technical=50.0,
            fundamental=50.0, sentiment=50.0, completeness=1.0, price=100.0,
            universe="watchlist", risk_profile="moderate", benchmark_price=500.0,
        ))
        append_snapshots(records, path)

        prices = {f"T{i}": _price_frame(100.0 * (1 + 0.01 * i)) for i in range(9)}
        prices["SPY"] = _price_frame(510.0)
        monkeypatch.setattr(engine, "fetch_price_history", lambda tickers, days: prices)

        result = engine.evaluate_snapshots(path, min_age_days=21, score_key="momentum")
        assert result.n_observations == 8
        assert result.ic_by_horizon[0].mean == pytest.approx(1.0)

    def test_by_component_evaluates_every_signal_with_one_fetch(
        self, tmp_path, monkeypatch,
    ) -> None:
        from backtest import engine

        path = tmp_path / "scores.jsonl"
        append_snapshots(self._aged_records(9), path)

        prices = {f"T{i}": _price_frame(100.0 * (1 + 0.01 * i)) for i in range(9)}
        prices["SPY"] = _price_frame(510.0)
        fetch_calls = []

        def _fake_fetch(tickers, days):
            fetch_calls.append(tickers)
            return prices

        monkeypatch.setattr(engine, "fetch_price_history", _fake_fetch)

        results = engine.evaluate_snapshot_components(path, min_age_days=21)
        assert len(fetch_calls) == 1  # prices fetched once, not per signal
        assert set(results) == {
            "composite", "technical", "fundamental", "sentiment", "momentum",
        }
        assert results["composite"].ic_by_horizon[0].mean == pytest.approx(1.0)
        assert results["momentum"].ic_by_horizon[0].mean == pytest.approx(1.0)
        # technical is constant 50 across records -> no rankable cross-section
        assert results["technical"].n_dates == 0

    def test_by_component_no_aged_snapshots(self, tmp_path) -> None:
        from backtest import engine

        results = engine.evaluate_snapshot_components(
            tmp_path / "none.jsonl", min_age_days=21,
        )
        assert set(results) == {"composite"}
        assert results["composite"].n_observations == 0

    def test_raw_fallback_without_benchmark_price(self, tmp_path, monkeypatch) -> None:
        from backtest import engine

        path = tmp_path / "scores.jsonl"
        records = [
            SnapshotRecord(
                date="2026-01-02", ticker=f"T{i}", composite=10.0 * i,
                technical=50.0, fundamental=50.0, sentiment=50.0,
                completeness=1.0, price=100.0, universe="watchlist",
                risk_profile="moderate",  # benchmark_price defaults to 0.0
            )
            for i in range(8)
        ]
        append_snapshots(records, path)

        prices = {f"T{i}": _price_frame(100.0 * (1 + 0.01 * i)) for i in range(8)}
        prices["SPY"] = _price_frame(510.0)
        monkeypatch.setattr(engine, "fetch_price_history", lambda tickers, days: prices)

        result = engine.evaluate_snapshots(path, min_age_days=21)
        assert result.benchmark is None
        # Lowest bucket holds T0 (raw 0%) and T1 (raw 1%)
        assert result.bucket_means[0][0] == pytest.approx(0.005, abs=1e-9)
        assert any("raw" in c for c in result.caveats)
