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
        assert result.completeness == pytest.approx(1.0)

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
        # only dividend_yield (0.15 weight) is scoreable when everything is absent
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
        assert result.completeness == pytest.approx(0.75)

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
            (0.25 * 100 + 0.25 * 100 + 0.15 * 100 + 0.15 * 75) / 0.80, abs=0.1
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
