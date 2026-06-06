"""
smc_analysis.py — Smart Money Concepts (SMC) implementation.

Detects:
  1. Order Blocks (OB)     — Institutional accumulation/distribution zones
  2. Break of Structure (BOS) — Confirms trend direction
  3. Change of Character (CHOCH) — Potential reversal signal
  4. Fair Value Gaps (FVG) — Price imbalances; market tends to revisit

All logic is price-action based — no indicators, no repainting.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from enum import Enum

from config import cfg


class Direction(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class OrderBlock:
    index: int              # DataFrame row index
    timestamp: pd.Timestamp
    direction: Direction    # BULLISH OB = support, BEARISH OB = resistance
    high: float
    low: float
    mid: float
    is_tested: bool = False
    is_broken: bool = False

    @property
    def zone(self) -> Tuple[float, float]:
        return (self.low, self.high)

    def price_inside(self, price: float) -> bool:
        return self.low <= price <= self.high


@dataclass
class FVG:
    timestamp: pd.Timestamp
    direction: Direction    # BULLISH FVG = gap above, price likely to fill
    upper: float
    lower: float
    gap_pct: float
    filled: bool = False

    def price_inside(self, price: float) -> bool:
        return self.lower <= price <= self.upper


@dataclass
class StructurePoint:
    timestamp: pd.Timestamp
    price: float
    point_type: str     # "HH" | "HL" | "LH" | "LL"


@dataclass
class SMCContext:
    """Aggregated SMC analysis result for a given candle series."""
    trend: Direction = Direction.NEUTRAL
    last_bos: Optional[Direction] = None
    choch_detected: bool = False
    choch_direction: Optional[Direction] = None
    bullish_obs: List[OrderBlock] = field(default_factory=list)
    bearish_obs: List[OrderBlock] = field(default_factory=list)
    bullish_fvgs: List[FVG] = field(default_factory=list)
    bearish_fvgs: List[FVG] = field(default_factory=list)
    nearest_support_ob: Optional[OrderBlock] = None
    nearest_resist_ob: Optional[OrderBlock] = None
    active_bullish_fvg: Optional[FVG] = None
    active_bearish_fvg: Optional[FVG] = None
    structure_points: List[StructurePoint] = field(default_factory=list)


class SMCAnalyzer:
    """
    Stateless SMC analysis. Call analyze() on every scan with the latest candles.
    """

    def __init__(self, lookback: int = None, fvg_min_pct: float = None):
        self.lookback = lookback or cfg.ob_lookback
        self.fvg_min_pct = fvg_min_pct or cfg.fvg_min_size_pct

    # ─── Main Entry Point ─────────────────────────────────────────────────────

    def analyze(self, df: pd.DataFrame) -> SMCContext:
        """
        Full SMC scan on the provided OHLCV DataFrame.
        df must have at least 50 candles and columns: open, high, low, close
        """
        ctx = SMCContext()

        if len(df) < 30:
            return ctx

        swings = self._detect_swings(df)
        ctx.structure_points = self._classify_structure(swings)
        ctx.trend = self._determine_trend(ctx.structure_points)
        ctx.last_bos, ctx.choch_detected, ctx.choch_direction = self._detect_bos_choch(
            df, ctx.structure_points
        )

        ctx.bullish_obs = self._detect_order_blocks(df, Direction.BULLISH)
        ctx.bearish_obs = self._detect_order_blocks(df, Direction.BEARISH)

        # Mark tested/broken OBs against current price
        current_price = df["close"].iloc[-1]
        ctx.nearest_support_ob = self._nearest_ob(ctx.bullish_obs, current_price, Direction.BULLISH)
        ctx.nearest_resist_ob  = self._nearest_ob(ctx.bearish_obs, current_price, Direction.BEARISH)

        ctx.bullish_fvgs = self._detect_fvgs(df, Direction.BULLISH)
        ctx.bearish_fvgs = self._detect_fvgs(df, Direction.BEARISH)
        ctx.active_bullish_fvg = self._nearest_fvg(ctx.bullish_fvgs, current_price, Direction.BULLISH)
        ctx.active_bearish_fvg = self._nearest_fvg(ctx.bearish_fvgs, current_price, Direction.BEARISH)

        return ctx

    # ─── Swing Detection ──────────────────────────────────────────────────────

    def _detect_swings(self, df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
        """Identify pivot highs and lows using a rolling window."""
        n = window
        highs = df["high"]
        lows  = df["low"]

        # A swing high is the highest in a 2n+1 window centred on that bar
        rolling_high_max = highs.rolling(2 * n + 1, center=True).max()
        rolling_low_min  = lows.rolling(2 * n + 1, center=True).min()

        swings = df.copy()
        swings["is_swing_high"] = highs == rolling_high_max
        swings["is_swing_low"]  = lows  == rolling_low_min
        return swings

    # ─── Market Structure Classification ─────────────────────────────────────

    def _classify_structure(self, swings: pd.DataFrame) -> List[StructurePoint]:
        """
        Classify sequence of swing highs/lows into HH, HL, LH, LL.
        HH+HL sequence = uptrend; LH+LL = downtrend.
        """
        points: List[StructurePoint] = []

        swing_highs = swings[swings["is_swing_high"]][["high"]].copy()
        swing_lows  = swings[swings["is_swing_low"]][["low"]].copy()

        # Interleave and classify
        prev_high: Optional[float] = None
        prev_low:  Optional[float] = None

        for ts, row in swings.iterrows():
            if row["is_swing_high"]:
                pt_type = "HH" if prev_high is None or row["high"] > prev_high else "LH"
                points.append(StructurePoint(ts, row["high"], pt_type))
                prev_high = row["high"]
            if row["is_swing_low"]:
                pt_type = "HL" if prev_low is None or row["low"] > prev_low else "LL"
                points.append(StructurePoint(ts, row["low"], pt_type))
                prev_low = row["low"]

        return points[-20:]  # Keep only recent structure

    def _determine_trend(self, structure: List[StructurePoint]) -> Direction:
        """
        Trend = HH + HL (bullish) | LH + LL (bearish)
        Check last 4 significant structure points.
        """
        if len(structure) < 4:
            return Direction.NEUTRAL

        recent = structure[-6:]
        highs = [p for p in recent if p.point_type in ("HH", "LH")]
        lows  = [p for p in recent if p.point_type in ("HL", "LL")]

        bull_score = sum(1 for p in highs if p.point_type == "HH") + \
                     sum(1 for p in lows  if p.point_type == "HL")
        bear_score = sum(1 for p in highs if p.point_type == "LH") + \
                     sum(1 for p in lows  if p.point_type == "LL")

        if bull_score > bear_score + 1:
            return Direction.BULLISH
        if bear_score > bull_score + 1:
            return Direction.BEARISH
        return Direction.NEUTRAL

    # ─── BOS / CHOCH ─────────────────────────────────────────────────────────

    def _detect_bos_choch(
        self,
        df: pd.DataFrame,
        structure: List[StructurePoint],
    ) -> Tuple[Optional[Direction], bool, Optional[Direction]]:
        """
        BOS  = price breaks a previous swing high/low in the direction of trend.
        CHOCH = price breaks structure AGAINST the prevailing trend (early reversal).
        Returns (last_bos_direction, choch_detected, choch_direction)
        """
        if len(structure) < 2:
            return None, False, None

        last_close = df["close"].iloc[-1]
        last_bos: Optional[Direction] = None
        choch_detected = False
        choch_dir: Optional[Direction] = None

        recent_highs = [p for p in structure if p.point_type in ("HH", "LH")]
        recent_lows  = [p for p in structure if p.point_type in ("HL", "LL")]

        if recent_highs:
            last_sh = recent_highs[-1]
            # BOS bullish: close breaks above last swing high
            if last_close > last_sh.price:
                last_bos = Direction.BULLISH
                # CHOCH: last structure was bearish but price broke bull
                if last_sh.point_type == "LH":
                    choch_detected = True
                    choch_dir = Direction.BULLISH

        if recent_lows:
            last_sl = recent_lows[-1]
            # BOS bearish: close breaks below last swing low
            if last_close < last_sl.price:
                last_bos = Direction.BEARISH
                if last_sl.point_type == "HL":
                    choch_detected = True
                    choch_dir = Direction.BEARISH

        return last_bos, choch_detected, choch_dir

    # ─── Order Blocks ─────────────────────────────────────────────────────────

    def _detect_order_blocks(
        self, df: pd.DataFrame, direction: Direction
    ) -> List[OrderBlock]:
        """
        Bullish OB  = Last bearish candle (close < open) before a strong bullish move.
        Bearish OB  = Last bullish candle (close > open) before a strong bearish move.
        """
        obs: List[OrderBlock] = []
        lookback = min(self.lookback, len(df) - 3)

        for i in range(lookback, len(df) - 2):
            candle     = df.iloc[i]
            next1      = df.iloc[i + 1]
            next2      = df.iloc[i + 2]

            body_size   = abs(candle["close"] - candle["open"])
            next_range  = abs(next2["close"] - next1["open"])

            if direction == Direction.BULLISH:
                # Last bearish candle before 2-bar bullish expansion
                is_bearish_candle  = candle["close"] < candle["open"]
                is_bullish_move    = next1["close"] > next1["open"] and next2["close"] > next2["open"]
                impulse_strong     = next_range > body_size * 1.5

                if is_bearish_candle and is_bullish_move and impulse_strong:
                    ob = OrderBlock(
                        index=i,
                        timestamp=df.index[i],
                        direction=Direction.BULLISH,
                        high=candle["high"],
                        low=candle["low"],
                        mid=(candle["high"] + candle["low"]) / 2,
                    )
                    # Check if still valid (not broken by price going below)
                    subsequent_low = df["low"].iloc[i + 1:].min()
                    if subsequent_low > candle["low"] * 0.998:  # 0.2% tolerance
                        obs.append(ob)

            elif direction == Direction.BEARISH:
                is_bullish_candle  = candle["close"] > candle["open"]
                is_bearish_move    = next1["close"] < next1["open"] and next2["close"] < next2["open"]
                impulse_strong     = next_range > body_size * 1.5

                if is_bullish_candle and is_bearish_move and impulse_strong:
                    ob = OrderBlock(
                        index=i,
                        timestamp=df.index[i],
                        direction=Direction.BEARISH,
                        high=candle["high"],
                        low=candle["low"],
                        mid=(candle["high"] + candle["low"]) / 2,
                    )
                    subsequent_high = df["high"].iloc[i + 1:].max()
                    if subsequent_high < candle["high"] * 1.002:
                        obs.append(ob)

        return obs[-5:]  # Keep only last 5 valid OBs

    def _nearest_ob(
        self,
        obs: List[OrderBlock],
        price: float,
        direction: Direction,
    ) -> Optional[OrderBlock]:
        """Find the most relevant OB near current price."""
        if not obs:
            return None
        if direction == Direction.BULLISH:
            # Support: find highest OB below current price
            below = [ob for ob in obs if ob.high < price * 1.005]
            return max(below, key=lambda ob: ob.high) if below else None
        else:
            # Resistance: find lowest OB above current price
            above = [ob for ob in obs if ob.low > price * 0.995]
            return min(above, key=lambda ob: ob.low) if above else None

    # ─── Fair Value Gaps ──────────────────────────────────────────────────────

    def _detect_fvgs(self, df: pd.DataFrame, direction: Direction) -> List[FVG]:
        """
        Bullish FVG : candle[i-1].high < candle[i+1].low  → gap above price
        Bearish FVG : candle[i-1].low  > candle[i+1].high → gap below price
        """
        fvgs: List[FVG] = []
        lookback = min(self.lookback, len(df) - 3)

        for i in range(1, lookback):
            # Use negative indexing to scan recent candles
            idx = len(df) - lookback + i
            if idx < 2 or idx >= len(df) - 1:
                continue

            prev = df.iloc[idx - 1]
            curr = df.iloc[idx]       # noqa: F841 (middle candle — impulse)
            nxt  = df.iloc[idx + 1]

            if direction == Direction.BULLISH:
                if prev["high"] < nxt["low"]:
                    gap = nxt["low"] - prev["high"]
                    mid = (nxt["low"] + prev["high"]) / 2
                    gap_pct = gap / mid
                    if gap_pct >= self.fvg_min_pct:
                        fvgs.append(FVG(
                            timestamp=df.index[idx],
                            direction=Direction.BULLISH,
                            upper=nxt["low"],
                            lower=prev["high"],
                            gap_pct=gap_pct,
                        ))

            elif direction == Direction.BEARISH:
                if prev["low"] > nxt["high"]:
                    gap = prev["low"] - nxt["high"]
                    mid = (prev["low"] + nxt["high"]) / 2
                    gap_pct = gap / mid
                    if gap_pct >= self.fvg_min_pct:
                        fvgs.append(FVG(
                            timestamp=df.index[idx],
                            direction=Direction.BEARISH,
                            upper=prev["low"],
                            lower=nxt["high"],
                            gap_pct=gap_pct,
                        ))

        return fvgs[-3:]  # Keep 3 most recent FVGs

    def _nearest_fvg(
        self,
        fvgs: List[FVG],
        price: float,
        direction: Direction,
    ) -> Optional[FVG]:
        if not fvgs:
            return None
        unfilled = [f for f in fvgs if not f.filled]
        if direction == Direction.BULLISH:
            below = [f for f in unfilled if f.upper < price * 1.003]
            return max(below, key=lambda f: f.upper) if below else None
        else:
            above = [f for f in unfilled if f.lower > price * 0.997]
            return min(above, key=lambda f: f.lower) if above else None
