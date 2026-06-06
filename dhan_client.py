"""
dhan_client.py — Correct DhanHQ-py v2.x wrapper.

SDK v2.1+ uses DhanContext:
    dhan_context = DhanContext(client_id, access_token)
    dhan = dhanhq(dhan_context)

Key API methods (from official GitHub):
    intraday_minute_data(security_id, exchange_segment, instrument_type, from_date, to_date, interval)
    historical_daily_data(security_id, exchange_segment, instrument_type, from_date, to_date)
    option_chain(under_security_id, under_exchange_segment, expiry)
    expiry_list(under_security_id, under_exchange_segment)
    ohlc_data(securities)
    ticker_data(securities)
    quote_data(securities)
"""

import time
import logging
from datetime import date, timedelta
from typing import Optional, Dict, Any, List
from functools import wraps

import pandas as pd

from config import cfg

log = logging.getLogger(__name__)


def retry(max_attempts=3, delay=1.0, backoff=2.0):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            attempt, wait = 0, delay
            while attempt < max_attempts:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    attempt += 1
                    if attempt >= max_attempts:
                        log.error(f"[DHAN] {fn.__name__} failed after {max_attempts} attempts: {exc}")
                        raise
                    log.warning(f"[DHAN] {fn.__name__} retry {attempt}: {exc} (wait {wait:.1f}s)")
                    time.sleep(wait)
                    wait *= backoff
        return wrapper
    return decorator

class MockDhanSDK:
    def intraday_minute_data(self, *args, **kwargs):
        return {"status": "success", "data": {}}

    def ticker_data(self, *args, **kwargs):
        return {"status": "success", "data": {}}

    def ohlc_data(self, *args, **kwargs):
        return {"status": "success", "data": {}}

    def quote_data(self, *args, **kwargs):
        return {"status": "success", "data": {}}

    def option_chain(self, *args, **kwargs):
        return {"status": "success", "data": {}}

    def expiry_list(self, *args, **kwargs):
        return {"status": "success", "data": []}

    def get_fund_limits(self, *args, **kwargs):
        return {"status": "success", "data": {}}

    def place_order(self, *args, **kwargs):
        return {"status": "success", "data": {"orderId": "MOCK_ORDER_ID"}}


