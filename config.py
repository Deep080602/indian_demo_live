"""
config.py — Central configuration for the ALGO PULSE Paper Trader.
Edit ONLY this file to change credentials, capital, index, and risk settings.
"""

import os
from dataclasses import dataclass, field
from typing import Literal

def _load_dotenv(path=".env"):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    os.environ[k] = v
        except Exception:
            pass

_load_dotenv()


@dataclass
class TradingConfig:
    # ─── Dhan API Credentials ─────────────────────────────────────────────────
    # ── Paste your Dhan credentials directly below ──
    # Get from: https://web.dhan.co → My Profile → My API Access
    client_id: str = os.environ.get("DHAN_CLIENT_ID", "")
    access_token: str = os.environ.get("DHAN_ACCESS_TOKEN", "")
    groww_client_id: str = os.environ.get("GROWW_CLIENT_ID", "")
    groww_pin: str = os.environ.get("GROWW_PIN", "")

    # ─── Capital & Universe ───────────────────────────────────────────────────
    capital: float = 200_000.0          # Starting paper capital (INR) - SPLIT between indices
    index: Literal["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"] = "SENSEX"
    timeframe: Literal["intraday", "positional"] = "intraday"

    # ─── Multi-Index Trading ──────────────────────────────────────────────────
    trading_indices: list = field(default_factory=lambda: [x.strip().upper() for x in os.environ.get("TRADING_INDICES", "NIFTY,SENSEX").split(",") if x.strip()])  # Indices to trade
    max_open_per_index: int = 2         # Max concurrent open positions per index
    max_daily_trades_per_index: int = 3  # Max trades per day per index

    # ─── Risk Parameters ──────────────────────────────────────────────────────
    risk_per_trade_pct: float = 0.05    # 5% of capital risked per trade
    target_per_trade_pct: float = 0.15  # 15% target on capital deployed
    sl_on_premium_pct: float = 0.05     # SL fires when trade loses 5% of deployed capital (NOTE: dynamic now)
    tp_on_premium_pct: float = 0.15     # TP fires when trade gains 15% of deployed capital (NOTE: dynamic now)
    max_trades_per_day: int = 6         # NIFTY 3 + SENSEX 3 per day
    daily_drawdown_limit_pct: float = 0.04   # Halt if day P&L < -4% of capital
    max_open_positions: int = 4              # Max 4 total (2 per index)

    # ─── Pulse-Guard Risk Engine ──────────────────────────────────────────────
    breakeven_enabled: bool = True
    breakeven_trigger_ratio: float = 1.0     # Trigger breakeven once price reaches +1.0R
    breakeven_buffer_pts: float = 0.5        # Buffer points added to entry to protect against transaction fees
    multi_stage_trail_enabled: bool = True   # Enable multi-stage trailing stop levels
    partial_booking_enabled: bool = False    # Book partial profits when target 1 hit (disabled by default)
    partial_booking_trigger_ratio: float = 1.0 # book partial at +1.0R
    partial_booking_pct: float = 0.5         # Book 50% of the position
    dynamic_rrr_enabled: bool = True         # Volatility-adjusted dynamic target RRR (1.5x - 2.2x)
    time_decay_exit_enabled: bool = True     # Close stale trade in loss to avoid theta decay
    time_decay_timeout_mins: int = 45        # Timeout stagnant trades after 45 minutes

    # ─── Indicator Parameters ─────────────────────────────────────────────────
    ema_fast: int = 9
    ema_slow: int = 21
    ema_trend: int = 50
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5      # SL = ATR * multiplier (for underlying)
    vwap_lookback_candles: int = 78     # ~390 min of 5m candles = 1 full session
    bb_period: int = 20
    bb_std: float = 2.0

    # ─── Volatility Filters ───────────────────────────────────────────────────
    vix_max: float = 20.0       # Do not trade above this VIX (panic mode) — tightened
    vix_min: float = 12.0       # Do not trade below this VIX (no movement) — tightened
    atr_min_pct: float = 0.005  # Min ATR/close ratio = 0.5% (INCREASED: need real moves, not noise)

    # ─── Option Selection ─────────────────────────────────────────────────────
    min_oi: int = 100_000           # Minimum open interest on the option
    min_volume: int = 1_000         # Minimum daily volume on the option
    max_spread_pct: float = 0.015   # Max (ask-bid)/mid ratio = 1.5%
    strike_selection: Literal["ATM", "ITM1", "ITM2"] = "ATM"
    option_max_premium: float = 300.0   # Never buy options above this price (avoid paying for decay)
    option_min_premium: float = 20.0    # Never buy options below this (too far OTM)

    # ─── SMC / Structure Parameters ──────────────────────────────────────────
    ob_lookback: int = 20       # Candles to look back for Order Block detection
    fvg_min_size_pct: float = 0.002  # Minimum FVG gap relative to price (0.2%)
    swing_lookback: int = 5     # Pivot high/low detection window

    # ─── Execution / Session ─────────────────────────────────────────────────
    paper_trading: bool = True
    scan_interval_seconds: int = 60     # How often the main loop scans (1 min)
    market_open: str = "09:15"          # Early scan — catch morning momentum
    market_close: str = "15:25"         # Capture full day movement
    force_exit_time: str = "15:25"      # Hard exit all positions at close
    log_file: str = "trades.csv"
    log_dir: str = "logs"
    # ─── Smart Signal Guard ───────────────────────────────────────────────────
    smart_filter_enabled: bool = True      # ON/OFF toggle for the Smart Signal Guard
    smart_min_trades: int = 5             # Min trades required to train model
    smart_win_threshold: float = 0.55     # Rejects signal if win probability < 55%
    dhan_super_order: bool = True          # ON/OFF toggle for advanced Dhan Super Order (Bracket OCO)
    trailing_sl_enabled: bool = True       # ON/OFF toggle for Trailing Stop Loss (TSL)
    # ─── WhatsApp Alerts ──────────────────────────────────────────────────────
    whatsapp_enabled: bool = False             # Set to True to enable WhatsApp trade alerts
    whatsapp_provider: str = "callmebot"       # "callmebot" (free & easy) or "twilio"
    
    # For CallMeBot (Free):
    # To get apikey: Add '+34 644 97 50 14' on WhatsApp, send message 'I allow callmebot to send me messages', get your API Key
    whatsapp_phone: str = "+919369092424"                   # Phone number with country code (e.g. "+919876543210")
    whatsapp_apikey: str = ""                  # CallMeBot API key
    
    # For Twilio (Paid / Sandbox):
    whatsapp_twilio_sid: str = ""              # Twilio Account SID
    whatsapp_twilio_auth_token: str = ""        # Twilio Auth Token
    whatsapp_twilio_from: str = "whatsapp:+14155238886"  # Twilio Sandbox number
    
    # For Green-API (Highly stable, QR-code based, free developer tier):
    # Create free instance at green-api.com, scan QR code using WhatsApp linked devices to connect your own number.
    whatsapp_green_instance_id: str = ""       # Green-API Instance ID (e.g. "110185...")
    whatsapp_green_api_token: str = ""         # Green-API API Token
    whatsapp_green_to_phone: str = ""          # Recipient phone with country code (e.g. "919876543210")

    # ─── ntfy.sh Mobile Push Alerts (Zero API/Keyless) ───────────────────────
    ntfy_enabled: bool = True                  # Set to True to enable ntfy mobile alerts
    ntfy_topic: str = "algo_pulse_alerts_dipma" # Custom unique/secret topic path for your push alerts

    # ─── Telegram Alerts ──────────────────────────────────────────────────────
    telegram_enabled: bool = os.environ.get("TELEGRAM_ENABLED", "False").lower() in ("true", "1", "yes")
    telegram_token: str = os.environ.get("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")

    # ─── AI / LLM Trading Brain ──────────────────────────────────────────────
    ai_brain_enabled: bool = True              # Enable/Disable the AI Brain module
    ai_autonomous_trading: bool = False        # Enable AI to place trades autonomously
    ai_model_type: str = "neural_network"       # "neural_network" or "gemini_llm"
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")



    # ─── Multi-Timeframe Candle Lengths ──────────────────────────────────────
    htf_minutes: int = 15   # Higher timeframe (trend)
    mtf_minutes: int = 5    # Medium timeframe (structure)
    ltf_minutes: int = 1    # Lower timeframe (entry)
    htf_candles: int = 100  # How many HTF candles to fetch
    mtf_candles: int = 200
    ltf_candles: int = 100

    # ─── Index-specific Derived Properties ───────────────────────────────────
    @property
    def lot_size(self) -> int:
        return {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 60,
            "MIDCPNIFTY": 120,
            "SENSEX": 20,
            "BANKEX": 30
        }.get(self.index, 65)

    @property
    def strike_gap(self) -> int:
        return {
            "NIFTY": 50,
            "BANKNIFTY": 100,
            "FINNIFTY": 50,
            "MIDCPNIFTY": 25,
            "SENSEX": 100,
            "BANKEX": 100
        }.get(self.index, 50)

    @property
    def dhan_security_id(self) -> str:
        """NSE index security IDs for Dhan API."""
        return {
            "NIFTY": "13",
            "BANKNIFTY": "25",
            "FINNIFTY": "27",
            "MIDCPNIFTY": "50",
            "SENSEX": "51",
            "BANKEX": "52"
        }.get(self.index, "13")

    @property
    def exchange_segment(self) -> str:
        return "IDX_I"

    @property
    def risk_amount_per_trade(self) -> float:
        return self.capital * self.risk_per_trade_pct

    @property
    def max_lots(self) -> int:
        """Maximum lots per trade based on risk budget."""
        # Assume avg option price of 100 for sizing; recalculated at entry
        assumed_premium = 100.0
        max_spend = self.risk_amount_per_trade / self.sl_on_premium_pct
        return max(1, int(max_spend / (assumed_premium * self.lot_size)))


