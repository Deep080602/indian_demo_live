"""
index_selector.py — Auto-selects the index with the strongest momentum.

Checks NIFTY, BANKNIFTY, FINNIFTY, SENSEX every 30 minutes.
Picks whichever has:
  - Highest ADX (trending most strongly)
  - EMA9 > EMA21 (direction confirmed)
  - Price above/below VWAP (institutional bias)

Returns the selected index name + direction (CALL/PUT).
"""

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Dict
import pandas as pd
import numpy as np

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

CANDIDATES = ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY"]

TICKER_MAP = {
    "NIFTY":     "^NSEI",
    "SENSEX":    "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY":  "NIFTY_FIN_SERVICE.NS",
}


def _fetch_15m(ticker: str) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        raw = yf.download(ticker, period="5d", interval="15m",
                          progress=False, auto_adjust=True,
                          multi_level_index=False)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0].lower() for c in raw.columns]
        else:
            raw.columns = [str(c).lower().strip() for c in raw.columns]
        if "close" not in raw.columns:
            return None
        for col in ["open","high","low","volume"]:
            if col not in raw.columns:
                raw[col] = raw["close"]
        if raw.index.tz is None:
            raw.index = raw.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
        else:
            raw.index = raw.index.tz_convert("Asia/Kolkata")
        raw = raw.between_time("09:15","15:30").dropna(subset=["close"])
        return raw if not raw.empty else None
    except Exception as e:
        log.debug(f"[SEL] fetch {ticker}: {e}")
        return None


def _score(df: pd.DataFrame) -> Tuple[float, str]:
    """Returns (momentum_score, direction)."""
    if df is None or len(df) < 20:
        return 0.0, "NEUTRAL"

    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    # EMA
    e9  = close.ewm(span=9,  adjust=False).mean()
    e21 = close.ewm(span=21, adjust=False).mean()
    e50 = close.ewm(span=50, adjust=False).mean()

    # ATR
    pc = close.shift(1)
    tr = pd.concat([high-low,(high-pc).abs(),(low-pc).abs()],axis=1).max(axis=1)
    atr= tr.ewm(com=13, min_periods=14).mean()

    # ADX
    pdm = (high-high.shift(1)).clip(lower=0)
    mdm = (low.shift(1)-low).clip(lower=0)
    pdm = pdm.where(pdm>mdm,0)
    mdm = mdm.where(mdm>pdm,0)
    pdi = 100*pdm.ewm(com=13,min_periods=14).mean()/atr.replace(0,1)
    mdi = 100*mdm.ewm(com=13,min_periods=14).mean()/atr.replace(0,1)
    dx  = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0,1)
    adx = dx.ewm(com=13,min_periods=14).mean()

    # VWAP
    tp  = (high+low+close)/3
    grp = df.index.normalize()
    vwap= (tp*df["volume"]).groupby(grp).cumsum()/df["volume"].groupby(grp).cumsum().replace(0,np.nan)

    last_e9  = float(e9.iloc[-1])
    last_e21 = float(e21.iloc[-1])
    last_e50 = float(e50.iloc[-1])
    last_c   = float(close.iloc[-1])
    last_adx = float(adx.iloc[-1])
    last_pdi = float(pdi.iloc[-1])
    last_mdi = float(mdi.iloc[-1])
    last_vwap= float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else last_c

    if last_adx < 15:
        return 0.0, "NEUTRAL"

    bull = (last_e9>last_e21) + (last_e21>last_e50) + (last_c>last_vwap) + (last_pdi>last_mdi)
    bear = (last_e9<last_e21) + (last_e21<last_e50) + (last_c<last_vwap) + (last_mdi>last_pdi)

    direction = "NEUTRAL"
    score     = 0.0

    if bull >= 3:
        direction = "BULL"
        score     = last_adx * (bull / 4)
    elif bear >= 3:
        direction = "BEAR"
        score     = last_adx * (bear / 4)

    return round(score, 1), direction


class IndexSelector:
    """
    Auto-selects the best index to trade based on momentum.
    Re-evaluates every 30 minutes.
    """

    def __init__(self, candidates=None):
        self.candidates  = candidates or CANDIDATES
        self._selected   = "NIFTY"
        self._direction  = "NEUTRAL"
        self._scores:    Dict[str, float] = {}
        self._last_check = None
        self._ttl        = 30 * 60  # recheck every 30 min

    def get_best(self) -> Tuple[str, str]:
        """
        Returns (index_name, direction) for the strongest trending index.
        direction = 'BULL' or 'BEAR' or 'NEUTRAL'
        """
        now = datetime.now(IST)
        if (self._last_check is None or
                (now - self._last_check).total_seconds() > self._ttl):
            self._evaluate()
            self._last_check = now

        return self._selected, self._direction

    def _evaluate(self):
        log.info("[SEL] Evaluating all indices for momentum...")
        best_score = 0.0
        best_index = "NIFTY"
        best_dir   = "NEUTRAL"

        for idx in self.candidates:
            ticker = TICKER_MAP.get(idx, "^NSEI")
            df     = _fetch_15m(ticker)
            score, direction = _score(df)
            self._scores[idx] = score
            log.info(f"[SEL]   {idx:12s}: ADX-score={score:.1f}  dir={direction}")

            if score > best_score and direction != "NEUTRAL":
                best_score = score
                best_index = idx
                best_dir   = direction

        self._selected  = best_index
        self._direction = best_dir
        log.info(f"[SEL] SELECTED: {best_index} (score={best_score:.1f}, {best_dir})")
