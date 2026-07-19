from __future__ import annotations

import pytest

from analysis.fundamental import score_fundamental


class TestScoreFundamental:
    def test_strong_fundamentals_score_high(self) -> None:
        fundamentals = {
            "pe_ratio": 12.0,
            "eps_growth": 0.30,
            "revenue_growth": 0.25,
            "debt_to_equity": 0.2,
            "dividend_yield": 0.035,
        }
        result = score_fundamental(fundamentals)
        assert result.score >= 80
        assert any("undervalued" in r.lower() for r in result.reasons)
        # quality metrics (roe/margin/fcf, 0.30 weight) absent from this fixture
        assert result.completeness == pytest.approx(0.70)

    def test_weak_fundamentals_score_low(self) -> None:
        fundamentals = {
            "pe_ratio": 60.0,
            "eps_growth": -0.20,
            "revenue_growth": -0.10,
            "debt_to_equity": 4.0,
            "dividend_yield": 0.0,
        }
        result = score_fundamental(fundamentals)
        assert result.score <= 25

    def test_score_in_range(self) -> None:
        fundamentals = {
            "pe_ratio": 20.0,
            "eps_growth": 0.05,
            "revenue_growth": 0.05,
            "debt_to_equity": 1.0,
            "dividend_yield": 0.02,
        }
        result = score_fundamental(fundamentals)
        assert 0 <= result.score <= 100

    def test_missing_data_returns_neutral_with_low_completeness(self) -> None:
        result = score_fundamental({})
        assert result.score == pytest.approx(50.0)
        # only dividend_yield (0.10 weight) is scoreable when everything is absent
        assert result.completeness < 0.4

    def test_missing_pe_is_skipped_not_scored_as_negative_earnings(self) -> None:
        fundamentals = {
            "pe_ratio": None,
            "eps_growth": 0.30,
            "revenue_growth": 0.25,
            "debt_to_equity": 0.2,
            "dividend_yield": 0.035,
        }
        result = score_fundamental(fundamentals)
        assert not any("negative earnings" in r.lower() for r in result.reasons)
        assert any("unavailable" in r.lower() for r in result.reasons)
        assert result.completeness == pytest.approx(0.50)

    def test_one_missing_metric_renormalizes_weights(self) -> None:
        full = {
            "pe_ratio": 12.0,
            "eps_growth": 0.30,
            "revenue_growth": 0.25,
            "debt_to_equity": 0.2,
            "dividend_yield": 0.035,
        }
        # all sub-scores are 100 except dividend (75 at 3.5%): removing a
        # 100-scoring metric should not change a renormalized all-else-equal mix
        missing_rev = dict(full, revenue_growth=None)
        r_full = score_fundamental(full)
        r_missing = score_fundamental(missing_rev)
        assert r_missing.completeness < r_full.completeness
        assert r_missing.score == pytest.approx(
            (0.20 * 100 + 0.15 * 100 + 0.10 * 100 + 0.10 * 75) / 0.55, abs=0.1
        )

    def test_negative_pe_handled(self) -> None:
        fundamentals = {
            "pe_ratio": -5.0,
            "eps_growth": 0.0,
            "revenue_growth": 0.0,
            "debt_to_equity": 0.0,
            "dividend_yield": 0.0,
        }
        result = score_fundamental(fundamentals)
        assert any("negative earnings" in r.lower() for r in result.reasons)

    def test_zero_debt_scores_as_low_debt_not_missing(self) -> None:
        fundamentals = {
            "pe_ratio": 18.0,
            "eps_growth": 0.10,
            "revenue_growth": 0.10,
            "debt_to_equity": 0.0,
            "dividend_yield": 0.02,
        }
        result = score_fundamental(fundamentals)
        assert any("very low debt" in r.lower() for r in result.reasons)

    def test_returns_reasoning_strings(self) -> None:
        fundamentals = {
            "pe_ratio": 18.0,
            "eps_growth": 0.15,
            "revenue_growth": 0.08,
            "debt_to_equity": 0.5,
            "dividend_yield": 0.025,
        }
        result = score_fundamental(fundamentals)
        assert len(result.reasons) > 0
        assert all(isinstance(r, str) for r in result.reasons)


class TestRiskWeights:
    def test_every_profile_sums_to_one(self) -> None:
        from analysis.fundamental import _RISK_WEIGHTS

        for profile, weights in _RISK_WEIGHTS.items():
            assert sum(weights.values()) == pytest.approx(1.0), profile


class TestQualityMetrics:
    def test_high_roe_scores_high(self) -> None:
        fundamentals = {"pe_ratio": 18.0, "roe": 0.30, "dividend_yield": 0.0}
        result = score_fundamental(fundamentals)
        assert any("excellent capital efficiency" in r for r in result.reasons)

    def test_negative_roe_scores_low(self) -> None:
        from analysis.fundamental import _score_roe

        assert _score_roe(-0.05) == 15.0
        assert _score_roe(0.30) == 90.0
        assert _score_roe(0.30) > _score_roe(0.10) > _score_roe(-0.05)

    def test_fcf_yield_bands(self) -> None:
        from analysis.fundamental import _score_fcf_yield

        assert _score_fcf_yield(0.08) == 90.0
        assert _score_fcf_yield(0.04) == 75.0
        assert _score_fcf_yield(0.02) == 55.0
        assert _score_fcf_yield(0.0) == 40.0
        assert _score_fcf_yield(-0.02) == 20.0

    def test_profit_margin_fallback_when_no_sector_median(self) -> None:
        fundamentals = {
            "pe_ratio": 18.0, "dividend_yield": 0.0,
            "gross_margin": 0.60, "profit_margin": 0.15,
        }
        result = score_fundamental(fundamentals)
        assert any("Profit margin" in r for r in result.reasons)

    def test_gross_margin_alone_without_sector_median_is_skipped(self) -> None:
        fundamentals = {
            "pe_ratio": 18.0, "dividend_yield": 0.0, "gross_margin": 0.60,
        }
        result = score_fundamental(fundamentals)
        assert any("Margins unavailable" in r for r in result.reasons)

    def test_quality_component_score(self) -> None:
        from analysis.fundamental import quality_component_score

        assert quality_component_score({}) is None
        strong = quality_component_score(
            {"roe": 0.30, "profit_margin": 0.25, "fcf_yield": 0.08}
        )
        weak = quality_component_score(
            {"roe": -0.05, "profit_margin": -0.02, "fcf_yield": -0.03}
        )
        assert strong == pytest.approx(90.0)
        assert weak is not None and strong > weak


class TestForwardPeFallback:
    def test_forward_pe_used_when_trailing_missing(self) -> None:
        fundamentals = {
            "pe_ratio": None, "forward_pe": 12.0, "dividend_yield": 0.0,
            "eps_growth": 0.10, "revenue_growth": 0.10, "debt_to_equity": 0.5,
        }
        result = score_fundamental(fundamentals)
        assert any("forward P/E" in r for r in result.reasons)
        assert any("undervalued" in r.lower() for r in result.reasons)

    def test_trailing_preferred_over_forward(self) -> None:
        fundamentals = {
            "pe_ratio": 18.0, "forward_pe": 60.0, "dividend_yield": 0.0,
        }
        result = score_fundamental(fundamentals)
        assert not any("forward P/E" in r for r in result.reasons)
