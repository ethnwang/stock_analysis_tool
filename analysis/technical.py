from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator, SMAIndicator
from ta.volatility import BollingerBands

from analysis.xsection import (
    MOMENTUM_LOOKBACK_BARS,
    MOMENTUM_SKIP_BARS,
    FactorStats,
    blend_with_percentile,
    compute_momentum_12_1,
)
from data.models import ScoreResult

_WEIGHTS = {
    "momentum": 0.25,
    "macd": 0.20,
    "rsi": 0.15,
    "sma": 0.15,
    "bb": 0.10,
    "volume": 0.10,
    "adx": 0.05,
}

# 12-1 momentum: return from 12 months ago to 1 month ago. The most recent
# month is skipped because of short-term reversal (last month's winners tend
# to give some back); 252/21 trading bars ≈ 12/1 calendar months.
_MOMENTUM_LOOKBACK_BARS = MOMENTUM_LOOKBACK_BARS
_MOMENTUM_SKIP_BARS = MOMENTUM_SKIP_BARS

# Below this fraction of available indicator weight, a technical score would
# rest on too little signal — return neutral instead of pretending confidence.
_MIN_AVAILABLE_WEIGHT = 0.4

# Realized volatility: annualized std of daily returns over ~3 months
_VOL_WINDOW_BARS = 63
_VOL_MIN_OBSERVATIONS = 20


