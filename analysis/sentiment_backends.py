"""Pluggable sentiment backends.

The aggregation pipeline (dedup, recency weighting, Laplace shrinkage) in
`analysis/sentiment.py` is backend-agnostic; a backend only turns each text
into (positive, negative) signal weights. The built-in keyword lexicon is the
zero-dependency default; FinBERT is the accuracy upgrade (~72% vs ~50-60% for
lexicons on financial news) behind the optional `finbert` dependency extra.

Backend selection must never break a cron run: any failure to construct the
configured backend logs a warning and falls back to the lexicon path.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)

_FINBERT_MODEL = "ProsusAI/finbert"

# One confident model classification counts like a strong phrase match (2 pts)
# so FinBERT signals land in the same units the shrinkage math expects.
_MODEL_SIGNAL_SCALE = 2.0


class SentimentBackend(Protocol):
    name: str

    def classify(self, texts: list[str]) -> list[tuple[float, float]]:
        """Return (positive, negative) signal weights for each text."""
        ...


class LexiconBackend:
    """Keyword/phrase lexicon — identical to the built-in default path."""

    name = "lexicon"

    def classify(self, texts: list[str]) -> list[tuple[float, float]]:
        from analysis.sentiment import _analyze_text  # avoid circular import

        return [(float(p), float(n)) for p, n in (_analyze_text(t) for t in texts)]


class FinBERTBackend:
    """ProsusAI/finbert via transformers — free, local, CPU-friendly.

    Install: `pip3 install torch --index-url https://download.pytorch.org/whl/cpu`
    then `pip3 install transformers` (or `pip3 install .[finbert]`).
    First use downloads the ~440MB model to the HuggingFace cache.
    """

    name = "finbert"

    def __init__(self) -> None:
        from transformers import pipeline  # heavy import — deliberately lazy

        self._pipeline = pipeline(
            "text-classification",
            model=_FINBERT_MODEL,
            top_k=None,
            truncation=True,
        )

    def classify(self, texts: list[str]) -> list[tuple[float, float]]:
        if not texts:
            return []
        results = self._pipeline(texts, batch_size=16)
        pairs: list[tuple[float, float]] = []
        for label_scores in results:
            by_label = {s["label"].lower(): float(s["score"]) for s in label_scores}
            pairs.append((
                _MODEL_SIGNAL_SCALE * by_label.get("positive", 0.0),
                _MODEL_SIGNAL_SCALE * by_label.get("negative", 0.0),
            ))
        return pairs


def get_backend(name: str) -> SentimentBackend | None:
    """Resolve the configured backend; None selects the built-in lexicon path.

    Never raises: an unavailable or unknown backend falls back to the lexicon
    with a logged warning so automated runs keep working.
    """
    normalized = (name or "lexicon").strip().lower()
    if normalized in ("", "lexicon"):
        return None
    if normalized == "finbert":
        try:
            return FinBERTBackend()
        except ImportError as exc:
            logger.warning(
                "FinBERT backend unavailable (%s) — falling back to lexicon. "
                "Install with: pip3 install torch --index-url "
                "https://download.pytorch.org/whl/cpu && pip3 install transformers",
                exc,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning(
                "FinBERT model failed to load (%s) — falling back to lexicon", exc,
            )
        return None
    logger.warning("Unknown SENTIMENT_BACKEND %r — using lexicon", name)
    return None