# ─── MULTI-INDEX CONFIGURATION ────────────────────────────────────────────────
INDEX_CONFIG = {
    "NIFTY": {
        "yf_symbol": "^NSEI",
        "yf_source": "yahoo",           # "yahoo" for NIFTY
        "strike_gap": 50,
        "lot_size": 65,
        "option_min_premium": 20.0,
        "data_provider": "yfinance",    # Use Yahoo Finance for NIFTY data
        "exchange": "NSE",
    },
    "BANKNIFTY": {
        "yf_symbol": "^NSEBANK",
        "yf_source": "yahoo",
        "strike_gap": 100,
        "lot_size": 30,
        "option_min_premium": 50.0,
        "data_provider": "yfinance",
        "exchange": "NSE",
    },
    "FINNIFTY": {
        "yf_symbol": "^CNXFIN",
        "yf_source": "yahoo",
        "strike_gap": 50,
        "lot_size": 60,
        "option_min_premium": 20.0,
        "data_provider": "yfinance",
        "exchange": "NSE",
    },
    "MIDCPNIFTY": {
        "yf_symbol": "MIDCPNIFTY.NS",
        "yf_source": "yahoo",
        "strike_gap": 25,
        "lot_size": 120,
        "option_min_premium": 10.0,
        "data_provider": "yfinance",
        "exchange": "NSE",
    },
    "SENSEX": {
        "yf_symbol": "^BSESN",
        "yf_source": "groww",           # "groww" for Groww scraping
        "strike_gap": 100,
        "lot_size": 20,                 # SENSEX lot size = 20
        "option_min_premium": 50.0,
        "data_provider": "bse_selenium",  # Use BSE Selenium scraper for SENSEX data
        "exchange": "BSE",
    },
    "BANKEX": {
        "yf_symbol": "^BSEBANK",
        "yf_source": "yahoo",
        "strike_gap": 100,
        "lot_size": 30,
        "option_min_premium": 50.0,
        "data_provider": "yfinance",
        "exchange": "BSE",
    }
}

# Capital split across indices
CAPITAL_PER_INDEX = {
    "NIFTY": 100_000.0,
    "BANKNIFTY": 100_000.0,
    "FINNIFTY": 100_000.0,
    "MIDCPNIFTY": 100_000.0,
    "SENSEX": 100_000.0,
    "BANKEX": 100_000.0,
}


# Singleton — import and use everywhere
cfg = TradingConfig()
