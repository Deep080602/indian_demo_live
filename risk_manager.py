"""
risk_manager.py — Institutional-grade risk management.

Responsibilities:
  - Position sizing (risk-based lot calculation)
  - Daily P&L tracking and drawdown halt
  - Max trades per day enforcement
  - Open position guard
  - Pre-trade and post-trade risk checks
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List

from config import cfg
try:
    from strategy_mtf import TradeSignal
except ImportError:
    from strategy_engine import TradeSignal

log = logging.getLogger(__name__)


@dataclass
class DailyRiskState:
    date: date = field(default_factory=date.today)
    trades_taken: int = 0
    realised_pnl: float = 0.0
    peak_capital: float = 0.0
    drawdown_halt: bool = False

    def reset_for_new_day(self, current_capital: float):
        self.date = date.today()
        self.trades_taken = 0
        self.realised_pnl = 0.0
        self.peak_capital = current_capital
        self.drawdown_halt = False


@dataclass
class PositionSizeResult:
    lots: int
    contracts: int       # lots × lot_size
    max_spend: float     # total premium outlay
    risk_amount: float   # max loss if SL hit
    target_amount: float # max gain if TP hit
    approved: bool
    reason: str = ""


class RiskManager:

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or cfg.capital
        self.current_capital = self.initial_capital
        self.daily = DailyRiskState(peak_capital=self.initial_capital)
        self.open_positions_count = 0
        self._ensure_fresh_day()

    # ─── Position Sizing ──────────────────────────────────────────────────────

    def size_position(self, signal: TradeSignal) -> PositionSizeResult:
        """
        IMPROVED: Risk-based position sizing using actual SL distance.

        risk_amount = capital × risk_pct
        actual_sl_distance = |entry - sl| in points
        max_loss_per_lot = sl_distance × premium_value_per_point × lot_size
        lots = floor(risk_amount / max_loss_per_lot)
        """
        entry_price = signal.entry_price
        sl_price = signal.sl_price
        tp_price = signal.tp_price
        premium = signal.option_ask
        lot_size = cfg.lot_size

        # Actual distance to SL in points (for option premium)
        sl_distance = abs(entry_price - sl_price)

        if sl_distance <= 0 or premium <= 0:
            return PositionSizeResult(0, 0, 0, 0, 0, False, "Invalid entry/SL/premium")

        # Max loss per contract = SL distance in premium points × lot size
        max_loss_per_lot = sl_distance * lot_size

        # Risk budget
        risk_amount = self.current_capital * cfg.risk_per_trade_pct

        # Calculate lots that fit within risk budget
        lots = int(risk_amount / max_loss_per_lot) if max_loss_per_lot > 0 else 0
        lots = max(1, min(lots, self._max_affordable_lots(premium)))

        contracts = lots * lot_size
        max_spend = premium * contracts
        actual_risk = sl_distance * contracts
        target_gain = abs(tp_price - entry_price) * contracts

        return PositionSizeResult(
            lots=lots,
            contracts=contracts,
            max_spend=max_spend,
            risk_amount=actual_risk,
            target_amount=target_gain,
            approved=True,
        )

    def _max_affordable_lots(self, premium: float) -> int:
        """Can't spend more than 20% of capital on a single trade."""
        max_spend = self.current_capital * 0.20
        max_lots  = int(max_spend / (premium * cfg.lot_size))
        return max(1, max_lots)

    # ─── Pre-Trade Checks ─────────────────────────────────────────────────────

    def pre_trade_check(self, signal: TradeSignal) -> tuple[bool, str]:
        """
        Returns (approved: bool, reason: str).
        Call before every paper trade entry.
        """
        self._ensure_fresh_day()

        # 1. Daily halt check
        if self.daily.drawdown_halt:
            return False, "DAILY_DRAWDOWN_HALT"

        # 2. Max trades per day
        if self.daily.trades_taken >= cfg.max_trades_per_day:
            return False, f"MAX_TRADES_REACHED ({cfg.max_trades_per_day}/day)"

        # 3. Max open positions
        if self.open_positions_count >= cfg.max_open_positions:
            return False, f"MAX_OPEN_POSITIONS ({cfg.max_open_positions})"

        # 4. Drawdown check (real-time unrealised included in calling code)
        if self._daily_drawdown_pct() >= cfg.daily_drawdown_limit_pct:
            self.daily.drawdown_halt = True
            return False, f"DAILY_DRAWDOWN_LIMIT ({cfg.daily_drawdown_limit_pct*100:.1f}%)"

        # 5. Premium sanity check
        premium = signal.option_ask
        if premium < cfg.option_min_premium or premium > cfg.option_max_premium:
            return False, f"PREMIUM_OUT_OF_RANGE ({premium:.1f})"

        # 6. Minimum capital available
        size = self.size_position(signal)
        if not size.approved or size.lots < 1:
            return False, "CANNOT_SIZE_POSITION"

        if size.max_spend > self.current_capital * 0.20:
            return False, "TRADE_TOO_LARGE_RELATIVE_TO_CAPITAL"

        return True, "OK"

    # ─── Post-Trade Updates ───────────────────────────────────────────────────

    def on_trade_opened(self, cost: float):
        """Call when a paper position is opened."""
        self.daily.trades_taken += 1
        self.open_positions_count += 1
        log.info(
            f"[RISK] Trade opened | Cost={cost:.0f} | "
            f"Daily trades={self.daily.trades_taken}/{cfg.max_trades_per_day}"
        )

    def on_trade_closed(self, pnl: float):
        """Call when a paper position is closed."""
        self.open_positions_count = max(0, self.open_positions_count - 1)
        self.daily.realised_pnl += pnl
        self.current_capital    += pnl

        log.info(
            f"[RISK] Trade closed | PnL={pnl:+.0f} | "
            f"DailyPnL={self.daily.realised_pnl:+.0f} | "
            f"Capital={self.current_capital:.0f}"
        )

        # Check drawdown after closing
        if self._daily_drawdown_pct() >= cfg.daily_drawdown_limit_pct:
            self.daily.drawdown_halt = True
            log.warning(
                f"[RISK] ⛔ Daily drawdown limit hit: {self._daily_drawdown_pct()*100:.2f}% — "
                "halting trading for today"
            )

    # ─── Dynamic SL / TP Adjustments ─────────────────────────────────────────

    def trailing_sl(
        self,
        entry_price: float,
        current_price: float,
        highest_price: float,
        trail_pct: float = 0.30,   # Trail at 30% below peak
    ) -> float:
        """
        Once premium is up 50%+ from entry, apply trailing stop.
        Trail = highest_seen × (1 - trail_pct)
        """
        gain_pct = (current_price - entry_price) / entry_price
        if gain_pct >= 0.50:
            trail_stop = highest_price * (1 - trail_pct)
            return max(trail_stop, entry_price * 1.10)  # Never trail below breakeven
        return 0.0  # 0 means trailing not yet active

    # ─── Reporting ────────────────────────────────────────────────────────────

    def daily_summary(self) -> dict:
        return {
            "date":             str(self.daily.date),
            "trades_taken":     self.daily.trades_taken,
            "realised_pnl":     round(self.daily.realised_pnl, 2),
            "capital":          round(self.current_capital, 2),
            "daily_dd_pct":     round(self._daily_drawdown_pct() * 100, 2),
            "drawdown_halt":    self.daily.drawdown_halt,
            "total_return_pct": round(
                (self.current_capital - self.initial_capital) / self.initial_capital * 100, 2
            ),
        }

    # ─── Internal Helpers ─────────────────────────────────────────────────────

    def _daily_drawdown_pct(self) -> float:
        if self.daily.peak_capital <= 0:
            return 0.0
        return -self.daily.realised_pnl / self.daily.peak_capital

    def _ensure_fresh_day(self):
        today = date.today()
        if self.daily.date != today:
            log.info(f"[RISK] New trading day {today} — resetting daily counters")
            self.daily.reset_for_new_day(self.current_capital)
