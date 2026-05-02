from __future__ import annotations

import time

from analysis.sentiment import _analyze_text, _recency_weight, score_sentiment


class TestAnalyzeText:
    def test_single_positive_keyword(self) -> None:
        pos, neg = _analyze_text("Stock rally continues")
        assert pos >= 1
        assert neg == 0

    def test_single_negative_keyword(self) -> None:
        pos, neg = _analyze_text("Company announces layoffs")
        assert neg >= 1

    def test_positive_phrase_match(self) -> None:
        pos, neg = _analyze_text("AAPL beat estimates in Q4")
        assert pos >= 2

    def test_negative_phrase_match(self) -> None:
        pos, neg = _analyze_text("Company lowered guidance for next quarter")
        assert neg >= 2

    def test_mixed_signals(self) -> None:
        pos, neg = _analyze_text("Strong growth despite lawsuit concerns")
        assert pos > 0
        assert neg > 0

    def test_no_signals(self) -> None:
        pos, neg = _analyze_text("Company held annual meeting today")
        assert pos == 0
        assert neg == 0

    def test_phrase_scores_higher_than_word(self) -> None:
        _, neg_phrase = _analyze_text("Company missed expectations")
        _, neg_word = _analyze_text("Company missed target")
        assert neg_phrase > neg_word


class TestRecencyWeight:
    def test_recent_article_weighted_higher(self) -> None:
        recent_ts = str(int(time.time()) - 3600)
        weight = _recency_weight(recent_ts)
        assert weight == 2.0

    def test_old_article_weighted_lower(self) -> None:
        old_ts = str(int(time.time()) - 6 * 86400)
        weight = _recency_weight(old_ts)
        assert weight == 0.5

    def test_mid_age_article_normal_weight(self) -> None:
        mid_ts = str(int(time.time()) - 2 * 86400)
        weight = _recency_weight(mid_ts)
        assert weight == 1.0

    def test_empty_datetime_returns_default(self) -> None:
        assert _recency_weight("") == 1.0

    def test_invalid_datetime_returns_default(self) -> None:
        assert _recency_weight("not-a-date") == 1.0


class TestScoreSentiment:
    def test_no_news_returns_neutral(self) -> None:
        score, reasons = score_sentiment([])
        assert score == 50.0
        assert any("neutral" in r.lower() for r in reasons)

    def test_positive_news_scores_above_50(self) -> None:
        news = [
            {"headline": "Company beats estimates with record revenue and strong growth", "summary": ""},
            {"headline": "Analyst upgrade to buy with raised guidance", "summary": ""},
        ]
        score, _ = score_sentiment(news)
        assert score > 50.0

    def test_negative_news_scores_below_50(self) -> None:
        news = [
            {"headline": "Stock crashes after fraud investigation and layoffs", "summary": ""},
            {"headline": "Earnings miss leads to downgrade and sell warning", "summary": ""},
        ]
        score, _ = score_sentiment(news)
        assert score < 50.0

    def test_neutral_news_returns_50(self) -> None:
        news = [
            {"headline": "Company held quarterly meeting today", "summary": ""},
            {"headline": "New office location announced", "summary": ""},
        ]
        score, _ = score_sentiment(news)
        assert score == 50.0

    def test_score_in_range(self) -> None:
        news = [
            {"headline": "beat growth surge rally profit buy", "summary": ""},
        ]
        score, _ = score_sentiment(news)
        assert 0 <= score <= 100

    def test_notable_headlines_in_reasons(self) -> None:
        news = [
            {"headline": "Company beats estimates with record profit margins", "summary": ""},
        ]
        _, reasons = score_sentiment(news)
        assert any("+" in r for r in reasons)

    def test_recency_affects_score(self) -> None:
        recent_ts = str(int(time.time()) - 3600)
        old_ts = str(int(time.time()) - 6 * 86400)

        pos = {"headline": "Strong growth and rally continues", "summary": ""}
        neg = {"headline": "Stock crashes after fraud investigation", "summary": ""}

        pos_recent = [{**pos, "datetime": recent_ts}, {**neg, "datetime": old_ts}]
        neg_recent = [{**pos, "datetime": old_ts}, {**neg, "datetime": recent_ts}]

        score_pos_recent, _ = score_sentiment(pos_recent)
        score_neg_recent, _ = score_sentiment(neg_recent)
        assert score_pos_recent > score_neg_recent
