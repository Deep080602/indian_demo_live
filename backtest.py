"""
backtest.py — Historical replay backtester.

How it works:
  - Fetches historical OHLCV for the index (daily chunks)
  - Simulates the scan loop tick-by-tick on historical 1m data
  - Generates signals using StrategyEngine (same production logic)
  - Simulates option pricing with simplified model (ATR-based premium proxy)
  - Tracks P&L, win rate, drawdown metrics

IMPORTANT:
  - This is NOT a Monte Carlo simulation — it replays actual historical price data.
  - Options pricing is approximated. Use with caution; live performance may differ.
  - Best used for parameter sensitivity testing, NOT as proof of profitability.

Usage:
  python backtest.py --from 2024-10-01 --to 2025-01-31 --capital 500000
"""

import argparse
import logging
import os
from datetime import datetime, timedelta, date
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

from config import cfg
from indicators import compute_all
from smc_analysis import SMCAnalyzer, Direction
from strategy_engine import StrategyEngine, TradeSignal, SignalType

log = logging.getLogger("backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ─── Simplified Option Pricer ─────────────────────────────────────────────────

def estimate_option_premium(
    spot: float,
    strike: float,
    days_to_expiry: int,
    atr: float,
    option_type: str,
    vix: float = 15.0,
) -> float:
    """
    Simplified Black-Scholes-like premium estimate for backtesting.
    NOT suitable for live trading — use real chain for live.
    """
    sigma = vix / 100.0
    T = max(days_to_expiry, 0.5) / 365.0

    intrinsic = max(0, spot - strike) if option_type == "CE" else max(0, strike - spot)
    time_value = spot * sigma * np.sqrt(T) * 0.4  # Rough proxy

    return max(1.0, round(intrinsic + time_value, 1))


# ─── BacktestTrade ────────────────────────────────────────────────────────────

class BacktestTrade:
    def __init__(
        self,
        signal_type: SignalType,
        entry_bar: int,
        entry_price: float,
        entry_spot: float,
        strike: float,
        option_type: str,
        lots: int,
        lot_size: int,
        sl: float,
        tp: float,
        entry_ts: pd.Timestamp,
        confidence: float,
    ):
        self.signal_type  = signal_type
        self.entry_bar    = entry_bar
        self.entry_price  = entry_price
        self.entry_spot   = entry_spot
        self.strike       = strike
        self.option_type  = option_type
        self.lots         = lots
        self.contracts    = lots * lot_size
        self.sl           = sl
        self.tp           = tp
        self.entry_ts     = entry_ts
        self.confidence   = confidence

        self.exit_price   = 0.0
        self.exit_ts      = None
        self.exit_reason  = ""
        self.pnl          = 0.0
        self.highest_seen = entry_price
        self.is_open      = True

    @property
    def pnl_pct(self):
        cost = self.entry_price * self.contracts
        return self.pnl / cost * 100 if cost > 0 else 0


# ─── Backtester ───────────────────────────────────────────────────────────────

class Backtester:

    def __init__(self, capital: float = None):
        self.capital           = capital or cfg.capital
        self.initial_capital   = self.capital
        self.strategy          = StrategyEngine()
        self.smc               = SMCAnalyzer()
        self.trades: List[BacktestTrade] = []
        self.daily_pnl: dict   = {}
        self.equity_curve: List[Tuple[pd.Timestamp, float]] = []

    def run(self, htf_df: pd.DataFrame, mtf_df: pd.DataFrame, ltf_df: pd.DataFrame) -> dict:
        """
        Run the backtest on pre-loaded OHLCV DataFrames.
        All three DataFrames must cover the same date range.
        """
        log.info(f"[BT] Starting backtest | Capital={self.capital:,.0f} | "
                 f"LTF rows={len(ltf_df)}")

        # HTF and MTF warmup periods
        htf_warmup = 80
        mtf_warmup = 100

        # Iterate over LTF candles starting after warmup
        for i in range(100, len(ltf_df)):
            current_ts   = ltf_df.index[i]
            current_spot = ltf_df["close"].iloc[i]
            current_date = current_ts.date()

            # Daily reset tracking
            day_str = str(current_date)
            if day_str not in self.daily_pnl:
                self.daily_pnl[day_str] = {
                    "trades": 0, "pnl": 0.0, "halted": False
                }

            # Update open trades
            self._update_open_trades(i, ltf_df, current_spot, current_ts)

            # Daily drawdown halt check
            day_data = self.daily_pnl[day_str]
            if day_data["halted"]:
                continue
            if day_data["pnl"] < -self.capital * cfg.daily_drawdown_limit_pct:
                day_data["halted"] = True
                log.debug(f"[BT] Daily halt: {day_str}")
                continue

            # Max trades per day
            if day_data["trades"] >= cfg.max_trades_per_day:
                continue

            # Only 1 open position
            open_count = sum(1 for t in self.trades if t.is_open)
            if open_count >= cfg.max_open_positions:
                continue

            # Market hours filter
            hour_min = current_ts.hour * 60 + current_ts.minute
            open_hm  = 9 * 60 + 20
            close_hm = 15 * 60 + 15
            if not (open_hm <= hour_min <= close_hm):
                continue

            # Slice DataFrames up to current time
            ltf_slice = ltf_df.iloc[:i + 1]
            mtf_idx   = mtf_df.index.searchsorted(current_ts)
            htf_idx   = htf_df.index.searchsorted(current_ts)
            mtf_slice = mtf_df.iloc[:mtf_idx + 1]
            htf_slice = htf_df.iloc[:htf_idx + 1]

            if len(htf_slice) < htf_warmup or len(mtf_slice) < mtf_warmup:
                continue

            # Estimate VIX (constant for backtest; replace with historical VIX data if available)
            mock_vix = 14.5

            # Build a mock options chain from estimated premiums
            days_to_exp = self._days_to_next_expiry(current_date)
            atr_val     = ltf_slice["close"].pct_change().std() * current_spot * 15

            mock_chain = self._build_mock_chain(current_spot, days_to_exp, mock_vix)

            # Generate signal
            try:
                signal = self.strategy.generate_signal(
                    htf_df=htf_slice,
                    mtf_df=mtf_slice,
                    ltf_df=ltf_slice,
                    underlying_ltp=current_spot,
                    vix=mock_vix,
                    option_chain=mock_chain,
                    expiry=self._next_expiry_str(current_date),
                )
            except Exception:
                continue

            if signal is None:
                continue

            # Size position
            premium    = signal.option_ltp
            risk_amt   = self.capital * cfg.risk_per_trade_pct
            max_loss_per_lot = premium * cfg.sl_on_premium_pct * cfg.lot_size
            lots = max(1, int(risk_amt / max_loss_per_lot)) if max_loss_per_lot > 0 else 1
            lots = min(lots, 5)  # Cap at 5 lots for backtest

            sl_price = signal.sl_price
            tp_price = signal.tp_price

            trade = BacktestTrade(
                signal_type=signal.signal_type,
                entry_bar=i,
                entry_price=premium,
                entry_spot=current_spot,
                strike=signal.strike,
                option_type=signal.option_type,
                lots=lots,
                lot_size=cfg.lot_size,
                sl=sl_price,
                tp=tp_price,
                entry_ts=current_ts,
                confidence=signal.confidence_score,
            )

            self.trades.append(trade)
            day_data["trades"] += 1
            log.info(
                f"[BT] {current_ts.strftime('%Y-%m-%d %H:%M')} ENTRY {signal.signal_type.value} "
                f"strike={signal.strike} premium={premium:.1f} sl={sl_price:.1f} tp={tp_price:.1f} "
                f"conf={signal.confidence_score:.0f}"
            )

            # Track equity
            self.equity_curve.append((current_ts, self.capital))

        # Force close remaining trades
        for trade in self.trades:
            if trade.is_open:
                trade.exit_price  = trade.entry_price * 0.70  # Assume expires ~30% loss
                trade.exit_reason = "EXPIRED"
                trade.pnl = (trade.exit_price - trade.entry_price) * trade.contracts
                trade.is_open = False
                self.capital += trade.pnl

        return self._generate_report()

    def _update_open_trades(self, i: int, ltf: pd.DataFrame, spot: float, ts: pd.Timestamp):
        """Simulate option price movement and check SL/TP."""
        current_date = ts.date()
        days_to_exp  = self._days_to_next_expiry(current_date)

        for trade in self.trades:
            if not trade.is_open:
                continue

            # Approximate current option price from spot move
            spot_move_pct = (spot - trade.entry_spot) / trade.entry_spot
            if trade.option_type == "CE":
                # Delta ~0.5 ATM, premium moves roughly 0.4x underlying move
                opt_price = trade.entry_price * (1 + spot_move_pct * 0.5 / 0.1)
            else:
                opt_price = trade.entry_price * (1 - spot_move_pct * 0.5 / 0.1)

            # Theta decay (simplified): lose ~1/DTE per day
            bars_held = i - trade.entry_bar
            bars_per_day = 375  # 1m bars per trading day
            days_held = bars_held / bars_per_day
            theta_decay = 1 - min(0.5, days_held / max(days_to_exp, 1) * 0.3)
            opt_price = max(1.0, opt_price * theta_decay)

            trade.highest_seen = max(trade.highest_seen, opt_price)

            # Trailing stop
            if opt_price >= trade.entry_price * 1.5:
                trail_sl = trade.highest_seen * 0.70
                trade.sl = max(trade.sl, trail_sl)

            # Force exit at session end
            hour_min = ts.hour * 60 + ts.minute
            if hour_min >= 15 * 60 + 20:
                exit_p = opt_price * 0.998
                self._close_trade(trade, exit_p, "FORCE_EXIT_EOD", ts)
                continue

            # SL / TP
            if opt_price <= trade.sl:
                self._close_trade(trade, opt_price, "STOP_LOSS", ts)
            elif opt_price >= trade.tp:
                self._close_trade(trade, opt_price, "TARGET", ts)

    def _close_trade(self, trade: BacktestTrade, price: float, reason: str, ts: pd.Timestamp):
        pnl = (price - trade.entry_price) * trade.contracts
        trade.exit_price  = price
        trade.exit_ts     = ts
        trade.exit_reason = reason
        trade.pnl         = round(pnl, 2)
        trade.is_open     = False
        self.capital     += pnl

        day_str = str(ts.date())
        if day_str in self.daily_pnl:
            self.daily_pnl[day_str]["pnl"] += pnl

        emoji = "🟢" if pnl > 0 else "🔴"
        log.info(
            f"[BT] {ts.strftime('%Y-%m-%d %H:%M')} {emoji} CLOSE {reason} "
            f"entry={trade.entry_price:.1f} exit={price:.1f} pnl={pnl:+.0f} "
            f"pnl%={trade.pnl_pct:+.1f}%"
        )

    def _build_mock_chain(self, spot: float, days_to_exp: int, vix: float) -> pd.DataFrame:
        """Create a synthetic option chain for backtesting."""
        rows = []
        atm  = round(spot / cfg.strike_gap) * cfg.strike_gap
        strikes = [atm + i * cfg.strike_gap for i in range(-5, 6)]

        for strike in strikes:
            for opt_type in ("CE", "PE"):
                prem = estimate_option_premium(spot, strike, days_to_exp, 0, opt_type, vix)
                if prem < cfg.option_min_premium or prem > cfg.option_max_premium:
                    continue
                rows.append({
                    "strike":      strike,
                    "option_type": opt_type,
                    "security_id": f"MOCK_{strike}_{opt_type}",
                    "ltp":         prem,
                    "bid":         round(prem * 0.99, 1),
                    "ask":         round(prem * 1.01, 1),
                    "oi":          500_000,
                    "volume":      50_000,
                    "iv":          vix,
                    "delta":       0.5 if strike == atm else (0.3 if opt_type == "CE" else -0.3),
                })

        return pd.DataFrame(rows)

    @staticmethod
    def _days_to_next_expiry(d: date) -> int:
        """Approximate days to next Thursday (weekly expiry)."""
        days_ahead = (3 - d.weekday()) % 7   # Thursday = 3
        return max(1, days_ahead)

    @staticmethod
    def _next_expiry_str(d: date) -> str:
        days_ahead = (3 - d.weekday()) % 7
        expiry = d + timedelta(days=days_ahead if days_ahead > 0 else 7)
        return expiry.strftime("%Y-%m-%d")

    def _generate_report(self) -> dict:
        if not self.trades:
            return {"error": "No trades generated"}

        total     = len(self.trades)
        wins      = [t for t in self.trades if t.pnl > 0]
        losses    = [t for t in self.trades if t.pnl <= 0]
        all_pnls  = [t.pnl for t in self.trades]

        win_rate  = len(wins) / total * 100
        avg_win   = np.mean([t.pnl for t in wins])   if wins   else 0
        avg_loss  = np.mean([t.pnl for t in losses]) if losses else 0
        rr        = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        # Drawdown
        cumulative  = np.cumsum(all_pnls)
        peak        = np.maximum.accumulate(cumulative)
        dd          = peak - cumulative
        max_dd      = dd.max()
        max_dd_pct  = max_dd / self.initial_capital * 100

        # Expectancy
        expectancy  = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

        # Sharpe (simplified, daily)
        pnl_series = pd.Series(all_pnls)
        sharpe = (pnl_series.mean() / pnl_series.std() * np.sqrt(252)) if pnl_series.std() > 0 else 0

        report = {
            "=== BACKTEST REPORT ===": "",
            "Period": f"{self.trades[0].entry_ts.date()} → {self.trades[-1].entry_ts.date()}",
            "Initial Capital":    f"₹{self.initial_capital:,.0f}",
            "Final Capital":      f"₹{self.capital:,.0f}",
            "Total Return":       f"{(self.capital-self.initial_capital)/self.initial_capital*100:+.2f}%",
            "Total Trades":       total,
            "Win Rate":           f"{win_rate:.1f}%",
            "Avg Win":            f"₹{avg_win:,.0f}",
            "Avg Loss":           f"₹{avg_loss:,.0f}",
            "R:R Ratio":          f"{rr:.2f}",
            "Expectancy/Trade":   f"₹{expectancy:,.0f}",
            "Max Drawdown":       f"₹{max_dd:,.0f} ({max_dd_pct:.2f}%)",
            "Sharpe Ratio":       f"{sharpe:.2f}",
            "Exit Breakdown": {
                r: sum(1 for t in self.trades if t.exit_reason == r)
                for r in set(t.exit_reason for t in self.trades)
            },
        }

        # Save trades to CSV
        trades_df = pd.DataFrame([{
            "entry_time":   t.entry_ts,
            "signal":       t.signal_type.value,
            "strike":       t.strike,
            "option_type":  t.option_type,
            "lots":         t.lots,
            "entry_price":  t.entry_price,
            "exit_price":   t.exit_price,
            "exit_time":    t.exit_ts,
            "exit_reason":  t.exit_reason,
            "pnl":          t.pnl,
            "pnl_pct":      t.pnl_pct,
            "confidence":   t.confidence,
        } for t in self.trades])

        os.makedirs(cfg.log_dir, exist_ok=True)
        trades_df.to_csv(os.path.join(cfg.log_dir, "backtest_trades.csv"), index=False)

        return report


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dhan Options Backtester")
    parser.add_argument("--from", dest="from_date", default="2024-10-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--to",   dest="to_date",   default="2024-12-31", help="End date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=500_000, help="Starting capital")
    args = parser.parse_args()

    cfg.capital = args.capital

    log.info(f"Backtesting {cfg.index} from {args.from_date} to {args.to_date}")
    log.info("Loading historical data from Dhan API...")

    from dhan_client import dhan as dhan_client

    def _load(interval: int, from_date: str, to_date: str):
        # Dhan data limits: 1m=5days, 5m/15m=90days, 60m=365days
        try:
            df = dhan_client.get_intraday_ohlcv(
                security_id=cfg.dhan_security_id,
                exchange_segment=cfg.exchange_segment,
                instrument_type="INDEX",
                interval=interval,
                from_date=from_date,
                to_date=to_date,
            )
            if df is None or df.empty:
                log.error(f"[LOAD] No data for interval={interval}m")
                return None
            if not isinstance(df.index, pd.DatetimeIndex):
                log.error(f"[LOAD] Not DatetimeIndex for interval={interval}m")
                return None
            df = df.between_time("09:15", "15:30")
            log.info(f"[LOAD] {interval}m: {len(df)} candles")
            return df
        except Exception as exc:
            log.error(f"[LOAD] interval={interval}m failed: {exc}")
            return None

    # NOTE: Dhan limits 1m to last 5 trading days only.
    # Backtest uses 5m as LTF to support multi-month date ranges.
    htf = _load(15, args.from_date, args.to_date)
    mtf = _load(5,  args.from_date, args.to_date)
    ltf = _load(5,  args.from_date, args.to_date)  # 5m, not 1m (Dhan limit)

    if not any([htf is not None and not htf.empty,
                mtf is not None and not mtf.empty,
                ltf is not None and not ltf.empty]):
        log.error("All data loads failed. Check credentials and date range.")
        exit(1)

    if htf is None or htf.empty: log.error("HTF failed"); exit(1)
    if mtf is None or mtf.empty: log.error("MTF failed"); exit(1)
    if ltf is None or ltf.empty: log.error("LTF failed"); exit(1)

    log.info(f"Loaded: HTF={len(htf)}, MTF={len(mtf)}, LTF={len(ltf)} candles")

    bt = Backtester(capital=args.capital)
    report = bt.run(htf, mtf, ltf)

    log.info("\n" + "=" * 50)
    for k, v in report.items():
        log.info(f"  {k}: {v}")
    log.info("=" * 50)
    log.info(f"Trades saved to {cfg.log_dir}/backtest_trades.csv")
