"""
REFINED STRATEGY ENGINE - High Probability, Low Frequency
Focus: Fewer, higher-quality entries with better risk/reward

Core Logic:
  1. Trend confirmation (EMA stack + strength check)
  2. Pullback into support/resistance (better risk/reward)
  3. Momentum confirmation (volume, RSI)
  4. Dynamic SL/TP scaled to ATR (not fixed %)
  5. Time filters (avoid noisy periods)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Literal
import pandas as pd
import numpy as np
from config import cfg


class SignalType(Enum):
    CALL = "CALL"
    PUT = "PUT"
    NONE = "NONE"


@dataclass
class TradeSignal:
    signal: SignalType
    option_type: str  # "CE" or "PE"
    strike: float
    option_ask: float  # Real NSE LTP
    option_ltp: float
    entry_price: float
    sl_price: float
    tp_price: float
    confidence: float
    ema9_3m: float
    ema15_3m: float
    rsi_3m: float
    trend_15m: int  # +1 up, -1 down
    trend_1h: int
    reasons: List[str]


class StrategyMTF:
    """Multi-Timeframe Strategy: Entry on 3m, confirmation on 5m/15m/1h"""

    def __init__(self):
        self.last_signal_time = None
        self.cooldown_mins = 45  # Avoid whipsaw signals

    def generate_signal(
        self,
        df_1m: pd.DataFrame,
        df_3m: pd.DataFrame,
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
        underlying_ltp: float,
        vix: float,
        option_chain: pd.DataFrame,
        expiry: str,
    ) -> Optional[TradeSignal]:
        """
        Generate trade signal based on multi-timeframe analysis.
        Returns None if conditions not met.
        """

        # ─── MARKET QUALITY CHECKS ────────────────────────────────────────────
        if vix < cfg.vix_min or vix > cfg.vix_max:
            return None  # VIX too low (no movement) or too high (panic)

        if not self._is_trading_hours():
            return None

        # ─── TIME-BASED FILTER ────────────────────────────────────────────────
        # Skip first 15 mins (worst fills) and last 60 mins (time decay dominates)
        if self._is_noisy_period():
            return None

        # ─── TREND CONFIRMATION (15m as primary trend) ────────────────────────
        trend_15m = self._get_trend(df_15m)
        trend_1h = self._get_trend(df_1h)

        if trend_15m == 0:  # Flat on 15m, not worth trading
            return None

        # 1h must agree or be neutral (not opposite)
        if trend_1h != 0 and trend_1h != trend_15m:
            return None  # Conflicting signals

        # ─── ENTRY SIGNAL (3m EMA crossover + pullback) ────────────────────────
        entry_signal, entry_price = self._pullback_entry(df_3m, trend_15m)

        if entry_signal is None:
            return None

        # ─── MOMENTUM CONFIRMATION (5m must align) ─────────────────────────────
        trend_5m = self._get_trend(df_5m)
        if trend_5m != trend_15m:
            return None  # Entry disagrees with 5m trend

        # ─── VOLATILITY CHECK (use ATR for SL/TP sizing) ─────────────────────
        atr_3m = self._get_atr(df_3m)
        atr_pct = atr_3m / underlying_ltp

        if atr_pct < cfg.atr_min_pct:
            return None  # Not enough movement today

        # ─── COOLDOWN CHECK ────────────────────────────────────────────────────
        if self.last_signal_time is not None:
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            ist = ZoneInfo("Asia/Kolkata")
            mins_since = (datetime.now(ist) - self.last_signal_time).total_seconds() / 60
            if mins_since < self.cooldown_mins:
                return None  # Still in cooldown

        # ─── STRIKE & OPTION SELECTION ────────────────────────────────────────
        strike = self._select_strike(underlying_ltp)
        option_type = "CE" if trend_15m > 0 else "PE"

        # Find option in chain
        opt_row = self._find_option(option_chain, strike, option_type)
        if opt_row is None or opt_row["ask"] < cfg.option_min_premium:
            return None

        # ─── DYNAMIC SL/TP (STRUCTURE BASED + ATR SCALED) ────────────────────
        sl_price, tp_price = self._calc_sl_tp_dynamic(
            entry=entry_price,
            direction=trend_15m,
            atr=atr_3m,
            df=df_15m,
        )

        # ─── RISK/REWARD CHECK ────────────────────────────────────────────────
        risk = abs(entry_price - sl_price)
        reward = abs(tp_price - entry_price)

        if risk <= 0 or reward / risk < 1.5:
            return None  # Poor risk/reward

        # ─── FINAL CONFIDENCE SCORE ───────────────────────────────────────────
        confidence = self._calc_confidence(df_3m, df_5m, df_15m, trend_15m, atr_pct)

        self.last_signal_time = datetime.now(ZoneInfo("Asia/Kolkata"))

        ema9_3m = float(df_3m["ema_fast"].iloc[-1])
        ema15_3m = float(df_3m["ema_slow"].iloc[-1])
        rsi_3m = float(df_3m["rsi"].iloc[-1])

        return TradeSignal(
            signal=SignalType.CALL if trend_15m > 0 else SignalType.PUT,
            option_type=option_type,
            strike=strike,
            option_ask=float(opt_row["ask"]),
            option_ltp=float(opt_row["ltp"]),
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            confidence=confidence,
            ema9_3m=ema9_3m,
            ema15_3m=ema15_3m,
            rsi_3m=rsi_3m,
            trend_15m=trend_15m,
            trend_1h=trend_1h,
            reasons=[
                f"15m trend: {'BULL' if trend_15m > 0 else 'BEAR'}",
                f"Pullback entry at {entry_price:.1f}",
                f"RR: 1:{reward/risk:.1f}",
                f"VIX: {15:.1f}",
            ],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ─────────────────────────────────────────────────────────────────────────

    def _get_trend(self, df: pd.DataFrame) -> int:
        """
        Returns:
          +1 = bullish (EMA9 > EMA21 > EMA50)
          -1 = bearish (EMA9 < EMA21 < EMA50)
           0 = flat/conflicted
        """
        if len(df) < 50:
            return 0

        e9 = float(df["ema_fast"].iloc[-1])
        e21 = float(df["ema_slow"].iloc[-1])
        e50 = float(df["ema_trend"].iloc[-1])

        if e9 > e21 > e50:
            return 1
        elif e9 < e21 < e50:
            return -1
        else:
            return 0

    def _pullback_entry(self, df: pd.DataFrame, trend: int) -> tuple:
        """
        Enhanced entry logic:
          1. Classic pullback (price dips to EMA21, reverses above EMA9)
          2. Consolidation in trend zone (between EMA9-EMA21 with volume)
          3. Trend continuation (strong momentum above/below EMA9)

        Returns (signal_type, entry_price) or (None, None)
        """
        if len(df) < 21:
            return None, None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        e9 = float(last["ema_fast"])
        e21 = float(last["ema_slow"])
        close = float(last["close"])
        prev_close = float(prev["close"])
        vol_avg = float(df["volume"].iloc[-20:].mean())

        if trend > 0:  # BULLISH
            # Entry 1: Classic pullback
            if prev_close <= e21 and close > e9:
                return SignalType.CALL, close
            # Entry 2: Consolidation with volume
            if e9 < close < e21 and float(last["volume"]) > vol_avg * 0.8:
                return SignalType.CALL, close
            # Entry 3: Strong continuation (higher highs)
            if close > e9 > e21 and float(last["high"]) > float(prev["high"]):
                return SignalType.CALL, close

        else:  # BEARISH
            # Entry 1: Classic pullback
            if prev_close >= e21 and close < e9:
                return SignalType.PUT, close
            # Entry 2: Consolidation with volume
            if e9 > close > e21 and float(last["volume"]) > vol_avg * 0.8:
                return SignalType.PUT, close
            # Entry 3: Strong continuation (lower lows)
            if close < e9 < e21 and float(last["low"]) < float(prev["low"]):
                return SignalType.PUT, close

        return None, None

    def _calc_sl_tp_dynamic(
        self,
        entry: float,
        direction: int,
        atr: float,
        df: pd.DataFrame,
    ) -> tuple:
        """
        Dynamic SL/TP based on structure and ATR.

        SL: Recent swing low/high (structure) + 0.5 ATR buffer
        TP: 1.5x to 2x the risk, or resistance level
        """
        if direction > 0:  # BULL
            # SL below recent swing low
            recent_low = df["low"].iloc[-10:].min()
            sl = recent_low - atr * 0.5
            sl = max(sl, entry - atr * 1.5)  # At least 1.5 ATR below entry

            # TP at 1.8x risk-reward
            risk = entry - sl
            tp = entry + risk * 1.8

        else:  # BEAR
            # SL above recent swing high
            recent_high = df["high"].iloc[-10:].max()
            sl = recent_high + atr * 0.5
            sl = min(sl, entry + atr * 1.5)  # At least 1.5 ATR above entry

            # TP at 1.8x risk-reward
            risk = sl - entry
            tp = entry - risk * 1.8

        return round(sl, 1), round(tp, 1)

    def _get_atr(self, df: pd.DataFrame) -> float:
        """Calculate ATR from dataframe."""
        if "atr" in df.columns:
            return float(df["atr"].iloc[-1])

        # Manual ATR calc if not present
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.ewm(span=14, adjust=False).mean()
        return float(atr.iloc[-1])

    def _is_trading_hours(self) -> bool:
        """Avoid pre/post market."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        h, m = now.hour, now.minute
        hm = h * 60 + m

        market_open = 9 * 60 + 20  # 09:20
        market_close = 15 * 60 + 15  # 15:15

        return market_open <= hm <= market_close

    def _is_noisy_period(self) -> bool:
        """Skip first 15 mins and last 60 mins of session."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        h, m = now.hour, now.minute
        hm = h * 60 + m

        market_open = 9 * 60 + 20
        market_close = 15 * 60 + 15

        # First 15 mins (9:20-9:35)
        if hm < market_open + 15:
            return True

        # Last 60 mins (14:15 onwards)
        if hm > market_close - 60:
            return True

        return False

    def _select_strike(self, underlying_ltp: float) -> float:
        """Select ATM strike."""
        gap = cfg.strike_gap
        return round(underlying_ltp / gap) * gap

    def _find_option(
        self, chain: pd.DataFrame, strike: float, option_type: str
    ) -> Optional[dict]:
        """Find option in chain with best bid/ask."""
        if chain is None or chain.empty:
            return None

        filtered = chain[
            (chain["strike"] == strike) & (chain["option_type"] == option_type)
        ]

        if filtered.empty:
            return None

        row = filtered.iloc[0]
        return {
            "ltp": float(row.get("ltp", 0)),
            "ask": float(row.get("ask", 0)),
            "bid": float(row.get("bid", 0)),
        }

    def _calc_confidence(
        self,
        df_3m: pd.DataFrame,
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame,
        direction: int,
        atr_pct: float,
    ) -> float:
        """Calculate confidence score (0-100)."""
        score = 50  # Base score

        # RSI extremes = less confident
        rsi_3m = float(df_3m["rsi"].iloc[-1])
        if rsi_3m > 70 or rsi_3m < 30:
            score -= 15

        # Trend strength on multiple timeframes
        ema_sep_5m = abs(float(df_5m["ema_fast"].iloc[-1]) - float(df_5m["ema_slow"].iloc[-1]))
        ema_sep_15m = abs(float(df_15m["ema_fast"].iloc[-1]) - float(df_15m["ema_slow"].iloc[-1]))

        if ema_sep_15m > ema_sep_5m * 1.2:
            score += 10  # 15m separation strong

        # Volume boost
        vol_avg = df_3m["volume"].iloc[-20:].mean()
        if float(df_3m["volume"].iloc[-1]) > vol_avg * 1.5:
            score += 10

        # ATR boost for high volatility (within healthy range)
        if atr_pct > 0.01:
            score += 10

        return min(100, max(0, score))


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY SINGLE-TIMEFRAME STRATEGY (for backcompat)
# ─────────────────────────────────────────────────────────────────────────────


class ProStrategyEngine:
    """Simple trend + structure strategy for reference."""

    def __init__(self):
        pass

    def generate_signal(self, df: pd.DataFrame) -> Optional[TradeSignal]:
        """Simple trend confirmation on single timeframe."""
        if len(df) < 50:
            return None

        last = df.iloc[-1]

        # Trend
        if last["ema_fast"] > last["ema_slow"] > last["ema_trend"]:
            signal_type = SignalType.CALL
        elif last["ema_fast"] < last["ema_slow"] < last["ema_trend"]:
            signal_type = SignalType.PUT
        else:
            return None

        # Simple structure
        recent_high = df["high"].iloc[-10:].max()
        recent_low = df["low"].iloc[-10:].min()

        entry = last["close"]
        if signal_type == SignalType.CALL:
            sl = recent_low
            tp = entry + (entry - sl) * 2
        else:
            sl = recent_high
            tp = entry - (sl - entry) * 2

        return TradeSignal(
            signal=signal_type,
            option_type="CE" if signal_type == SignalType.CALL else "PE",
            strike=round(entry / 50) * 50,
            option_ask=entry,
            option_ltp=entry,
            entry_price=entry,
            sl_price=sl,
            tp_price=tp,
            confidence=70,
            ema9_3m=float(last["ema_fast"]),
            ema15_3m=float(last["ema_slow"]),
            rsi_3m=0,
            trend_15m=1 if signal_type == SignalType.CALL else -1,
            trend_1h=0,
            reasons=["Trend confirmation"],
        )
