"""
free_data.py — 100% FREE live market data using yfinance.
Robust version — handles all yfinance response formats and errors.
"""

import logging
import math
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict
import pandas as pd
import numpy as np

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

TICKER_MAP = {
    "NIFTY":     "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY":  "NIFTY_FIN_SERVICE.NS",
    "SENSEX":    "^BSESN",
    "MIDCPNIFTY":"^NSEI",   # fallback to NIFTY data
}

STRIKE_GAP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "SENSEX": 100}
LOT_SIZE   = {"NIFTY": 65, "BANKNIFTY": 30,  "FINNIFTY": 60, "SENSEX": 20}

def _now_ist():
    return datetime.now(IST)

def _resample(df, minutes):
    if df is None or df.empty:
        return pd.DataFrame()
    r = df.resample(f"{minutes}min", closed="left", label="left").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    return r

def _days_to_expiry(index):
    today = _now_ist().date()
    wd = {"NIFTY": 3, "FINNIFTY": 3, "BANKNIFTY": 2, "SENSEX": 4}.get(index, 3)
    days = (wd - today.weekday()) % 7
    if days == 0:
        now = _now_ist()
        if now.hour >= 15 and now.minute >= 30:
            days = 7
    return max(1, days)

def _next_expiry_str(index):
    today = _now_ist().date()
    return (today + timedelta(days=_days_to_expiry(index))).strftime("%Y-%m-%d")

# ─── Safe yfinance downloader ─────────────────────────────────────────────────

def _safe_download(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    """
    Download from yfinance with full error handling.
    Returns clean OHLCV DataFrame in IST or None.
    """
    import yfinance as yf

    for attempt in range(3):
        try:
            raw = yf.download(
                ticker,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
                multi_level_index=False,   # flat columns
            )

            # Guard: None or empty
            if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
                log.debug(f"[FREE] {ticker} {interval}: empty response (attempt {attempt+1})")
                time.sleep(1)
                continue

            # Flatten MultiIndex columns if present
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0].lower() for c in raw.columns]
            else:
                raw.columns = [str(c).lower().strip() for c in raw.columns]

            # Ensure required columns exist
            if "close" not in raw.columns:
                log.debug(f"[FREE] {ticker} {interval}: no 'close' col — got {list(raw.columns)}")
                time.sleep(1)
                continue

            for col in ["open", "high", "low", "volume"]:
                if col not in raw.columns:
                    raw[col] = raw["close"]

            raw = raw[["open", "high", "low", "close", "volume"]].copy()
            raw = raw.apply(pd.to_numeric, errors="coerce")
            raw.dropna(subset=["close"], inplace=True)

            if raw.empty:
                continue

            # Convert index to IST
            if not isinstance(raw.index, pd.DatetimeIndex):
                raw.index = pd.to_datetime(raw.index)
            if raw.index.tz is None:
                raw.index = raw.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
            else:
                raw.index = raw.index.tz_convert("Asia/Kolkata")
            raw.index.name = "datetime"

            # Filter market hours
            try:
                raw = raw.between_time("09:15", "15:30")
            except Exception:
                pass

            raw.dropna(subset=["open", "close"], inplace=True)

            if not raw.empty:
                return raw

        except Exception as exc:
            log.debug(f"[FREE] {ticker} {interval} attempt {attempt+1} error: {exc}")
            time.sleep(1)

    return None

# ─── Black-Scholes pricer ─────────────────────────────────────────────────────

def _bs_price(S, K, T, r, sigma, option_type="CE"):
    if T <= 0 or sigma <= 0:
        intrinsic = max(0, S-K) if option_type=="CE" else max(0, K-S)
        return max(0.5, intrinsic)
    try:
        from scipy.stats import norm
        d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
        d2 = d1 - sigma*math.sqrt(T)
        if option_type == "CE":
            p = S*norm.cdf(d1) - K*math.exp(-r*T)*norm.cdf(d2)
        else:
            p = K*math.exp(-r*T)*norm.cdf(-d2) - S*norm.cdf(-d1)
        return max(0.5, round(p, 1))
    except Exception:
        intrinsic  = max(0, S-K) if option_type=="CE" else max(0, K-S)
        time_value = S * sigma * math.sqrt(max(T, 0.001)) * 0.4
        return max(0.5, round(intrinsic + time_value, 1))

