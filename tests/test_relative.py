from __future__ import annotations

import pytest

from analysis.fundamental import score_fundamental_adjusted
from analysis.relative import MIN_SECTOR_PEERS, SectorStats, build_sector_stats
from scoring.engine import rank_stocks
from tests.conftest import default_config, make_stock


def _tech_batch(n: int = 6) -> list:
    # pe spread 10..35 → median depends on n; all Technology sector
    return [
        make_stock(f"T{i}", pe=10.0 + 5 * i, eps_growth=0.10, rev_growth=0.10)
        for i in range(n)
    ]


class TestBuildSectorStats:
    def test_median_computed_per_sector(self) -> None:
        stats = build_sector_stats(_tech_batch(5))
        # pe values 10, 15, 20, 25, 30 → median 20
        assert stats.get("Technology", "pe_ratio") == pytest.approx(20.0)

    def test_min_peers_gate(self) -> None:
        stats = build_sector_stats(_tech_batch(MIN_SECTOR_PEERS - 1))
        assert stats.get("Technology", "pe_ratio") is None

    def test_unknown_sector_returns_none(self) -> None:
        stats = build_sector_stats(_tech_batch(6))
        assert stats.get("Unknown", "pe_ratio") is None

    def test_none_and_negative_pe_excluded_from_median(self) -> None:
        stocks = _tech_batch(5)
        broken = make_stock("BROKEN")
        broken.fundamentals["pe_ratio"] = None
        negative = make_stock("NEG")
        negative.fundamentals["pe_ratio"] = -8.0
        stats = build_sector_stats(stocks + [broken, negative])
        # median over the 5 real P/Es only (10,15,20,25,30)
        assert stats.get("Technology", "pe_ratio") == pytest.approx(20.0)

    def test_etfs_excluded(self) -> None:
        stocks = _tech_batch(4)
        etf_like = make_stock("ETFX")
        etf_like.fundamentals["is_etf"] = 1.0
        stats = build_sector_stats(stocks + [etf_like])
        assert stats.counts.get("Technology", 0) == 4


class TestRelativeScoring:
    def _stats(self, pe_median: float = 25.0, de_median: float = 1.0) -> SectorStats:
        return SectorStats(
            medians={"Technology": {
                "pe_ratio": pe_median, "debt_to_equity": de_median,
                "eps_growth": 0.10, "revenue_growth": 0.10,
            }},
            counts={"Technology": 10},
        )

    def _fundamentals(self, pe: float = 25.0, de: float = 1.0) -> dict:
        return {
            "pe_ratio": pe, "eps_growth": 0.10, "revenue_growth": 0.10,
            "debt_to_equity": de, "dividend_yield": 0.02,
        }

    def test_cheap_vs_sector_beats_expensive_vs_sector(self) -> None:
        stats = self._stats(pe_median=30.0)
        cheap = score_fundamental_adjusted(
            self._fundamentals(pe=15.0), sector_stats=stats, sector="Technology",
        )
        rich = score_fundamental_adjusted(
            self._fundamentals(pe=55.0), sector_stats=stats, sector="Technology",
        )
        assert cheap.score > rich.score
        assert any("sector median" in r for r in cheap.reasons)

    def test_high_absolute_pe_ok_if_sector_norm(self) -> None:
        # P/E 40 in a sector with median 42: near norm (55), whereas the
        # absolute band would call it "expensive" (25)
        stats = self._stats(pe_median=42.0)
        relative = score_fundamental_adjusted(
            self._fundamentals(pe=40.0), sector_stats=stats, sector="Technology",
        )
        absolute = score_fundamental_adjusted(self._fundamentals(pe=40.0))
        assert relative.score > absolute.score

    def test_fallback_to_absolute_without_stats(self) -> None:
        result = score_fundamental_adjusted(self._fundamentals(pe=12.0))
        assert any("undervalued" in r for r in result.reasons)

    def test_fallback_when_sector_not_in_stats(self) -> None:
        stats = self._stats()
        result = score_fundamental_adjusted(
            self._fundamentals(pe=12.0), sector_stats=stats, sector="Utilities",
        )
        assert any("undervalued" in r for r in result.reasons)

    def test_negative_pe_keeps_absolute_branch(self) -> None:
        stats = self._stats()
        result = score_fundamental_adjusted(
            self._fundamentals(pe=-5.0), sector_stats=stats, sector="Technology",
        )
        assert any("negative earnings" in r for r in result.reasons)

    def test_growth_adjustment_clamped_and_cited(self) -> None:
        stats = SectorStats(
            medians={"Technology": {"eps_growth": 0.02, "revenue_growth": 0.02}},
            counts={"Technology": 10},
        )
        result = score_fundamental_adjusted(
            self._fundamentals(), sector_stats=stats, sector="Technology",
        )
        assert any("above sector median" in r for r in result.reasons)


class TestEngineIntegration:
    def test_rank_stocks_builds_and_applies_sector_stats(self) -> None:
        stocks = _tech_batch(6)
        config = default_config(top_n=6)
        ranked = rank_stocks(stocks, config)
        assert any(
            "sector median" in r for s in ranked for r in s.reasoning
        )
