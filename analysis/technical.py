from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator, SMAIndicator
from ta.volatility import BollingerBands

_WEIGHTS = {
    "rsi": 0.20,
    "macd": 0.25,
    "bb": 0.10,
    "sma": 0.20,
    "adx": 0.10,
    "volume": 0.15,
}


def compute_indicators(df: pd.DataFrame) -> dict[str, float | str]:
    close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
    high = df["High"].squeeze() if isinstance(df["High"], pd.DataFrame) else df["High"]
    low = df["Low"].squeeze() if isinstance(df["Low"], pd.DataFrame) else df["Low"]
    volume = df["Volume"].squeeze() if isinstance(df["Volume"], pd.DataFrame) else df["Volume"]

    rsi_ind = RSIIndicator(close=close, window=14)
    rsi = rsi_ind.rsi().iloc[-1]

    macd_ind = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = macd_ind.macd().iloc[-1]
    macd_signal = macd_ind.macd_signal().iloc[-1]
    macd_hist = macd_ind.macd_diff().iloc[-1]
    macd_hist_prev = macd_ind.macd_diff().iloc[-2] if len(macd_ind.macd_diff()) > 1 else 0.0

    bb = BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_lower = bb.bollinger_lband().iloc[-1]
    bb_pct = bb.bollinger_pband().iloc[-1]

    sma50 = SMAIndicator(close=close, window=50).sma_indicator().iloc[-1]
    sma200 = SMAIndicator(close=close, window=200).sma_indicator().iloc[-1]
    current_price = float(close.iloc[-1])

    adx_ind = ADXIndicator(high=high, low=low, close=close, window=14)
    adx = adx_ind.adx().iloc[-1]

    vol_recent = float(volume.iloc[-5:].mean())
    vol_avg = float(volume.iloc[-20:].mean())
    volume_ratio = vol_recent / vol_avg if vol_avg > 0 else 1.0

    macd_direction = "bullish" if macd_hist > macd_hist_prev else "bearish"

    return {
        "rsi": float(rsi) if not np.isnan(rsi) else 50.0,
        "macd_line": float(macd_line) if not np.isnan(macd_line) else 0.0,
        "macd_signal": float(macd_signal) if not np.isnan(macd_signal) else 0.0,
        "macd_hist": float(macd_hist) if not np.isnan(macd_hist) else 0.0,
        "macd_direction": macd_direction,
        "bb_pct": float(bb_pct) if not np.isnan(bb_pct) else 0.5,
        "bb_upper": float(bb_upper) if not np.isnan(bb_upper) else 0.0,
        "bb_lower": float(bb_lower) if not np.isnan(bb_lower) else 0.0,
        "sma50": float(sma50) if not np.isnan(sma50) else 0.0,
        "sma200": float(sma200) if not np.isnan(sma200) else 0.0,
        "adx": float(adx) if not np.isnan(adx) else 0.0,
        "volume_ratio": volume_ratio,
        "current_price": current_price,
    }


def _is_bullish_trend(macd_line: float, macd_signal: float, sma50: float, sma200: float) -> bool:
    bullish_signals = 0
    if macd_line > macd_signal:
        bullish_signals += 1
    if sma50 > 0 and sma200 > 0 and sma50 > sma200:
        bullish_signals += 1
    return bullish_signals >= 1


def score_technical(indicators: dict[str, float | str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    rsi = float(indicators["rsi"])
    macd_line = float(indicators["macd_line"])
    macd_signal = float(indicators["macd_signal"])
    macd_dir = str(indicators["macd_direction"])
    bb_pct = float(indicators["bb_pct"])
    sma50 = float(indicators["sma50"])
    sma200 = float(indicators["sma200"])
    adx = float(indicators["adx"])
    vol_ratio = float(indicators["volume_ratio"])

    bullish = _is_bullish_trend(macd_line, macd_signal, sma50, sma200)

    if rsi < 30:
        rsi_score = 100.0
        reasons.append(f"RSI {rsi:.0f} — oversold (bullish)")
    elif rsi < 40:
        rsi_score = 75.0
        reasons.append(f"RSI {rsi:.0f} — approaching oversold")
    elif rsi < 60:
        rsi_score = 50.0
    elif rsi < 70:
        rsi_score = 25.0
        reasons.append(f"RSI {rsi:.0f} — approaching overbought")
    else:
        rsi_score = 0.0
        reasons.append(f"RSI {rsi:.0f} — overbought (bearish)")
    score += rsi_score * _WEIGHTS["rsi"]

    if macd_line > macd_signal and macd_dir == "bullish":
        macd_score = 100.0
        reasons.append("MACD bullish crossover with rising momentum")
    elif macd_line > macd_signal:
        macd_score = 70.0
        reasons.append("MACD above signal line")
    elif macd_line < macd_signal and macd_dir == "bearish":
        macd_score = 0.0
        reasons.append("MACD bearish crossover with falling momentum")
    elif macd_line < macd_signal:
        macd_score = 30.0
        reasons.append("MACD below signal line")
    else:
        macd_score = 50.0
    score += macd_score * _WEIGHTS["macd"]

    if bb_pct < 0.0:
        bb_score = 100.0
        reasons.append("Price below lower Bollinger Band (potential buy)")
    elif bb_pct < 0.2:
        bb_score = 80.0
        reasons.append("Price near lower Bollinger Band")
    elif bb_pct < 0.8:
        bb_score = 50.0
    elif bb_pct < 1.0:
        bb_score = 20.0
        reasons.append("Price near upper Bollinger Band")
    else:
        bb_score = 0.0
        reasons.append("Price above upper Bollinger Band (overbought)")
    score += bb_score * _WEIGHTS["bb"]

    if sma50 > 0 and sma200 > 0:
        sma_ratio = sma50 / sma200
        if sma_ratio > 1.02:
            sma_score = 100.0
            reasons.append("Golden cross — SMA 50 above SMA 200 (bullish)")
        elif sma_ratio > 1.0:
            sma_score = 70.0
            reasons.append("SMA 50 slightly above SMA 200")
        elif sma_ratio > 0.98:
            sma_score = 40.0
            reasons.append("SMA 50 near SMA 200 (neutral)")
        else:
            sma_score = 0.0
            reasons.append("Death cross — SMA 50 below SMA 200 (bearish)")
    else:
        sma_score = 50.0
    score += sma_score * _WEIGHTS["sma"]

    if adx > 25:
        if bullish:
            adx_score = 90.0
            reasons.append(f"ADX {adx:.0f} — strong bullish trend")
        else:
            adx_score = 20.0
            reasons.append(f"ADX {adx:.0f} — strong bearish trend")
    elif adx > 20:
        adx_score = 50.0
    else:
        adx_score = 40.0
        reasons.append(f"ADX {adx:.0f} — weak/no trend")
    score += adx_score * _WEIGHTS["adx"]

    if vol_ratio > 1.5:
        if bullish:
            vol_score = 90.0
            reasons.append(f"Volume {vol_ratio:.1f}x above average (confirms bullish move)")
        else:
            vol_score = 15.0
            reasons.append(f"Volume {vol_ratio:.1f}x above average (confirms bearish move)")
    elif vol_ratio > 1.0:
        vol_score = 60.0
    elif vol_ratio > 0.5:
        vol_score = 40.0
    else:
        vol_score = 20.0
        reasons.append(f"Volume {vol_ratio:.1f}x below average (low interest)")
    score += vol_score * _WEIGHTS["volume"]

    return min(max(score, 0.0), 100.0), reasons
