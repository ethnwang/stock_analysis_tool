from __future__ import annotations

from datetime import datetime, timezone

from data.models import ScoreResult

_POSITIVE_KEYWORDS = {
    "beat", "beats", "exceeded", "upgrade", "upgrades", "upgraded",
    "growth", "record", "strong", "bullish", "outperform", "outperforms",
    "surge", "surges", "soar", "soars", "rally", "rallies", "gain", "gains",
    "profit", "profitable", "positive", "optimistic", "breakout",
    "buy", "overweight", "raise", "raises", "raised", "innovation",
    "approved", "buyback", "repurchase", "expanding", "acquisition",
    "partnership", "momentum", "accelerating", "rebound", "recovery",
    "launch", "award", "contract", "revenue",
}

_NEGATIVE_KEYWORDS = {
    "miss", "misses", "missed", "downgrade", "downgrades", "downgraded",
    "decline", "declines", "declining", "weak", "bearish", "underperform",
    "underperforms", "lawsuit", "recall", "crash", "crashes", "plunge",
    "plunges", "drop", "drops", "loss", "losses", "sell", "underweight",
    "cut", "cuts", "warning", "warns", "layoff", "layoffs", "bankruptcy",
    "fraud", "investigation", "probe", "fine", "penalty", "debt",
    "rejected", "default", "delisted", "shortage", "subpoena",
    "restructuring", "impairment", "writedown", "disappointing",
    "slowdown", "suspension", "violation",
}

_POSITIVE_PHRASES = {
    "beat estimates", "exceeded expectations", "raised guidance",
    "fda approval", "dividend increase", "all-time high",
    "strong earnings", "record revenue", "stock buyback",
    "share repurchase", "price target raised", "analyst upgrade",
}

_NEGATIVE_PHRASES = {
    "missed expectations", "lowered guidance", "fda rejection",
    "margin pressure", "supply chain", "price target cut",
    "analyst downgrade", "earnings miss", "revenue miss",
    "sec investigation", "class action", "debt default",
}


def _analyze_text(text: str) -> tuple[int, int]:
    lower = text.lower()

    pos = 0
    neg = 0
    matched_words: set[str] = set()

    for phrase in _POSITIVE_PHRASES:
        if phrase in lower:
            pos += 2
            matched_words.update(phrase.split())

    for phrase in _NEGATIVE_PHRASES:
        if phrase in lower:
            neg += 2
            matched_words.update(phrase.split())

    words = set(lower.split()) - matched_words
    pos += len(words & _POSITIVE_KEYWORDS)
    neg += len(words & _NEGATIVE_KEYWORDS)

    return pos, neg


def _recency_weight(article_datetime: str) -> float:
    if not article_datetime:
        return 1.0

    try:
        ts = int(article_datetime)
        article_time = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        try:
            article_time = datetime.fromisoformat(article_datetime)
            if article_time.tzinfo is None:
                article_time = article_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return 1.0

    now = datetime.now(timezone.utc)
    age_days = (now - article_time).total_seconds() / 86400

    if age_days <= 1:
        return 2.0
    if age_days <= 3:
        return 1.0
    return 0.5


def score_sentiment(news: list[dict[str, str]]) -> ScoreResult:
    if not news:
        return ScoreResult(
            50.0, ["No news data available — sentiment neutral"], completeness=0.0,
        )

    total_pos = 0.0
    total_neg = 0.0
    notable: list[str] = []

    for article in news:
        headline = article.get("headline", "")
        summary = article.get("summary", "")
        text = f"{headline} {summary}"

        pos, neg = _analyze_text(text)
        weight = _recency_weight(article.get("datetime", ""))

        total_pos += pos * weight
        total_neg += neg * weight

        if pos > neg and pos >= 2:
            notable.append(f"  + {headline[:80]}")
        elif neg > pos and neg >= 2:
            notable.append(f"  - {headline[:80]}")

    completeness = min(1.0, len(news) / 5)

    total = total_pos + total_neg
    if total == 0:
        return ScoreResult(
            50.0, [f"Analyzed {len(news)} articles — no strong signals"],
            completeness=completeness,
        )

    ratio = total_pos / total
    score = ratio * 100.0

    reasons = [
        f"News sentiment: {total_pos:.0f} positive vs {total_neg:.0f} negative "
        f"signals across {len(news)} articles (recency-weighted)"
    ]
    reasons.extend(notable[:5])

    return ScoreResult(min(max(score, 0.0), 100.0), reasons, completeness=completeness)
