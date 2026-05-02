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
        score, reasons = score_fundamental(fundamentals)
        assert score >= 80
        assert any("undervalued" in r.lower() for r in reasons)

    def test_weak_fundamentals_score_low(self) -> None:
        fundamentals = {
            "pe_ratio": 60.0,
            "eps_growth": -0.20,
            "revenue_growth": -0.10,
            "debt_to_equity": 4.0,
            "dividend_yield": 0.0,
        }
        score, reasons = score_fundamental(fundamentals)
        assert score <= 25

    def test_score_in_range(self) -> None:
        fundamentals = {
            "pe_ratio": 20.0,
            "eps_growth": 0.05,
            "revenue_growth": 0.05,
            "debt_to_equity": 1.0,
            "dividend_yield": 0.02,
        }
        score, reasons = score_fundamental(fundamentals)
        assert 0 <= score <= 100

    def test_missing_data_returns_neutral(self) -> None:
        score, reasons = score_fundamental({})
        assert 15 <= score <= 50

    def test_negative_pe_handled(self) -> None:
        fundamentals = {
            "pe_ratio": -5.0,
            "eps_growth": 0.0,
            "revenue_growth": 0.0,
            "debt_to_equity": 0.0,
            "dividend_yield": 0.0,
        }
        score, reasons = score_fundamental(fundamentals)
        assert any("negative earnings" in r.lower() for r in reasons)

    def test_returns_reasoning_strings(self) -> None:
        fundamentals = {
            "pe_ratio": 18.0,
            "eps_growth": 0.15,
            "revenue_growth": 0.08,
            "debt_to_equity": 0.5,
            "dividend_yield": 0.025,
        }
        score, reasons = score_fundamental(fundamentals)
        assert len(reasons) > 0
        assert all(isinstance(r, str) for r in reasons)
