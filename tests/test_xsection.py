from __future__ import annotations

import json

import pytest

from analysis.xsection import (
    FactorStats,
    _percentile_share,
    blend_with_percentile,
    build_factor_stats,
    compute_momentum_12_1,
    load_factor_stats,
    percentile_score,
    save_factor_stats,
    sentiment_offset,
)
from tests.conftest import default_config, make_etf, make_stock

_BREAKPOINTS = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]


class TestPercentileScore:
    def test_median_value_scores_fifty(self) -> None:
        assert percentile_score(50.0, _BREAKPOINTS, higher_is_better=True) == pytest.approx(50.0)

    def test_interpolates_between_deciles(self) -> None:
        assert percentile_score(35.0, _BREAKPOINTS, higher_is_better=True) == pytest.approx(35.0)

    def test_clamps_below_first_decile(self) -> None:
        assert percentile_score(-100.0, _BREAKPOINTS, higher_is_better=True) == pytest.approx(10.0)

    def test_clamps_above_last_decile(self) -> None:
        assert percentile_score(1000.0, _BREAKPOINTS, higher_is_better=True) == pytest.approx(90.0)

    def test_lower_is_better_inverts(self) -> None:
        # A cheap P/E (low value) must score high
        assert percentile_score(10.0, _BREAKPOINTS, higher_is_better=False) == pytest.approx(90.0)
        assert percentile_score(90.0, _BREAKPOINTS, higher_is_better=False) == pytest.approx(10.0)


class TestBlendRamp:
    def test_small_batch_uses_bands_only(self) -> None:
        assert _percentile_share(10) == 0.0
        assert _percentile_share(20) == 0.0

    def test_ramp_midpoint(self) -> None:
        assert _percentile_share(60) == pytest.approx(0.25)

    def test_full_universe_gets_half_share(self) -> None:
        assert _percentile_share(100) == pytest.approx(0.5)
        assert _percentile_share(500) == pytest.approx(0.5)


class TestBlendWithPercentile:
    def _stats(self, n: int = 500) -> FactorStats:
        return FactorStats(
            breakpoints={"mom_12_1": [i / 100 for i in range(-20, 70, 10)]},
            n=n, as_of="2026-07-19",
        )

    def test_no_stats_returns_band(self) -> None:
        assert blend_with_percentile(70.0, "mom_12_1", 0.2, None) == 70.0

    def test_unknown_factor_returns_band(self) -> None:
        assert blend_with_percentile(70.0, "pe_ratio", 20.0, self._stats()) == 70.0

    def test_small_batch_returns_band(self) -> None:
        assert blend_with_percentile(70.0, "mom_12_1", 0.2, self._stats(n=10)) == 70.0

    def test_blend_math(self) -> None:
        # value 0.2 sits at the 50th percentile of these breakpoints
        blended = blend_with_percentile(70.0, "mom_12_1", 0.2, self._stats())
        assert blended == pytest.approx(0.5 * 70.0 + 0.5 * 50.0)

    def test_blending_can_reorder_equal_bands(self) -> None:
        # Both in the same band (score 70) but very different cross-sectional
        # standing — blending must separate them, in the right order
        weak = blend_with_percentile(70.0, "mom_12_1", 0.11, self._stats())
        strong = blend_with_percentile(70.0, "mom_12_1", 0.29, self._stats())
        assert strong > weak


class TestBuildFactorStats:
    def test_builds_breakpoints_for_large_batch(self) -> None:
        stocks = [make_stock(f"S{i}", pe=10.0 + i) for i in range(30)]
        stats = build_factor_stats(stocks)
        assert stats.n == 30
        assert stats.has("pe_ratio")
        assert stats.has("roe")
        assert stats.has("mom_12_1")
        assert stats.breakpoints["pe_ratio"] == sorted(stats.breakpoints["pe_ratio"])

    def test_small_batch_has_no_breakpoints(self) -> None:
        stocks = [make_stock(f"S{i}") for i in range(5)]
        stats = build_factor_stats(stocks)
        assert stats.breakpoints == {}

    def test_etfs_and_negative_pe_excluded(self) -> None:
        stocks = [make_stock(f"S{i}", pe=-5.0) for i in range(25)]
        stocks.extend(make_etf(f"E{i}") for i in range(5))
        stats = build_factor_stats(stocks)
        assert stats.n == 25  # ETFs don't count
        assert not stats.has("pe_ratio")  # negative P/Es excluded