class DhanClient:

    def __init__(self, client_id: Optional[str] = None, access_token: Optional[str] = None):
        self._initialized = False
        self._ctx_instance = None
        self._dhan_instance = None
        self.client_id = client_id
        self.access_token = access_token

    def _ensure_sdk(self):
        if self._initialized:
            return
        self._initialized = True
        try:
            client_id = self.client_id or cfg.client_id
            access_token = self.access_token or cfg.access_token
            if not client_id or not access_token:
                log.warning("[DHAN] ⚠️ Dhan credentials not configured. DhanClient running in uninitialized/mock mode.")
                self._dhan_instance = MockDhanSDK()
                self._ctx_instance = None
                return

            try:
                import dhanhq
                dhanhq_class = getattr(dhanhq, 'dhanhq')
                if hasattr(dhanhq, 'DhanContext'):
                    DhanContext = dhanhq.DhanContext
                    self._ctx_instance  = DhanContext(client_id, access_token)
                    self._dhan_instance = dhanhq_class(self._ctx_instance)
                    log.info(f"[DHAN] ✅ Initialized with DhanContext (v2.x) | client={client_id}")
                else:
                    # v1.x fallback
                    self._dhan_instance = dhanhq_class(client_id, access_token)
                    self._ctx_instance  = None
                    log.info(f"[DHAN] ✅ Initialized legacy (v1.x) | client={client_id}")
            except ImportError:
                log.warning("[DHAN] ⚠️ dhanhq module not found. DhanClient running in uninitialized/mock mode.")
                self._dhan_instance = MockDhanSDK()
                self._ctx_instance = None
        except Exception as e:
            log.warning(f"[DHAN] ⚠️ Dhan SDK initialization failed: {e}. DhanClient running in uninitialized/mock mode.")
            self._dhan_instance = MockDhanSDK()
            self._ctx_instance = None

    @property
    def _ctx(self):
        self._ensure_sdk()
        return self._ctx_instance

    @_ctx.setter
    def _ctx(self, value):
        self._ctx_instance = value

    @property
    def _dhan(self):
        self._ensure_sdk()
        return self._dhan_instance

    @_dhan.setter
    def _dhan(self, value):
        self._dhan_instance = value

    def _init_sdk(self):
        """Force initialization of the SDK, e.g. when setting credentials."""
        self._initialized = False
        self._ensure_sdk()

    # ─── Historical Candles ───────────────────────────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_candles(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        interval: int,          # minutes: 1, 3, 5, 15, 60, 240
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles.
        Dhan intraday supports: 1, 5, 15, 25, 60 minutes.
        3m and 4h are computed by resampling from 1m and 60m.
        """
        # Map to supported Dhan intervals
        dhan_interval = self._map_interval(interval)

        resp = self._dhan.intraday_minute_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            from_date=from_date,
            to_date=to_date,
            interval=dhan_interval,
        )

        df = self._parse_ohlcv(resp)

        # Resample if needed (3m from 1m, 4h=240m from 60m)
        if not df.empty and interval not in (1, 5, 15, 25, 60):
            df = self._resample(df, interval)

        return df

    def get_intraday_ohlcv(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        interval: int,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """Alias for get_candles to support older calls."""
        return self.get_candles(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )

    def _map_interval(self, minutes: int) -> int:
        """Map any interval to nearest Dhan-supported one."""
        supported = [1, 5, 15, 25, 60]
        if minutes in supported:
            return minutes
        if minutes == 3:
            return 1    # fetch 1m, resample to 3m
        if minutes == 240:
            return 60   # fetch 60m, resample to 4h
        # Find nearest
        return min(supported, key=lambda x: abs(x - minutes))

    @staticmethod
    def _resample(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
        """Resample fine-grain OHLCV to target timeframe."""
        rule = f"{target_minutes}min"
        resampled = df.resample(rule, closed="left", label="left").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna(subset=["open"])
        return resampled

    # ─── Live Data ────────────────────────────────────────────────────────────

    def get_ltp(self, security_id: str, exchange_segment: str) -> float:
        try:
            resp = self._dhan.ticker_data(
                securities={exchange_segment: [int(security_id)]}
            )
            data = resp.get("data", {}).get(exchange_segment, {})
            row  = data.get(str(security_id), data.get(int(security_id), {}))
            return float(row.get("last_price", row.get("LTP", 0)))
        except Exception as exc:
            log.debug(f"[DHAN] LTP failed: {exc}")
            return 0.0

    def get_ohlc(self, security_id: str, exchange_segment: str) -> Dict:
        try:
            resp = self._dhan.ohlc_data(
                securities={exchange_segment: [int(security_id)]}
            )
            data = resp.get("data", {}).get(exchange_segment, {})
            row  = data.get(str(security_id), data.get(int(security_id), {}))
            return {
                "open":       float(row.get("open", 0)),
                "high":       float(row.get("high", 0)),
                "low":        float(row.get("low",  0)),
                "close":      float(row.get("close", row.get("last_price", 0))),
                "last_price": float(row.get("last_price", 0)),
            }
        except Exception as exc:
            log.debug(f"[DHAN] OHLC failed: {exc}")
            return {}

    def get_india_vix(self) -> float:
        """India VIX — security_id=13 on IDX_I."""
        for sec_id in [13, 4, "13", "4"]:
            try:
                resp = self._dhan.ohlc_data(securities={"IDX_I": [sec_id]})
                data = resp.get("data", {}).get("IDX_I", {})
                row  = data.get(str(sec_id), data.get(sec_id, {}))
                ltp  = float(row.get("last_price", row.get("close", 0)))
                if ltp > 0:
                    return ltp
            except Exception:
                pass
        return 15.0

    # ─── Options ──────────────────────────────────────────────────────────────

    def get_option_chain(self, underlying_id: str, expiry: str) -> pd.DataFrame:
        try:
            segment = "IDX_B" if int(underlying_id) in (51, 52) else "IDX_I"
            resp = self._dhan.option_chain(
                under_security_id=int(underlying_id),
                under_exchange_segment=segment,
                expiry=expiry,
            )
            return self._parse_option_chain(resp)
        except Exception as exc:
            log.error(f"[DHAN] option_chain failed: {exc}")
            return pd.DataFrame()

    def get_nearest_expiry(self, underlying_id: str = None) -> Optional[str]:
        """Calculate nearest weekly expiry (no API call needed)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        IST   = ZoneInfo("Asia/Kolkata")
        today = datetime.now(IST).date()

        expiry_weekday = {
            "NIFTY": 3,
            "BANKNIFTY": 2,
            "FINNIFTY": 1,
            "MIDCPNIFTY": 0,
            "SENSEX": 4,
            "BANKEX": 0
        }.get(cfg.index, 3)
        days_ahead = (expiry_weekday - today.weekday()) % 7
        if days_ahead == 0:
            now_ist = datetime.now(IST)
            if now_ist.hour >= 15 and now_ist.minute >= 30:
                days_ahead = 7
        expiry = today + timedelta(days=days_ahead)
        return expiry.strftime("%Y-%m-%d")

    def get_expiry_list(self, underlying_id: str) -> List[str]:
        try:
            segment = "IDX_B" if int(underlying_id) in (51, 52) else "IDX_I"
            resp = self._dhan.expiry_list(
                under_security_id=int(underlying_id),
                under_exchange_segment=segment,
            )
            return sorted(resp.get("data", []))
        except Exception as exc:
            log.debug(f"[DHAN] expiry_list failed: {exc}")
            return []

    def get_broker_capital(self) -> Dict[str, float]:
        """
        Fetch actual capital details from Dhan API.
        Returns:
            dict containing "available" and "base" (SOD limit) capital values.
        """
        try:
            resp = self._dhan.get_fund_limits()
            if resp.get("status") == "success":
                data = resp.get("data", {})
                # Use standard spelling or the known API typo "availabelBalance"
                avail = data.get("availabelBalance", data.get("availableBalance", data.get("withdrawableBalance", 0.0)))
                sod = data.get("sodLimit", 0.0)
                return {
                    "available": float(avail),
                    "base": float(sod) if float(sod) > 0 else 0.0
                }
            else:
                log.error(f"[DHAN] Failed to fetch fund limits: {resp.get('remarks')}")
        except Exception as e:
            log.error(f"[DHAN] Exception fetching broker capital: {e}")
        return {"available": 0.0, "base": 0.0}

    # ─── Parsers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_ohlcv(resp: Dict) -> pd.DataFrame:
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")

        _empty = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="Asia/Kolkata", name="datetime"),
        )
        try:
            if resp.get("status") == "failure":
                err = resp.get("remarks", resp.get("data", {}))
                log.warning(f"[DHAN] API failure: {err}")
                return _empty

            data = resp.get("data", {})
            if not data or isinstance(data, str):
                return _empty

            timestamps = (data.get("timestamp") or data.get("start_time") or data.get("time") or [])
            opens  = data.get("open",   [])
            highs  = data.get("high",   [])
            lows   = data.get("low",    [])
            closes = data.get("close",  [])
            vols   = data.get("volume", [0] * len(timestamps))

            if not timestamps or not closes:
                return _empty

            sample = timestamps[0]
            if isinstance(sample, (int, float)):
                dt_index = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata")
            else:
                dt_index = pd.to_datetime(timestamps)
                if dt_index.tz is None:
                    dt_index = dt_index.tz_localize("Asia/Kolkata")
                else:
                    dt_index = dt_index.tz_convert("Asia/Kolkata")

            df = pd.DataFrame({
                "open":   pd.to_numeric(opens,  errors="coerce"),
                "high":   pd.to_numeric(highs,  errors="coerce"),
                "low":    pd.to_numeric(lows,   errors="coerce"),
                "close":  pd.to_numeric(closes, errors="coerce"),
                "volume": pd.to_numeric(vols,   errors="coerce").fillna(0).astype(int),
            }, index=dt_index)
            df.index.name = "datetime"
            df.dropna(subset=["open", "high", "low", "close"], inplace=True)
            df.sort_index(inplace=True)
            return df
        except Exception as exc:
            log.error(f"[DHAN] _parse_ohlcv error: {exc}")
            return _empty

    @staticmethod
    def _parse_option_chain(resp: Dict) -> pd.DataFrame:
        def _f(v):
            try: return float(v)
            except: return 0.0
        def _i(v):
            try: return int(v)
            except: return 0
        def _row(strike, side, s):
            return {
                "strike":      strike,
                "option_type": side,
                "security_id": str(s.get("security_id", s.get("securityId", ""))),
                "ltp":   _f(s.get("last_price", s.get("lastPrice", s.get("LTP", 0)))),
                "bid":   _f(s.get("buy_price",  s.get("bidPrice",  0))),
                "ask":   _f(s.get("sell_price", s.get("askPrice",  0))),
                "oi":    _i(s.get("oi",     s.get("OI",     0))),
                "volume":_i(s.get("volume", s.get("Volume", 0))),
                "iv":    _f(s.get("implied_volatility", s.get("IV", 0))),
                "delta": _f(s.get("delta", 0)),
                "theta": _f(s.get("theta", 0)),
            }
        rows = []
        try:
            data = resp.get("data", resp)
            oc   = (data.get("oc_data") or data.get("optionData") or
                    data.get("optionChain") or (data if isinstance(data, list) else None))
            if oc:
                for entry in oc:
                    strike = float(entry.get("strikePrice", entry.get("strike_price", 0)))
                    for side in ("CE", "PE"):
                        s = entry.get(side, {})
                        if s: rows.append(_row(strike, side, s))
            elif isinstance(data, dict):
                for key, val in data.items():
                    try:
                        strike = float(key)
                        for side in ("CE", "PE"):
                            s = val.get(side, {})
                            if s: rows.append(_row(strike, side, s))
                    except: continue
        except Exception as exc:
            log.error(f"[DHAN] _parse_option_chain: {exc}")

        df = pd.DataFrame(rows)
        if not df.empty:
            df.sort_values("strike", inplace=True)
            df.reset_index(drop=True, inplace=True)
        return df


dhan = DhanClient()