def _nan_to_none(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def compute_realized_vol(close: pd.Series) -> float | None:
    """Annualized std of the last ~3 months of daily returns."""
    daily_returns = close.pct_change().iloc[-_VOL_WINDOW_BARS:].dropna()
    if len(daily_returns) < _VOL_MIN_OBSERVATIONS:
        return None
    return float(daily_returns.std() * np.sqrt(252))


def compute_indicators(df: pd.DataFrame) -> dict[str, float | str | None]:
    close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
    high = df["High"].squeeze() if isinstance(df["High"], pd.DataFrame) else df["High"]
    low = df["Low"].squeeze() if isinstance(df["Low"], pd.DataFrame) else df["Low"]
    volume = df["Volume"].squeeze() if isinstance(df["Volume"], pd.DataFrame) else df["Volume"]

    rsi_ind = RSIIndicator(close=close, window=14)
    rsi = _nan_to_none(rsi_ind.rsi().iloc[-1])

    macd_ind = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = _nan_to_none(macd_ind.macd().iloc[-1])
    macd_signal = _nan_to_none(macd_ind.macd_signal().iloc[-1])
    macd_hist_series = macd_ind.macd_diff()
    macd_hist = _nan_to_none(macd_hist_series.iloc[-1])
    # 3-bar slope: a single histogram tick is noise, not momentum direction
    macd_hist_prev = (
        _nan_to_none(macd_hist_series.iloc[-3]) if len(macd_hist_series) >= 4 else None
    )

    bb = BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = _nan_to_none(bb.bollinger_hband().iloc[-1])
    bb_lower = _nan_to_none(bb.bollinger_lband().iloc[-1])
    bb_pct = _nan_to_none(bb.bollinger_pband().iloc[-1])

    sma50 = _nan_to_none(SMAIndicator(close=close, window=50).sma_indicator().iloc[-1])
    sma200 = _nan_to_none(SMAIndicator(close=close, window=200).sma_indicator().iloc[-1])
    current_price = float(close.iloc[-1])

    adx_ind = ADXIndicator(high=high, low=low, close=close, window=14)
    adx = _nan_to_none(adx_ind.adx().iloc[-1])
    if adx == 0.0:
        # ta's ADX emits 0.0 during its warm-up window instead of NaN
        adx = None

    vol_recent = float(volume.iloc[-5:].mean())
    vol_avg = float(volume.iloc[-20:].mean())
    volume_ratio = vol_recent / vol_avg if vol_avg > 0 else None

    macd_direction: str | None = None
    if macd_hist is not None and macd_hist_prev is not None:
        macd_direction = "bullish" if macd_hist > macd_hist_prev else "bearish"

    return {
        "mom_12_1": compute_momentum_12_1(close),
        "realized_vol": compute_realized_vol(close),
        "rsi": rsi,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "macd_direction": macd_direction,
        "bb_pct": bb_pct,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "sma50": sma50,
        "sma200": sma200,
        "adx": adx,
        "volume_ratio": volume_ratio,
        "current_price": current_price,
    }


def _is_bullish_trend(
    macd_line: float | None,
    macd_signal: float | None,
    sma50: float | None,
    sma200: float | None,
) -> bool | None:
    signals: list[bool] = []
    if macd_line is not None and macd_signal is not None:
        signals.append(macd_line > macd_signal)
    if sma50 is not None and sma200 is not None and sma50 > 0 and sma200 > 0:
        signals.append(sma50 > sma200)
    if not signals:
        return None
    return any(signals)


def _score_rsi(rsi: float, reasons: list[str]) -> float:
    if rsi < 30:
        reasons.append(f"RSI {rsi:.0f} — oversold (bullish)")
        return 100.0
    if rsi < 40:
        reasons.append(f"RSI {rsi:.0f} — approaching oversold")
        return 75.0
    if rsi < 60:
        return 50.0
    if rsi < 70:
        reasons.append(f"RSI {rsi:.0f} — approaching overbought")
        return 25.0
    reasons.append(f"RSI {rsi:.0f} — overbought (bearish)")
    return 0.0


def _score_macd(
    macd_line: float, macd_signal: float, macd_dir: str | None, reasons: list[str],
) -> float:
    if macd_line > macd_signal and macd_dir == "bullish":
        reasons.append("MACD bullish crossover with rising momentum")
        return 100.0
    if macd_line > macd_signal:
        reasons.append("MACD above signal line")
        return 70.0
    if macd_line < macd_signal and macd_dir == "bearish":
        reasons.append("MACD bearish crossover with falling momentum")
        return 0.0
    if macd_line < macd_signal:
        reasons.append("MACD below signal line")
        return 30.0
    return 50.0


def _score_bb(bb_pct: float, reasons: list[str]) -> float:
    if bb_pct < 0.0:
        reasons.append("Price below lower Bollinger Band (potential buy)")
        return 100.0
    if bb_pct < 0.2:
        reasons.append("Price near lower Bollinger Band")
        return 80.0
    if bb_pct < 0.8:
        return 50.0
    if bb_pct < 1.0:
        reasons.append("Price near upper Bollinger Band")
        return 20.0
    reasons.append("Price above upper Bollinger Band (overbought)")
    return 0.0


def _score_sma(sma50: float, sma200: float, reasons: list[str]) -> float:
    sma_ratio = sma50 / sma200
    if sma_ratio > 1.02:
        reasons.append("Golden cross — SMA 50 above SMA 200 (bullish)")
        return 100.0
    if sma_ratio > 1.0:
        reasons.append("SMA 50 slightly above SMA 200")
        return 70.0
    if sma_ratio > 0.98:
        reasons.append("SMA 50 near SMA 200 (neutral)")
        return 40.0
    reasons.append("Death cross — SMA 50 below SMA 200 (bearish)")
    return 0.0


def _score_adx(adx: float, bullish: bool | None, reasons: list[str]) -> float:
    if adx > 25:
        if bullish is None:
            reasons.append(f"ADX {adx:.0f} — strong trend, direction unclear")
            return 50.0
        if bullish:
            reasons.append(f"ADX {adx:.0f} — strong bullish trend")
            return 90.0
        reasons.append(f"ADX {adx:.0f} — strong bearish trend")
        return 20.0
    if adx > 20:
        return 50.0
    reasons.append(f"ADX {adx:.0f} — weak/no trend")
    return 40.0


def _score_volume(vol_ratio: float, bullish: bool | None, reasons: list[str]) -> float:
    if vol_ratio > 1.5:
        if bullish is None:
            return 60.0
        if bullish:
            reasons.append(f"Volume {vol_ratio:.1f}x above average (confirms bullish move)")
            return 90.0
        reasons.append(f"Volume {vol_ratio:.1f}x above average (confirms bearish move)")
        return 15.0
    if vol_ratio > 1.0:
        return 60.0
    if vol_ratio > 0.5:
        return 40.0
    reasons.append(f"Volume {vol_ratio:.1f}x below average (low interest)")
    return 20.0


def score_momentum(mom: float, reasons: list[str] | None = None) -> float:
    """Score 12-1 momentum with monotonic bands — it's a continuation signal,
    unlike RSI's mean-reversion bands."""
    if mom > 0.30:
        score, label = 90.0, "strong relative strength"
    elif mom > 0.10:
        score, label = 70.0, "solid relative strength"
    elif mom > -0.05:
        score, label = 50.0, "flat — no momentum edge"
    elif mom > -0.20:
        score, label = 30.0, "weak relative strength"
    else:
        score, label = 10.0, "deeply negative momentum"
    if reasons is not None:
        reasons.append(f"12-1 momentum {mom:+.0%} — {label}")
    return score


def score_technical(
    indicators: dict[str, float | str | None],
    factor_stats: FactorStats | None = None,
) -> ScoreResult:
    reasons: list[str] = []
    weighted_sum = 0.0
    available_weight = 0.0

    rsi = indicators["rsi"]
    macd_line = indicators["macd_line"]
    macd_signal = indicators["macd_signal"]
    macd_dir = indicators["macd_direction"]
    bb_pct = indicators["bb_pct"]
    sma50 = indicators["sma50"]
    sma200 = indicators["sma200"]
    adx = indicators["adx"]
    vol_ratio = indicators["volume_ratio"]

    # .get(): older callers build indicator dicts by hand without this key
    mom = indicators.get("mom_12_1")

    bullish = _is_bullish_trend(macd_line, macd_signal, sma50, sma200)  # type: ignore[arg-type]

    if mom is not None:
        mom_score = blend_with_percentile(
            score_momentum(float(mom), reasons), "mom_12_1", float(mom), factor_stats,
        )
        weighted_sum += mom_score * _WEIGHTS["momentum"]
        available_weight += _WEIGHTS["momentum"]
    else:
        reasons.append("12-1 momentum unavailable (needs ~13 months of history) — not scored")

    if rsi is not None:
        weighted_sum += _score_rsi(float(rsi), reasons) * _WEIGHTS["rsi"]
        available_weight += _WEIGHTS["rsi"]
    else:
        reasons.append("RSI unavailable — not scored")

    if macd_line is not None and macd_signal is not None:
        macd_dir_str = str(macd_dir) if macd_dir is not None else None
        weighted_sum += _score_macd(
            float(macd_line), float(macd_signal), macd_dir_str, reasons,
        ) * _WEIGHTS["macd"]
        available_weight += _WEIGHTS["macd"]
    else:
        reasons.append("MACD unavailable — not scored")

    if bb_pct is not None:
        weighted_sum += _score_bb(float(bb_pct), reasons) * _WEIGHTS["bb"]
        available_weight += _WEIGHTS["bb"]
    else:
        reasons.append("Bollinger Bands unavailable — not scored")

    if sma50 is not None and sma200 is not None and float(sma200) > 0:
        weighted_sum += _score_sma(float(sma50), float(sma200), reasons) * _WEIGHTS["sma"]
        available_weight += _WEIGHTS["sma"]
    else:
        reasons.append("SMA 50/200 unavailable (insufficient history) — not scored")

    if adx is not None:
        weighted_sum += _score_adx(float(adx), bullish, reasons) * _WEIGHTS["adx"]
        available_weight += _WEIGHTS["adx"]
    else:
        reasons.append("ADX unavailable — not scored")

    if vol_ratio is not None:
        weighted_sum += _score_volume(float(vol_ratio), bullish, reasons) * _WEIGHTS["volume"]
        available_weight += _WEIGHTS["volume"]
    else:
        reasons.append("Volume data unavailable — not scored")

    if available_weight < _MIN_AVAILABLE_WEIGHT:
        reasons.append("Too few indicators available — technical score is neutral")
        return ScoreResult(50.0, reasons, completeness=available_weight)

    score = weighted_sum / available_weight
    return ScoreResult(min(max(score, 0.0), 100.0), reasons, completeness=available_weight)
