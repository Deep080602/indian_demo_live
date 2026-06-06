"""
indicators.py — Pure vectorised indicator calculations (pandas/numpy only).

All functions:
  - Accept pd.DataFrame with [open, high, low, close, volume] columns
  - Return a new column Series or modified DataFrame
  - Are stateless — safe to call on every scan loop iteration
"""

import numpy as np
import pandas as pd
from typing import Tuple

from config import cfg


# ─── Exponential Moving Average ───────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def add_emas(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA-9, EMA-21, EMA-50 to a OHLCV DataFrame."""
    df = df.copy()
    df["ema_fast"] = ema(df["close"], cfg.ema_fast)
    df["ema_slow"] = ema(df["close"], cfg.ema_slow)
    df["ema_trend"] = ema(df["close"], cfg.ema_trend)
    return df


# ─── RSI ──────────────────────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ─── ATR ──────────────────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


# ─── VWAP ─────────────────────────────────────────────────────────────────────

def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Session VWAP — resets at market open each day.
    Requires datetime index with timezone info.
    """
    df = df.copy()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    df["_date"] = df.index.normalize()
    df["_tp_vol"] = typical * df["volume"]
    df["_cum_tp_vol"] = df.groupby("_date")["_tp_vol"].cumsum()
    df["_cum_vol"]    = df.groupby("_date")["volume"].cumsum()
    vwap_series = df["_cum_tp_vol"] / df["_cum_vol"].replace(0, np.nan)
    return vwap_series.rename("vwap")


# ─── Bollinger Bands ──────────────────────────────────────────────────────────

def bollinger_bands(
    series: pd.Series, period: int = 20, std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower)."""
    mid = series.rolling(period).mean()
    sd  = series.rolling(period).std(ddof=0)
    return mid + std * sd, mid, mid - std * sd


# ─── Supertrend ───────────────────────────────────────────────────────────────

def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Returns DataFrame with columns: [supertrend, trend_direction]
    trend_direction: +1 = bullish, -1 = bearish
    """
    df = df.copy()
    hl2 = (df["high"] + df["low"]) / 2
    atr_val = atr(df, period)

    basic_upper = hl2 + multiplier * atr_val
    basic_lower = hl2 - multiplier * atr_val

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()

    for i in range(1, len(df)):
        fu_prev = final_upper.iloc[i - 1]
        fl_prev = final_lower.iloc[i - 1]
        c_prev  = df["close"].iloc[i - 1]

        final_upper.iloc[i] = (
            basic_upper.iloc[i]
            if basic_upper.iloc[i] < fu_prev or c_prev > fu_prev
            else fu_prev
        )
        final_lower.iloc[i] = (
            basic_lower.iloc[i]
            if basic_lower.iloc[i] > fl_prev or c_prev < fl_prev
            else fl_prev
        )

    supertrend_vals = pd.Series(index=df.index, dtype=float)
    direction       = pd.Series(index=df.index, dtype=int)

    for i in range(1, len(df)):
        c = df["close"].iloc[i]
        st_prev = supertrend_vals.iloc[i - 1]
        if pd.isna(st_prev):
            supertrend_vals.iloc[i] = final_upper.iloc[i]
            direction.iloc[i] = -1
        elif st_prev == final_upper.iloc[i - 1]:
            if c <= final_upper.iloc[i]:
                supertrend_vals.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1
            else:
                supertrend_vals.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = +1
        else:
            if c >= final_lower.iloc[i]:
                supertrend_vals.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = +1
            else:
                supertrend_vals.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1

    df["supertrend"]       = supertrend_vals
    df["trend_direction"]  = direction
    return df


# ─── MACD ─────────────────────────────────────────────────────────────────────

def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    fast_ema   = series.ewm(span=fast,   adjust=False).mean()
    slow_ema   = series.ewm(span=slow,   adjust=False).mean()
    macd_line  = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


# ─── Swing Highs / Lows ───────────────────────────────────────────────────────

def swing_highs_lows(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """
    Identifies swing highs and lows.
    Returns df with columns: [swing_high, swing_low] (bool).
    """
    df = df.copy()
    highs = df["high"]
    lows  = df["low"]

    df["swing_high"] = (
        (highs == highs.rolling(2 * lookback + 1, center=True).max())
    )
    df["swing_low"] = (
        (lows == lows.rolling(2 * lookback + 1, center=True).min())
    )
    return df


# ─── Volume Profile / Average Volume ─────────────────────────────────────────

def relative_volume(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Current volume relative to rolling mean. > 1.5 = high volume."""
    avg_vol = df["volume"].rolling(period).mean()
    return df["volume"] / avg_vol.replace(0, np.nan)


# ─── Convenience: Compute All Indicators ─────────────────────────────────────

def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run all indicators on a clean OHLCV DataFrame.
    Returns enriched DataFrame ready for strategy consumption.
    """
    if len(df) < 60:
        return df  # Not enough data

    df = add_emas(df)
    df["rsi"]   = rsi(df["close"], cfg.rsi_period)
    df["atr"]   = atr(df, cfg.atr_period)
    df["vwap"]  = vwap(df)
    df["rvol"]  = relative_volume(df)

    bb_upper, bb_mid, bb_lower = bollinger_bands(df["close"], cfg.bb_period, cfg.bb_std)
    df["bb_upper"] = bb_upper
    df["bb_mid"]   = bb_mid
    df["bb_lower"] = bb_lower

    macd_l, macd_s, macd_h = macd(df["close"])
    df["macd"]        = macd_l
    df["macd_signal"] = macd_s
    df["macd_hist"]   = macd_h

    df = supertrend(df, period=10, multiplier=3.0)
    df = swing_highs_lows(df, lookback=cfg.swing_lookback)

    df["atr_pct"] = df["atr"] / df["close"]  # ATR as % of price

    return df