def build_simulated_chain(spot, index, vix=15.0, num_strikes=10):
    gap   = STRIKE_GAP.get(index, 50)
    dte   = _days_to_expiry(index)
    T     = dte / 365.0
    r     = 0.065
    sigma = vix / 100.0
    atm   = round(spot / gap) * gap
    rows  = []
    for i in range(-num_strikes, num_strikes+1):
        strike = atm + i * gap
        for opt in ("CE", "PE"):
            price = _bs_price(spot, strike, T, r, sigma, opt)
            dist  = abs(strike - spot) / spot
            oi    = max(50_000, int(2_000_000 * math.exp(-20*dist)))
            rows.append({
                "strike":      float(strike),
                "option_type": opt,
                "security_id": f"SIM_{index}_{strike}_{opt}_{dte}",
                "ltp":   price,
                "bid":   round(price*0.985, 1),
                "ask":   round(price*1.015, 1),
                "oi":    oi,
                "volume":max(5_000, int(oi*0.10)),
                "iv":    round(vix + np.random.uniform(-1,1), 2),
                "delta": round(0.5*math.exp(-5*dist)*(1 if opt=="CE" else -1), 3),
                "theta": round(-price/(dte*2), 2),
                "gamma": 0.0,
                "vega":  round(price*math.sqrt(T)*0.1, 2),
            })
    df = pd.DataFrame(rows)
    df.sort_values("strike", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def update_simulated_chain(chain, new_spot, index, vix):
    if chain is None or chain.empty:
        return build_simulated_chain(new_spot, index, vix)
    dte   = _days_to_expiry(index)
    T     = max(dte, 0.5) / 365.0
    sigma = vix / 100.0
    chain = chain.copy()
    for idx, row in chain.iterrows():
        price = _bs_price(new_spot, row["strike"], T, 0.065, sigma, row["option_type"])
        chain.at[idx, "ltp"] = price
        chain.at[idx, "bid"] = round(price*0.985, 1)
        chain.at[idx, "ask"] = round(price*1.015, 1)
    return chain

# ─── Main data class ──────────────────────────────────────────────────────────

class FreeMarketData:
    """
    Live NIFTY/BANKNIFTY data via Yahoo Finance (free).
    Options priced via Black-Scholes simulation.
    """

    # period to fetch per timeframe
    _PERIOD = {"1m": "5d", "5m": "60d", "15m": "60d", "1h": "730d"}
    _YF_INT = {"1m": "1m", "5m": "5m",  "15m": "15m", "1h": "60m"}
    _TTL    = {"1m": 55,   "5m": 240,   "15m": 840,   "1h": 3300}

    def __init__(self, index="NIFTY"):
        self.index  = index
        self.ticker = TICKER_MAP.get(index, "^NSEI")
        self._cache: Dict[str, Optional[pd.DataFrame]] = {
            tf: None for tf in ["1m","3m","5m","15m","1h","4h"]
        }
        self._ts:    Dict[str, Optional[datetime]] = {tf: None for tf in self._cache}
        self._chain:   Optional[pd.DataFrame] = None
        self._vix:     float = 15.0
        self._vix_ts:  Optional[datetime] = None
        self._expiry:  Optional[str] = None

    def refresh(self) -> bool:
        now = datetime.now()

        for base_tf in ["1m", "5m", "15m", "1h"]:
            ttl  = self._TTL[base_tf]
            last = self._ts.get(base_tf)
            if last and (now - last).total_seconds() < ttl:
                continue  # cache still fresh

            df = _safe_download(self.ticker, self._PERIOD[base_tf], self._YF_INT[base_tf])
            if df is not None and not df.empty:
                self._cache[base_tf] = df
                self._ts[base_tf]    = now
                log.info(f"[FREE] ✅ {base_tf}: {len(df)} candles | "
                         f"LTP={df['close'].iloc[-1]:.0f} | "
                         f"Last={df.index[-1].strftime('%d-%b %H:%M')}")
            elif self._cache[base_tf] is not None:
                log.debug(f"[FREE] {base_tf}: using cached data")
            else:
                log.warning(f"[FREE] {base_tf}: no data available")

        # Derive 3m from 1m
        src1m = self._cache.get("1m")
        if src1m is not None and not src1m.empty:
            d3 = _resample(src1m, 3)
            if not d3.empty:
                self._cache["3m"] = d3
                self._ts["3m"]    = now
                log.info(f"[FREE] ✅ 3m (resampled): {len(d3)} candles")

        # Derive 4h from 1h
        src1h = self._cache.get("1h")
        if src1h is not None and not src1h.empty:
            d4h = _resample(src1h, 240)
            if not d4h.empty:
                self._cache["4h"] = d4h
                self._ts["4h"]    = now
                log.info(f"[FREE] ✅ 4h (resampled): {len(d4h)} candles")

        # VIX
        self._fetch_vix(now)

        # Option chain
        ltp = self.underlying_ltp
        if ltp > 0:
            if self._chain is None:
                self._chain  = build_simulated_chain(ltp, self.index, self._vix)
                self._expiry = _next_expiry_str(self.index)
                atm = round(ltp / STRIKE_GAP.get(self.index,50)) * STRIKE_GAP.get(self.index,50)
                log.info(f"[FREE] ✅ Chain built | ATM={atm:.0f} | Expiry={self._expiry}")
            else:
                self._chain = update_simulated_chain(self._chain, ltp, self.index, self._vix)

        return (self._cache.get("3m")  is not None and
                self._cache.get("15m") is not None and
                self._cache.get("1h")  is not None)

    def _fetch_vix(self, now):
        if self._vix_ts and (now - self._vix_ts).total_seconds() < 300:
            return
        df = _safe_download("^INDIAVIX", "5d", "1m")
        if df is not None and not df.empty:
            v = float(df["close"].iloc[-1])
            if v > 0:
                self._vix    = v
                self._vix_ts = now
                log.info(f"[FREE] ✅ India VIX: {v:.2f}")

    def get(self, tf):
        return self._cache.get(tf)

    @property
    def underlying_ltp(self):
        for tf in ["1m","5m","15m"]:
            df = self._cache.get(tf)
            if df is not None and not df.empty:
                return float(df["close"].iloc[-1])
        return 0.0

    @property
    def vix(self): return self._vix

    @property
    def chain(self): return self._chain

    @property
    def expiry(self):
        if not self._expiry:
            self._expiry = _next_expiry_str(self.index)
        return self._expiry

    def get_live_option_price(self, security_id):
        if self._chain is None: return 0.0
        row = self._chain[self._chain["security_id"] == security_id]
        return float(row["ltp"].iloc[0]) if not row.empty else 0.0

    def status(self):
        parts = [f"{tf}={len(self._cache[tf]) if self._cache[tf] is not None else 0}"
                 for tf in ["1m","3m","5m","15m","1h","4h"]]
        return " | ".join(parts) + f" | LTP={self.underlying_ltp:.0f} | VIX={self._vix:.2f}"