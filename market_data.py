"""
market_data.py — Live data feed.
Only fetches data when called (no internal time checks).
main.py controls WHEN to call refresh_live().
"""
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional
import pandas as pd

from config import cfg
from dhan_client import dhan

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

def _now_ist() -> datetime:
    return datetime.now(IST)

def _date_range_for_live() -> tuple:
    """Last 5 trading days (covers indicator warmup + today's live candles)."""
    today = _now_ist().date()
    from_dt = today - timedelta(days=8)
    return from_dt.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


class MarketData:

    def __init__(self):
        self._htf: Optional[pd.DataFrame] = None
        self._mtf: Optional[pd.DataFrame] = None
        self._ltf: Optional[pd.DataFrame] = None
        self._chain: Optional[pd.DataFrame] = None
        self._vix: float = 15.0
        self._expiry: Optional[str] = None
        self._expiry_date: Optional[date] = None

        self._htf_ts: Optional[datetime] = None
        self._mtf_ts: Optional[datetime] = None
        self._ltf_ts: Optional[datetime] = None
        self._chain_ts: Optional[datetime] = None
        self._vix_ts: Optional[datetime] = None

    def refresh_live(self) -> bool:
        """Fetch latest live candles. Returns True if all 3 timeframes loaded."""
        now = datetime.now()
        h = self._load(cfg.htf_minutes, now, self._htf_ts, 14*60, 20)
        m = self._load(cfg.mtf_minutes, now, self._mtf_ts, 4*60,  20)
        l = self._load(cfg.ltf_minutes, now, self._ltf_ts, 55,     5)

        if h is not None: self._htf = h; self._htf_ts = now
        if m is not None: self._mtf = m; self._mtf_ts = now
        if l is not None: self._ltf = l; self._ltf_ts = now

        self._refresh_vix(now)
        self._refresh_chain(now)

        return self._htf is not None and self._mtf is not None and self._ltf is not None

    def _load(self, interval, now, last_ts, ttl, min_rows) -> Optional[pd.DataFrame]:
        """Fetch candles if cache is stale. Returns new df or None (use existing)."""
        if last_ts and (now - last_ts).total_seconds() < ttl:
            return None  # Cache still fresh

        from_date, to_date = _date_range_for_live()
        try:
            df = dhan.get_intraday_ohlcv(
                security_id=cfg.dhan_security_id,
                exchange_segment=cfg.exchange_segment,
                instrument_type="INDEX",
                interval=interval,
                from_date=from_date,
                to_date=to_date,
            )
            if df is None or df.empty:
                log.warning(f"[DATA] {interval}m: no data ({from_date}→{to_date}) — market may be closed")
                return None
            df = df.between_time("09:15", "15:30")
            if df.empty or len(df) < min_rows:
                log.warning(f"[DATA] {interval}m: only {len(df)} candles (need {min_rows})")
                return df if not df.empty else None
            log.info(f"[DATA] ✅ {interval}m: {len(df)} candles | "
                     f"LTP={df['close'].iloc[-1]:.0f} | "
                     f"Last={df.index[-1].strftime('%d-%b %H:%M')}")
            return df
        except Exception as exc:
            log.error(f"[DATA] {interval}m fetch error: {exc}")
            return None

    def _refresh_vix(self, now: datetime):
        if self._vix_ts and (now - self._vix_ts).total_seconds() < 300:
            return
        try:
            # Method 1: ohlc_data
            resp = dhan._dhan.ohlc_data(securities={"IDX_I": [13]})
            data = resp.get("data", {}).get("IDX_I", {})
            row  = data.get("13", data.get(13, {}))
            ltp  = float(row.get("last_price", row.get("close", 0)))
            if ltp > 0:
                self._vix = ltp
                self._vix_ts = now
                log.info(f"[DATA] ✅ India VIX (live): {ltp:.2f}")
                return
        except Exception as e:
            log.debug(f"[DATA] VIX ohlc_data failed: {e}")
        try:
            # Method 2: quote_data
            resp = dhan._dhan.quote_data(securities={"IDX_I": [13]})
            data = resp.get("data", {}).get("IDX_I", {})
            row  = data.get("13", data.get(13, {}))
            ltp  = float(row.get("last_price", row.get("ltp", 0)))
            if ltp > 0:
                self._vix = ltp
                self._vix_ts = now
                log.info(f"[DATA] ✅ India VIX (quote): {ltp:.2f}")
                return
        except Exception as e:
            log.debug(f"[DATA] VIX quote_data failed: {e}")
        log.debug(f"[DATA] VIX unavailable (market closed) — using {self._vix:.2f}")

    def _refresh_chain(self, now: datetime):
        if self._chain_ts and (now - self._chain_ts).total_seconds() < 240:
            return
        expiry = self.expiry
        if not expiry:
            return
        try:
            chain = dhan.get_option_chain(cfg.dhan_security_id, expiry)
            if chain is not None and not chain.empty:
                self._chain = chain
                self._chain_ts = now
                # Show best strikes around ATM
                ltp = self.underlying_ltp
                if ltp > 0:
                    atm = round(ltp / cfg.strike_gap) * cfg.strike_gap
                    near = chain[
                        (chain["strike"] >= atm - cfg.strike_gap * 3) &
                        (chain["strike"] <= atm + cfg.strike_gap * 3)
                    ]
                    log.info(f"[DATA] ✅ Chain loaded | Expiry={expiry} | "
                             f"ATM={atm:.0f} | Strikes around ATM:")
                    for _, row in near.iterrows():
                        log.info(f"         {row['option_type']} {row['strike']:.0f} | "
                                 f"LTP=₹{row['ltp']:.1f} | "
                                 f"OI={row['oi']:,} | IV={row['iv']:.1f}%")
                else:
                    log.info(f"[DATA] ✅ Chain: {len(chain)} rows | Expiry={expiry}")
        except Exception as exc:
            log.debug(f"[DATA] Chain error: {exc}")

    # Properties
    @property
    def htf(self): return self._htf
    @property
    def mtf(self): return self._mtf
    @property
    def ltf(self): return self._ltf
    @property
    def chain(self): return self._chain
    @property
    def vix(self): return self._vix

    @property
    def underlying_ltp(self) -> float:
        if self._ltf is not None and not self._ltf.empty:
            return float(self._ltf["close"].iloc[-1])
        return 0.0

    @property
    def expiry(self) -> Optional[str]:
        today = _now_ist().date()
        if self._expiry is None or self._expiry_date != today:
            self._expiry = dhan.get_nearest_expiry()
            self._expiry_date = today
        return self._expiry

    def get_live_option_prices(self, security_ids: List[str]) -> Dict[str, float]:
        prices = {}
        for sec_id in security_ids:
            if not sec_id: continue
            try:
                prices[sec_id] = dhan.get_ltp(sec_id, "NSE_FNO")
            except Exception as exc:
                log.warning(f"[DATA] LTP failed {sec_id}: {exc}")
                prices[sec_id] = 0.0
        return prices

    def status_line(self) -> str:
        return (f"HTF={len(self._htf) if self._htf is not None else 0} "
                f"MTF={len(self._mtf) if self._mtf is not None else 0} "
                f"LTF={len(self._ltf) if self._ltf is not None else 0} "
                f"LTP={self.underlying_ltp:.0f} VIX={self._vix:.2f}")