class TestCache:
    def test_round_trip(self, tmp_path) -> None:
        path = tmp_path / "stats.json"
        stats = FactorStats(
            breakpoints={"roe": [0.05 * i for i in range(1, 10)]},
            n=480, as_of="2026-07-19",
        )
        save_factor_stats(stats, path)
        loaded = load_factor_stats(path)
        assert loaded == stats

    def test_missing_file_returns_none(self, tmp_path) -> None:
        assert load_factor_stats(tmp_path / "nope.json") is None

    def test_stale_cache_ignored(self, tmp_path) -> None:
        path = tmp_path / "stats.json"
        stats = FactorStats(
            breakpoints={"roe": [0.05] * 9}, n=480, as_of="2025-01-01",
        )
        save_factor_stats(stats, path)
        assert load_factor_stats(path) is None

    def test_malformed_cache_ignored(self, tmp_path) -> None:
        path = tmp_path / "stats.json"
        path.write_text("not json at all")
        assert load_factor_stats(path) is None
        path.write_text(json.dumps({"unexpected": "shape"}))
        assert load_factor_stats(path) is None

    def test_empty_breakpoints_not_saved(self, tmp_path) -> None:
        path = tmp_path / "stats.json"
        save_factor_stats(FactorStats(breakpoints={}, n=3, as_of="2026-07-19"), path)
        assert not path.exists()


class TestSentimentOffset:
    def test_empty_returns_zero(self) -> None:
        assert sentiment_offset([]) == 0.0

    def test_positive_skew_produces_negative_offset(self) -> None:
        assert sentiment_offset([65.0, 70.0, 75.0]) == pytest.approx(-20.0)

    def test_centered_batch_needs_no_offset(self) -> None:
        assert sentiment_offset([40.0, 50.0, 60.0]) == pytest.approx(0.0)


class TestMomentumHelper:
    def test_needs_252_bars(self) -> None:
        import pandas as pd

        assert compute_momentum_12_1(pd.Series([100.0] * 251)) is None
        assert compute_momentum_12_1(pd.Series([100.0] * 252)) is not None


class TestScoreStockIntegration:
    def test_pre_xs_composite_recorded_when_blending(self) -> None:
        from scoring.engine import score_stock

        stats = FactorStats(
            breakpoints={"pe_ratio": [10.0 + 2 * i for i in range(9)]},
            n=500, as_of="2026-07-19",
        )
        result = score_stock(make_stock(), default_config(), factor_stats=stats)
        assert "composite_pre_xs" in result.components

    def test_no_blending_means_no_continuity_component(self) -> None:
        from scoring.engine import score_stock

        result = score_stock(make_stock(), default_config())
        assert "composite_pre_xs" not in result.components

    def test_rank_stocks_classifies_each_stock_exactly_once(self, monkeypatch) -> None:
        from scoring import engine as scoring_engine

        classify_calls: list[int] = []

        class CountingBackend:
            name = "counting"

            def classify(self, texts: list[str]) -> list[tuple[float, float]]:
                classify_calls.append(len(texts))
                return [(1.0, 0.0)] * len(texts)

        monkeypatch.setattr(
            scoring_engine, "get_backend", lambda name: CountingBackend(),
        )
        stocks = []
        for i in range(3):
            stock = make_stock(f"S{i}")
            stock.news = [{"headline": f"Headline about S{i}", "summary": ""}]
            stocks.append(stock)

        scoring_engine.rank_stocks(stocks, default_config(top_n=5))
        # One classify call per stock (the recentering pre-pass result is
        # reused by score_stock), not two
        assert len(classify_calls) == 3

    def test_sentiment_offset_recenters_and_records_raw(self) -> None:
        from scoring.engine import score_stock

        stock = make_stock()
        stock.news = [
            {"title": "Company beats estimates and raised guidance",
             "summary": "strong quarter", "published": "", "source": "x"},
        ] * 5
        base = score_stock(stock, default_config())
        shifted = score_stock(
            stock, default_config(), batch_sentiment_offset=-15.0,
        )
        assert shifted.components["sentiment_raw"] == pytest.approx(base.sentiment_score)
        assert shifted.sentiment_score == pytest.approx(base.sentiment_score - 15.0)
