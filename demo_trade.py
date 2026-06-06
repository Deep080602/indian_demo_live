"""
demo_trade.py — REFINED MULTI-INDEX NIFTY + SENSEX Paper Trader
Data    : Yahoo Finance (NIFTY) + Groww scraper (SENSEX) for OHLCV
Prices  : NSE option chain (real LTP via Selenium)
Expiry  : Tuesday (weekly)
Strategy: Pullback-based entries + Dynamic SL/TP + Multi-timeframe confirmation
Multi-Index: Trade NIFTY and SENSEX simultaneously with split capital (Rs.100k each)
"""

import csv, json, logging, math, os, re, signal, subprocess, sys, threading, time

if sys.stdout is not None:
    _reconfig_out = getattr(sys.stdout, "reconfigure", None)
    if _reconfig_out is not None:
        try:
            _reconfig_out(encoding='utf-8')
        except Exception:
            pass
if sys.stderr is not None:
    _reconfig_err = getattr(sys.stderr, "reconfigure", None)
    if _reconfig_err is not None:
        try:
            _reconfig_err(encoding='utf-8')
        except Exception:
            pass

# ── Patch undetected_chromedriver to fix OSError WinError 6 on Python 3.13/Windows ──
try:
    import undetected_chromedriver as _uc
    _orig_quit = _uc.Chrome.quit
    def _safe_quit(self, *a, **kw):
        try: _orig_quit(self, *a, **kw)
        except OSError: pass
    _uc.Chrome.quit = _safe_quit
except Exception:
    pass
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from alerts import alert_entry, alert_win, alert_loss, alert_target_hit, alert_daily_halt
from flask import Flask, jsonify, request, session, redirect, url_for
from config import INDEX_CONFIG, CAPITAL_PER_INDEX, cfg
from smart_filter import smart_filter
from dhan_live import place_dhan_order, place_dhan_super_order, cancel_dhan_super_order
from groww_live import place_groww_order
from expiry_manager import ExpiryManager
import db_helper
# ─── CONFIG ───────────────────────────────────────────────────────────────────
CAPITAL       = 200_000
FIXED_LOTS    = 5
LOT_SIZE      = 65
STRIKE_GAP    = 50
TARGET_PCT    = 0.20   # 20% profit (will use dynamic)
SL_PCT        = 0.15   # 15% stop loss (will use dynamic)
MAX_TRADES    = 6      # Total per day: 3 per index × 2 indices
DAILY_DD      = 0.04
PROFIT_TARGET = 10_000
SCAN_SECS     = 60
LOG_DIR       = "logs"
LIMIT_IN      = 0.0    # entry = real LTP + 0
LIMIT_OUT     = 0.0    # exit  = real LTP - 0
COOLDOWN_MIN  = 20     # Reduced from 45 — allow trades more frequently in trends
MARKET_OPEN   = "09:18"  # Start scan early to catch morning momentum
MARKET_CLOSE  = "15:25"  # Force-exit close — capture full day movement
VIX_MIN       = 10.0     # Relaxed: volatility filters
VIX_MAX       = 25.0
ATR_MIN_PCT   = 0.003    # 0.3% min movement (relaxed)
MAX_TRADES_PER_INDEX       = 3  # Per index daily limit
MAX_DAILY_TRADES_PER_INDEX = MAX_TRADES_PER_INDEX   # alias used in Book class
MAX_OPEN_PER_INDEX         = 2  # Concurrent positions per index
TRADING_INDICES            = cfg.trading_indices
# Dynamically set cross-platform Chromedriver path
_env_chromedriver = os.environ.get("CHROMEDRIVER_PATH")
if _env_chromedriver:
    CHROMEDRIVER = _env_chromedriver
else:
    if os.name == 'nt':
        CHROMEDRIVER = (
            r"C:\Users\DIPMA\.wdm\drivers\chromedriver\win64"
            r"\147.0.7727.117\chromedriver-win32\chromedriver.exe"
        )
    else:
        # Default Linux chromedriver path (typically in PATH on cloud services)
        CHROMEDRIVER = "chromedriver"
FLAT_CHARGE_PER_ORDER = 40.0  # Flat charge in Rs. per executed order (brokerage + standard tax estimate)

IST = ZoneInfo("Asia/Kolkata")
os.makedirs(LOG_DIR, exist_ok=True)
vix = 15.0

# ─── PERSISTENT CAPITAL ───────────────────────────────────────────────────────
CAPITAL_FILE = os.path.join(LOG_DIR, "capital.json")

def _load_capital() -> float:
    """Load saved capital from previous session."""
    try:
        path = _get_capital_file()
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            saved = float(data.get("capital", CAPITAL))
            date_saved = data.get("date", "")
            log.info(f"[CAPITAL] Loaded Rs.{saved:,.0f} from {date_saved}")
            return saved
    except Exception as e:
        log.warning(f"[CAPITAL] Could not load saved capital: {e}")
    log.info(f"[CAPITAL] Starting fresh with Rs.{CAPITAL:,.0f}")
    return float(CAPITAL)

def _save_capital(capital: float):
    """Save current capital for next session."""
    try:
        base_val = CAPITAL
        path = _get_capital_file()
        if os.path.exists(path):
            try:
                with open(path) as f:
                    old_data = json.load(f)
                    base_val = float(old_data.get("base", CAPITAL))
            except:
                pass
        data = {
            "capital": round(capital, 2),
            "date":    datetime.now(IST).strftime("%Y-%m-%d %H:%M"),
            "base":    base_val,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"[CAPITAL] Could not save capital: {e}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{LOG_DIR}/nifty_trader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("trader")
_running = True
_trading_active = True
_live_trading = False
_active_broker = "GROWW"

def _get_capital_file() -> str:
    return os.path.join(LOG_DIR, "capital_live.json" if _live_trading else "capital.json")

def _get_trades_file() -> str:
    return os.path.join(LOG_DIR, "trades_live.csv" if _live_trading else "trades.csv")

# ─── DASHBOARD ─────────────────────────────────────────────────────────────────
_dashboard_app = Flask(__name__)
_dashboard_app.secret_key = os.environ.get("FLASK_SECRET_KEY", "algo_trading_super_secret_key_123")
_dashboard_thread: Optional[threading.Thread] = None
active_books: Dict[int, 'Book'] = {}

def get_user_book(user_id: int) -> 'Book':
    """Get or create the user's Book instance in memory."""
    global active_books
    if user_id not in active_books:
        active_books[user_id] = Book(user_id)
    return active_books[user_id]

# ─── VIX BACKGROUND CACHE ─────────────────────────────────────────────────────
_vix_value: float = 15.0
_vix_lock  = threading.Lock()
_vix_ts: Optional[datetime] = None

def _refresh_vix_cache() -> None:
    """Fetch VIX and update cache — runs in its own thread so API calls stay fast."""
    global _vix_value, _vix_ts
    try:
        val = _get_vix()
        with _vix_lock:
            _vix_value = val
            _vix_ts    = datetime.now()
    except Exception as e:
        log.debug(f"[VIX] background refresh failed: {e}")

def _get_cached_vix() -> float:
    """Return cached VIX. Triggers a background refresh if cache is stale (>60 s)."""
    with _vix_lock:
        ts  = _vix_ts
        val = _vix_value
    if ts is None or (datetime.now() - ts).total_seconds() > 60:
        threading.Thread(target=_refresh_vix_cache, daemon=True).start()
    return val

def _read_dashboard_data():
    """Gather dashboard data from logs and trades."""
    path = _get_trades_file()
    trades = []
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        tid = row.get("TradeID", "")
                        idx = row.get("Index", "NIFTY")
                        contracts = int(row.get("Contracts", 0) or 0)
                        entry_px = float(row.get("EntryPrice", 0) or 0)
                        exit_px = float(row.get("ExitPrice", 0) or 0)
                        total_charges = float(row.get("Charges", 0) or 0)
                        
                        # Read breakdown if present in CSV columns
                        brokerage = float(row.get("Brokerage") or 0) if "Brokerage" in row else 0.0
                        gst = float(row.get("GST") or 0) if "GST" in row else 0.0
                        stt = float(row.get("STT") or 0) if "STT" in row else 0.0
                        stamp_duty = float(row.get("StampDuty") or 0) if "StampDuty" in row else 0.0
                        exchange_charges = float(row.get("ExchangeCharges") or 0) if "ExchangeCharges" in row else 0.0
                        sebi_fee = float(row.get("SEBIFee") or 0) if "SEBIFee" in row else 0.0
                        
                        # On-the-fly fallback calculation for legacy trades
                        if (gst == 0.0 or stt == 0.0 or exchange_charges == 0.0) and contracts > 0 and entry_px > 0 and exit_px > 0:
                            entry_bd = calculate_charges_breakdown(entry_px, contracts, is_buy=True, index=idx)
                            exit_bd = calculate_charges_breakdown(exit_px, contracts, is_buy=False, index=idx)
                            
                            brokerage = round(entry_bd["brokerage"] + exit_bd["brokerage"], 2)
                            gst = round(entry_bd["gst"] + exit_bd["gst"], 2)
                            stt = round(entry_bd["stt"] + exit_bd["stt"], 2)
                            stamp_duty = round(entry_bd["stamp_duty"] + exit_bd["stamp_duty"], 2)
                            exchange_charges = round(entry_bd["exchange_charges"] + exit_bd["exchange_charges"], 2)
                            sebi_fee = round(entry_bd["sebi_fee"] + exit_bd["sebi_fee"], 2)
                            total_charges = round(entry_bd["total"] + exit_bd["total"], 2)
                            
                        trades.append({
                            "tid": tid,
                            "date": row.get("Date", ""),
                            "entry_time": row.get("EntryTime", ""),
                            "exit_time": row.get("ExitTime", ""),
                            "direction": row.get("Direction", ""),
                            "strike": row.get("Strike", ""),
                            "opt": row.get("OptType", ""),
                            "entry": entry_px,
                            "exit": exit_px,
                            "sl": float(row.get("SL", 0) or 0),
                            "tp": float(row.get("TP", 0) or 0),
                            "pnl": float(row.get("PnL_Rs", 0) or 0),
                            "pnl_pct": float(row.get("PnL_Pct", 0) or 0),
                            "reason": row.get("ExitReason", ""),
                            "charges": total_charges,
                            "brokerage": brokerage,
                            "gst": gst,
                            "stt": stt,
                            "stamp_duty": stamp_duty,
                            "exchange_charges": exchange_charges,
                            "sebi_fee": sebi_fee,
                        })
                    except:
                        pass
    except:
        pass
    return trades

def _read_log_data(username: str, n=60):
    """Read trading log file and filter by username or global system events."""
    for fname in ["nifty_trader.log", "demo.log"]:
        p = f"{LOG_DIR}/{fname}"
        if os.path.exists(p):
            try:
                filtered_lines = []
                with open(p, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line_str = line.rstrip()
                        if f"[{username}]" in line_str or any(tag in line_str for tag in ("[MAIN]", "[LTP]", "[VIX]", "[SYSTEM]", "[GUARD]", "[SMART GUARD]")) or "MULTI-INDEX" in line_str:
                            filtered_lines.append(line_str)
                return filtered_lines[-n:]
            except:
                pass
    return []

def check_and_clear_expired_credentials():
    """
    Check if saved credentials (Dhan / Groww) in .env are older than 24 hours.
    If so, delete them to enforce daily re-authentication security.
    """
    try:
        now = datetime.now()
        env_changed = False
        env_vars = {}
        
        # Read current .env variables
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line_strip = line.strip()
                    if "=" in line_strip and not line_strip.startswith("#"):
                        k, v = line_strip.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip("'").strip('"')

        # Groww Expiration Check (24 hours)
        groww_ts_str = env_vars.get("GROWW_CREDENTIALS_TIMESTAMP", "")
        if groww_ts_str:
            try:
                save_time = datetime.fromisoformat(groww_ts_str)
                if (now - save_time).total_seconds() >= 86400:
                    log.warning("[SECURITY] 🔐 Groww API credentials are older than 24 hours. Expiring and clearing...")
                    cfg.groww_client_id = ""
                    cfg.groww_pin = ""
                    env_vars.pop("GROWW_CLIENT_ID", None)
                    env_vars.pop("GROWW_PIN", None)
                    env_vars.pop("GROWW_CREDENTIALS_TIMESTAMP", None)
                    env_changed = True
            except Exception:
                env_vars.pop("GROWW_CLIENT_ID", None)
                env_vars.pop("GROWW_PIN", None)
                env_vars.pop("GROWW_CREDENTIALS_TIMESTAMP", None)
                env_changed = True
        
        # Dhan Expiration Check (24 hours)
        dhan_ts_str = env_vars.get("DHAN_CREDENTIALS_TIMESTAMP", "")
        if dhan_ts_str:
            try:
                save_time = datetime.fromisoformat(dhan_ts_str)
                if (now - save_time).total_seconds() >= 86400:
                    log.warning("[SECURITY] 🔐 Dhan API credentials are older than 24 hours. Expiring and clearing...")
                    cfg.client_id = ""
                    cfg.access_token = ""
                    env_vars.pop("DHAN_CLIENT_ID", None)
                    env_vars.pop("DHAN_ACCESS_TOKEN", None)
                    env_vars.pop("DHAN_CREDENTIALS_TIMESTAMP", None)
                    env_changed = True
            except Exception:
                env_vars.pop("DHAN_CLIENT_ID", None)
                env_vars.pop("DHAN_ACCESS_TOKEN", None)
                env_vars.pop("DHAN_CREDENTIALS_TIMESTAMP", None)
                env_changed = True
                
        if env_changed:
            new_lines = []
            for k, v in env_vars.items():
                new_lines.append(f"{k}={v}\n")
            with open(".env", "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            log.info("[SECURITY] 🔐 Expired credentials cleared from .env.")
    except Exception as e:
        log.error(f"[SECURITY] Error checking expired credentials: {e}")

def save_trading_indices_to_env(indices_list):
    """Save the active trading indices to .env so they persist across runs."""
    try:
        env_vars = {}
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line_strip = line.strip()
                    if "=" in line_strip and not line_strip.startswith("#"):
                        k, v = line_strip.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip("'").strip('"')

        env_vars["TRADING_INDICES"] = ",".join(indices_list)

        new_lines = []
        for k, v in env_vars.items():
            new_lines.append(f"{k}={v}\n")
        with open(".env", "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        log.info(f"[CONFIG] ⚙️ Saved active trading indices to .env: {','.join(indices_list)}")
    except Exception as e:
        log.error(f"[CONFIG] Error saving indices to .env: {e}")

def _get_groww_balance_details(client = None) -> Optional[dict]:
    """Fetch detailed Groww balance breakdown for dashboard display."""
    try:
        from groww_client import groww
        groww_inst = client or groww
        caps = groww_inst.get_broker_capital()
        if caps.get("available", 0.0) > 0:
            return {
                "available": round(caps.get("available", 0.0), 2),
                "base": round(caps.get("base", 0.0), 2),
                "clear_cash": round(caps.get("clear_cash", 0.0), 2),
                "fno_available": round(caps.get("fno_available", 0.0), 2),
                "equity_available": round(caps.get("equity_available", 0.0), 2),
                "collateral": round(caps.get("collateral", 0.0), 2),
                "adhoc": round(caps.get("adhoc", 0.0), 2),
            }
    except Exception as e:
        log.warning(f"[GROWW] Error getting balance details: {e}")
    return None

from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" in session:
            return f(*args, **kwargs)
        # Fallback for API and mobile clients using username query param or header
        u_header = request.headers.get("X-Username") or request.args.get("username")
        if u_header:
            uid = db_helper.get_user_id_by_username(u_header)
            if uid != -1:
                session["user_id"] = uid
                return f(*args, **kwargs)
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    return decorated_function

@_dashboard_app.route("/api/register", methods=["POST"])
def api_register():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password are required"}), 400
    if len(username) < 3 or len(password) < 4:
        return jsonify({"status": "error", "message": "Invalid username (min 3 chars) or password (min 4 chars)"}), 400
    
    success = db_helper.register_user(username, password)
    if success:
        return jsonify({"status": "success", "message": "User registered successfully!"})
    else:
        return jsonify({"status": "error", "message": "Username already exists"}), 400

@_dashboard_app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password are required"}), 400
    
    user_id = db_helper.verify_user(username, password)
    if user_id != -1:
        session["user_id"] = user_id
        session.permanent = True
        log.info(f"[AUTH] User '{username}' (ID: {user_id}) logged in successfully.")
        return jsonify({"status": "success", "message": "Logged in successfully", "user_id": user_id})
    else:
        return jsonify({"status": "error", "message": "Invalid username or password"}), 401

@_dashboard_app.route("/api/logout", methods=["POST", "GET"])
def api_logout():
    user_id = session.pop("user_id", None)
    if user_id:
        log.info(f"[AUTH] User ID {user_id} logged out.")
    return jsonify({"status": "success", "message": "Logged out successfully"})

@_dashboard_app.route("/api/auth/status")
def api_auth_status():
    user_id = session.get("user_id")
    if user_id:
        cfg_data = db_helper.get_user_config(user_id)
        if cfg_data:
            return jsonify({
                "authenticated": True,
                "user_id": user_id,
                "username": cfg_data.get("username", f"User_{user_id}")
            })
    return jsonify({"authenticated": False})

@_dashboard_app.route("/api/vix/refresh", methods=["POST", "GET"])
def api_vix_refresh():
    """Force immediate VIX refresh in background, return latest cached value."""
    threading.Thread(target=_refresh_vix_cache, daemon=True).start()
    return jsonify({"status": "refreshing", "vix": _get_cached_vix()})

@_dashboard_app.route("/api/data")
@login_required
def api_data():
    user_id = session["user_id"]
    u_config = db_helper.get_user_config(user_id)
    if not u_config:
        return jsonify({"status": "error", "message": "Configuration not found"}), 404
        
    username = u_config.get("username", f"User_{user_id}")
    live_trading = bool(u_config.get("live_trading", 0))
    active_broker = u_config.get("active_broker", "GROWW")
    trading_active = bool(u_config.get("trading_active", 1))
    trading_indices = [x.strip().upper() for x in u_config.get("trading_indices", "NIFTY,SENSEX").split(",") if x.strip()]
    
    book = get_user_book(user_id)
    current_vix = _get_cached_vix()
    
    if live_trading and book is not None:
        try:
            book.sync_live_positions()
        except Exception as e:
            log.error(f"[DASHBOARD] Error syncing positions for API request: {e}")
            
    trades = db_helper.load_user_trade_history(user_id, is_live=live_trading)
    lines = _read_log_data(username, 80)
    
    running_capital = u_config.get("capital", 200000.0)
    
    if not live_trading:
        realized_pnl = sum(t.get("pnl", 0.0) for t in trades)
        realized_capital = running_capital
        base = realized_capital - realized_pnl
    else:
        realized_pnl = book.cum_pnl
        realized_capital = book.total_capital
        base = realized_capital - realized_pnl

    open_positions = []
    unrealized_pnl = 0.0
    for idx in list(book.open.keys()):
        for tid, p in book.open[idx].items():
                real_ltp = 0.0
                if live_trading:
                    real_ltp = _get_live_position_ltp(p, client=book.groww_client if p.broker == "GROWW" else book.dhan_client, live_trading=live_trading)
                if real_ltp <= 0:
                    real_ltp = _get_nse_ltp(p.strike, p.opt, idx)
                if real_ltp > 0:
                    p.cur = real_ltp
                    p.peak = max(p.peak, real_ltp)
                    
                entry_chg = getattr(p, "entry_charges", 0.0)
                exit_bd_est = calculate_charges_breakdown(p.cur, p.contracts, is_buy=False, index=p.index)
                total_chg_est = round(entry_chg + exit_bd_est["total"], 2)
                
                brokerage = round(getattr(p, "brokerage", 0.0) + exit_bd_est["brokerage"], 2)
                gst = round(getattr(p, "gst", 0.0) + exit_bd_est["gst"], 2)
                stt = round(getattr(p, "stt", 0.0) + exit_bd_est["stt"], 2)
                stamp_duty = round(getattr(p, "stamp_duty", 0.0) + exit_bd_est["stamp_duty"], 2)
                exchange_charges = round(getattr(p, "exchange_charges", 0.0) + exit_bd_est["exchange_charges"], 2)
                sebi_fee = round(getattr(p, "sebi_fee", 0.0) + exit_bd_est["sebi_fee"], 2)
                
                gross_pnl = (p.cur - p.entry) * p.contracts
                net_pnl = round(gross_pnl - total_chg_est, 2)
                pnl_pct = round((net_pnl / p.cost * 100) if p.cost else 0, 2)
                
                contract_sym = getattr(p, 'dhan_sec_id', '') or ''
                if not contract_sym:
                    contract_sym = f"{p.index} {int(p.strike)} {p.opt}"
                    
                open_positions.append({
                    "tid": p.tid,
                    "index": p.index,
                    "direction": p.direction,
                    "strike": p.strike,
                    "opt": p.opt,
                    "expiry": p.expiry,
                    "lots": p.lots,
                    "contracts": p.contracts,
                    "entry": p.entry,
                    "sl": p.sl,
                    "tp": p.tp,
                    "cur": p.cur,
                    "pnl": net_pnl,
                    "pnl_pct": pnl_pct,
                    "charges": total_chg_est,
                    "brokerage": brokerage,
                    "gst": gst,
                    "stt": stt,
                    "stamp_duty": stamp_duty,
                    "exchange_charges": exchange_charges,
                    "sebi_fee": sebi_fee,
                    "entry_time": p.entry_time.strftime("%H:%M:%S") if p.entry_time else "",
                    "trailing_sl_enabled": p.trailing_sl_enabled,
                    "contract_sym": contract_sym,
                    "broker": getattr(p, 'broker', active_broker),
                })
                unrealized_pnl += net_pnl

    total_pnl = realized_pnl + unrealized_pnl
    current_capital = realized_capital + unrealized_pnl

    if live_trading:
        if active_broker == "DHAN" and book.dhan_client:
            try:
                broker_cap = book.dhan_client.get_broker_capital()
                if broker_cap["available"] > 0:
                    available_bal = broker_cap["available"]
                    open_val = 0.0
                    for idx in list(book.open.keys()):
                        for tid, p in book.open[idx].items():
                            if p.broker == "DHAN":
                                open_val += p.cur * p.contracts
                    current_capital = available_bal + open_val
                    base = broker_cap["base"] if broker_cap["base"] > 0 else current_capital
                    realized_capital = current_capital - unrealized_pnl
                    realized_pnl = realized_capital - base
                    total_pnl = current_capital - base
            except Exception as e:
                log.error(f"[DASHBOARD] Failed to fetch live Dhan capital: {e}")
        elif active_broker == "GROWW" and book.groww_client:
            try:
                groww_cap = book.groww_client.get_broker_capital()
                available_bal = groww_cap.get("available", 0.0)
                open_val = 0.0
                for idx in list(book.open.keys()):
                    for tid, p in book.open[idx].items():
                        if p.broker == "GROWW":
                            open_val += p.cur * p.contracts
                current_capital = available_bal + open_val
                base = groww_cap["base"] if groww_cap.get("base", 0.0) > 0 else current_capital
            except Exception as e:
                log.error(f"[DASHBOARD] Failed to fetch Groww capital: {e}")
                current_capital = 0.0
                base = 0.0
            realized_capital = current_capital - unrealized_pnl
            realized_pnl = realized_capital - base
            total_pnl = current_capital - base

    realized_charges = sum(t.get("charges", 0.0) for t in trades)
    unrealized_charges = sum(p.get("charges", 0.0) for p in open_positions)
    total_charges = realized_charges + unrealized_charges

    realized_brokerage = sum(t.get("brokerage", 0.0) for t in trades)
    realized_gst = sum(t.get("gst", 0.0) for t in trades)
    realized_stt = sum(t.get("stt", 0.0) for t in trades)
    realized_stamp_duty = sum(t.get("stamp_duty", 0.0) for t in trades)
    realized_exchange_charges = sum(t.get("exchange_charges", 0.0) for t in trades)
    realized_sebi_fee = sum(t.get("sebi_fee", 0.0) for t in trades)
    
    unrealized_brokerage = sum(p.get("brokerage", 0.0) for p in open_positions)
    unrealized_gst = sum(p.get("gst", 0.0) for p in open_positions)
    unrealized_stt = sum(p.get("stt", 0.0) for p in open_positions)
    unrealized_stamp_duty = sum(p.get("stamp_duty", 0.0) for p in open_positions)
    unrealized_exchange_charges = sum(p.get("exchange_charges", 0.0) for p in open_positions)
    unrealized_sebi_fee = sum(p.get("sebi_fee", 0.0) for p in open_positions)
    
    total_brokerage = round(realized_brokerage + unrealized_brokerage, 2)
    total_gst = round(realized_gst + unrealized_gst, 2)
    total_stt = round(realized_stt + unrealized_stt, 2)
    total_stamp_duty = round(realized_stamp_duty + unrealized_stamp_duty, 2)
    total_exchange_charges = round(realized_exchange_charges + unrealized_exchange_charges, 2)
    total_sebi_fee = round(realized_sebi_fee + unrealized_sebi_fee, 2)

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]
    n = len(pnls)
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(loss) / len(loss) if loss else 0

    cum = 0
    equity = []
    for t in trades:
        cum += t["pnl"]
        equity.append(round(cum, 2))

    return jsonify({
        "trades": trades,
        "open_positions": open_positions,
        "equity": equity,
        "log": lines[-30:],
        "stats": {
            "capital": round(current_capital, 2),
            "base_capital": round(base, 2),
            "total_pnl": round(total_pnl, 2),
            "total_trades": n,
            "wins": len(wins),
            "losses": len(loss),
            "win_rate": round(len(wins) / n * 100, 1) if n else 0,
            "avg_win": round(aw, 2),
            "avg_loss": round(al, 2),
            "rr": round(abs(aw / al), 2) if al else 0,
            "best": round(max(pnls), 2) if pnls else 0,
            "worst": round(min(pnls), 2) if pnls else 0,
            "total_charges": round(total_charges, 2),
            "total_brokerage": total_brokerage,
            "total_gst": total_gst,
            "total_stt": total_stt,
            "total_stamp_duty": total_stamp_duty,
            "total_exchange_charges": total_exchange_charges,
            "total_sebi_fee": total_sebi_fee,
        },
        "running": trading_active,
        "live_trading": live_trading,
        "active_broker": active_broker,
        "smart_status": smart_filter.get_status(),
        "trading_indices": trading_indices,
        "nifty_spot": _get_nse_spot("NIFTY"),
        "sensex_spot": _get_nse_spot("SENSEX"),
        "vix": current_vix,
        "dhan_credentials": {
            "client_id": u_config.get("dhan_client_id", ""),
            "has_token": bool(u_config.get("dhan_access_token"))
        },
        "groww_credentials": {
            "client_id": u_config.get("groww_client_id", ""),
            "has_token": bool(u_config.get("groww_pin"))
        },
        "groww_balance": _get_groww_balance_details(client=book.groww_client) if (live_trading and active_broker == "GROWW") else None,
        "trailing_sl_enabled": bool(u_config.get("trailing_sl_enabled", 1)),
        "smart_filter_enabled": bool(u_config.get("smart_filter_enabled", 1)),
    })

@_dashboard_app.route("/api/smart_filter/toggle", methods=["POST"])
@login_required
def api_smart_filter_toggle():
    user_id = session["user_id"]
    data = request.json or {}
    enabled = bool(data.get("enabled", True))
    db_helper.update_user_config(user_id, {"smart_filter_enabled": 1 if enabled else 0})
    
    book = get_user_book(user_id)
    book.smart_filter_enabled = enabled
        
    if enabled:
        try:
            smart_filter.train_model()
        except Exception as e:
            log.warning(f"[Guard] Error training model: {e}")
            
    log.info(f"[Guard] 🛡️ [{book.username}] Dynamic toggle: Smart Trade Guard is now {'ENABLED' if enabled else 'DISABLED'}")
    return jsonify({"status": "success", "enabled": enabled, "message": f"Smart Trade Guard is now {'enabled' if enabled else 'disabled'}."})

@_dashboard_app.route("/api/trailing_sl/toggle", methods=["POST"])
@login_required
def api_trailing_sl_toggle():
    user_id = session["user_id"]
    data = request.json or {}
    enabled = bool(data.get("enabled", True))
    db_helper.update_user_config(user_id, {"trailing_sl_enabled": 1 if enabled else 0})
    
    book = get_user_book(user_id)
    book.trailing_sl_enabled = enabled
        
    log.info(f"[RISK] 🛡️ [{book.username}] Dynamic toggle: Trailing Stop Loss is now {'ENABLED' if enabled else 'DISABLED'}")
    return jsonify({"status": "success", "enabled": enabled, "message": f"Trailing Stop Loss is now {'enabled' if enabled else 'disabled'}."})

@_dashboard_app.route("/api/broker", methods=["POST"])
@login_required
def api_broker():
    user_id = session["user_id"]
    data = request.json or {}
    target_broker = data.get("broker", "GROWW").upper()
    if target_broker in ("GROWW", "DHAN"):
        db_helper.update_user_config(user_id, {"active_broker": target_broker})
        book = get_user_book(user_id)
        book.active_broker = target_broker
        log.info(f"[MAIN] 🔌 [{book.username}] Switched active broker to {target_broker}")
        return jsonify({"status": "success", "message": f"Successfully switched active broker to {target_broker}."})
    return jsonify({"status": "error", "message": "Unsupported broker. Choose GROWW or DHAN."}), 400

@_dashboard_app.route("/api/broker/credentials", methods=["POST"])
@login_required
def api_broker_credentials():
    user_id = session["user_id"]
    u_config = db_helper.get_user_config(user_id)
    if not u_config:
        return jsonify({"status": "error", "message": "Configuration not found"}), 404
        
    data = request.json or {}
    broker = data.get("broker", "DHAN").upper()
    client_id = data.get("client_id", "").strip()
    access_token = data.get("access_token", "").strip()
    
    if not client_id:
        return jsonify({"status": "error", "message": "Client ID is required."}), 400
        
    if broker == "DHAN":
        if access_token == "REUSE_SAVED_TOKEN":
            access_token = u_config.get("dhan_access_token", "")
            if not access_token:
                return jsonify({"status": "error", "message": "No saved Access Token found. Please enter one."}), 400
        elif not access_token:
            return jsonify({"status": "error", "message": "Access Token is required."}), 400
            
        try:
            from dhan_client import DhanClient
            test_client = DhanClient(client_id=client_id, access_token=access_token)
            funds = test_client.get_broker_capital()
            if funds["available"] == 0.0 and funds["base"] == 0.0:
                log.error(f"[DHAN] [{u_config['username']}] Connected but limits are 0. Verification failed.")
                return jsonify({"status": "error", "message": "Dhan connection failed. Please verify your Client ID and Access Token."}), 400
        except Exception as e:
            log.error(f"[API] [{u_config['username']}] Error testing Dhan client: {e}")
            return jsonify({"status": "error", "message": f"Dhan API connection failed: {e}"}), 400
            
        db_helper.update_user_config(user_id, {
            "dhan_client_id": client_id,
            "dhan_access_token": access_token
        })
        
    else:
        # GROWW
        if access_token == "REUSE_SAVED_TOKEN":
            access_token = u_config.get("groww_pin", "")
            if not access_token:
                return jsonify({"status": "error", "message": "No saved Trading PIN found. Please enter one."}), 400
        elif not access_token:
            return jsonify({"status": "error", "message": "Trading PIN is required."}), 400
            
        try:
            from groww_client import GrowwClientWrapper
            test_client = GrowwClientWrapper(groww_client_id=client_id, groww_pin=access_token)
            funds = test_client.get_broker_capital()
            if not test_client.authenticated:
                log.error(f"[GROWW] [{u_config['username']}] Authentication is inactive. Verification failed.")
                error_msg = getattr(test_client, "auth_error", "Authentication failed. Please check your credentials.")
                return jsonify({"status": "error", "message": f"Groww connection failed: {error_msg}"}), 400
        except Exception as e:
            log.error(f"[API] [{u_config['username']}] Error testing Groww client: {e}")
            return jsonify({"status": "error", "message": f"Groww API connection failed: {e}"}), 400
            
        db_helper.update_user_config(user_id, {
            "groww_client_id": client_id,
            "groww_pin": access_token
        })
        
    if user_id in active_books:
        del active_books[user_id]
    book = get_user_book(user_id)
    
    balance_msg = ""
    if broker == "GROWW" and book.groww_client:
        try:
            caps = book.groww_client.get_broker_capital()
            avail_bal = caps.get("available", 0.0)
            if avail_bal > 0:
                balance_msg = f" | Available Balance: ₹{avail_bal:,.2f}"
        except:
            pass
    elif broker == "DHAN" and book.dhan_client:
        try:
            caps = book.dhan_client.get_broker_capital()
            avail_bal = caps.get("available", 0.0)
            if avail_bal > 0:
                balance_msg = f" | Available Balance: ₹{avail_bal:,.2f}"
        except:
            pass
            
    if book.live_trading:
        try:
            book.sync_live_positions()
        except:
            pass
            
    return jsonify({
        "status": "success", 
        "message": f"{broker} credentials successfully connected and saved!{balance_msg}"
    })

@_dashboard_app.route("/api/capital/update", methods=["POST"])
@login_required
def api_capital_update():
    user_id = session["user_id"]
    data = request.json or {}
    try:
        new_cap = float(data.get("capital", 0.0))
        if new_cap <= 0:
            return jsonify({"status": "error", "message": "Capital must be a positive number."}), 400
            
        db_helper.update_user_config(user_id, {"capital": new_cap})
        book = get_user_book(user_id)
        book.update_total_capital(new_cap)
            
        log.info(f"[API] 💰 [{book.username}] Dynamically updated capital to Rs.{new_cap:,.2f}")
        return jsonify({"status": "success", "message": f"Successfully updated active paper capital to Rs.{new_cap:,.2f}."})
    except Exception as e:
        log.error(f"[API] Exception updating capital: {e}")
        return jsonify({"status": "error", "message": f"Failed to update capital: {str(e)}"}), 500

@_dashboard_app.route("/api/mode", methods=["POST"])
@login_required
def api_mode():
    user_id = session["user_id"]
    data = request.json or {}
    target_mode = data.get("mode", "DEMO")
    live = 1 if target_mode == "LIVE" else 0
    
    db_helper.update_user_config(user_id, {"live_trading": live})
    global active_books
    active_books[user_id] = Book(user_id)
    book = active_books[user_id]
    if live:
        book.sync_live_positions()
        for index in list(book.open.keys()):
            for tid, p in list(book.open[index].items()):
                if not p.dhan_order_id or p.broker == "MOCK" or p.dhan_order_id.startswith("MOCK"):
                    log.info(f"[MODE SWITCH] 🔄 [{book.username}] Carry-over: Punching active demo position {p.tid} ({p.strike} {p.opt}) to live broker...")
                    sec_id = ""
                    if book.active_broker == "DHAN" and book.dhan_client:
                        sec_id = _get_dhan_sec_id(p.index, p.strike, p.opt, p.expiry, client=book.dhan_client)
                    elif book.active_broker == "GROWW" and book.groww_client:
                        sec_id = _get_groww_contract_symbol(p.index, p.strike, p.opt)
                        if not sec_id:
                            sec_id = f"GRW_SEC_{int(p.strike)}_{p.opt}"
                    if not sec_id:
                        log.error(f"[MODE SWITCH] ❌ [{book.username}] Could not resolve security ID for {p.tid}. Skipping punch.")
                        continue
                        
                    live_order_id = ""
                    try:
                        if book.active_broker == "DHAN":
                            live_order_id = place_dhan_order(sec_id, "BUY", p.contracts, p.index, client=book.dhan_client)
                        elif book.active_broker == "GROWW":
                            live_order_id = place_groww_order(sec_id, "BUY", p.contracts, p.index, client=book.groww_client)
                    except Exception as punch_err:
                        log.error(f"[MODE SWITCH] ❌ [{book.username}] Exception punching order for {p.tid}: {punch_err}")
                        
                    if live_order_id:
                        p.dhan_order_id = live_order_id
                        p.dhan_sec_id = sec_id
                        p.broker = book.active_broker
                        db_helper.save_position(user_id, p)
                        log.info(f"[MODE SWITCH] ✅ [{book.username}] Successfully carried position {p.tid} to live broker! OrderID: {live_order_id}")
                    else:
                        p.dhan_order_id = f"REJECTED_{_now().strftime('%H%M%S')}"
                        p.dhan_sec_id = sec_id
                        p.broker = book.active_broker
                        db_helper.save_position(user_id, p)
                        log.warning(f"[MODE SWITCH] ⚠️ [{book.username}] Live order rejected or failed for {p.tid}.")
    
    log.warning(f"[MAIN] ⚠️ [{book.username}] Live Trading Mode {'ENABLED' if live else 'DISABLED'} by user via dashboard")
        
    return jsonify({"status": "success", "message": f"Successfully switched to {target_mode} Trading Mode."})

@_dashboard_app.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    user_id = session["user_id"]
    db_helper.update_user_config(user_id, {"trading_active": 0})
    book = get_user_book(user_id)
    book.trading_active = False
    log.info(f"[MAIN] [{book.username}] Automated trading stopped/paused by user via dashboard.")
    return jsonify({"status": "stopped", "message": "Automated trading loop paused. Existing open positions remain active."})

@_dashboard_app.route("/api/squareoff", methods=["POST"])
@login_required
def api_squareoff():
    user_id = session["user_id"]
    book = get_user_book(user_id)
    book.exit_all("USER_SQUAREOFF")
    log.info(f"[MAIN] [{book.username}] SQUARE-OFF REQUESTED: Closing all active positions via dashboard.")
    return jsonify({"status": "success", "message": "All active positions successfully squared off."})

@_dashboard_app.route("/api/start", methods=["POST"])
@login_required
def api_start():
    user_id = session["user_id"]
    db_helper.update_user_config(user_id, {"trading_active": 1})
    book = get_user_book(user_id)
    book.trading_active = True
    log.info(f"[MAIN] [{book.username}] Automated trading started/resumed by user via dashboard.")
    return jsonify({"status": "started", "message": "Automated trading loop resumed successfully."})

@_dashboard_app.route("/api/reset", methods=["POST"])
@login_required
def api_reset():
    user_id = session["user_id"]
    username = db_helper.get_user_config(user_id).get("username", f"User_{user_id}")
    db_helper.delete_user_trade_history(user_id)
    
    if user_id in active_books:
        del active_books[user_id]
    book = get_user_book(user_id)
    
    try:
        smart_filter.train_model()
    except Exception as ex:
        log.warning(f"[Guard] Error resetting smart filter stats: {ex}")
        
    log.info(f"[MAIN] [{username}] Trade log and statistics reset by user via dashboard")
    return jsonify({"status": "success", "message": "Trade logs, logs, and capital have been fully reset."})

@_dashboard_app.route("/api/trades/download", methods=["GET"])
@login_required
def api_trades_download():
    user_id = session["user_id"]
    u_config = db_helper.get_user_config(user_id)
    live_trading = bool(u_config.get("live_trading", 0))
    trades = db_helper.load_user_trade_history(user_id, is_live=live_trading)
    
    import io
    from flask import Response
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        "TradeID", "Index", "Direction", "Strike", "OptType", "Lots", "Contracts",
        "EntryPrice", "ExitPrice", "EntryTime", "ExitTime", "Charges", "PnL_Rs",
        "ExitReason", "Broker", "Brokerage", "GST", "STT", "StampDuty", "ExchangeCharges", "SEBIFee"
    ])
    
    for t in trades:
        writer.writerow([
            t.get("tid", ""),
            t.get("index_name", ""),
            t.get("direction", ""),
            t.get("strike", ""),
            t.get("opt", ""),
            t.get("lots", ""),
            t.get("contracts", ""),
            t.get("entry", ""),
            t.get("exit_px", ""),
            t.get("entry_time", ""),
            t.get("exit_time", ""),
            t.get("charges", ""),
            t.get("pnl", ""),
            t.get("exit_reason", ""),
            t.get("broker", ""),
            t.get("brokerage", ""),
            t.get("gst", ""),
            t.get("stt", ""),
            t.get("stamp_duty", ""),
            t.get("exchange_charges", ""),
            t.get("sebi_fee", "")
        ])
    
    output.seek(0)
    mode_str = "live" if live_trading else "demo"
    filename = f"trades_{mode_str}_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )

@_dashboard_app.route("/api/indices/update", methods=["POST"])
@login_required
def api_indices_update():
    user_id = session["user_id"]
    data = request.json or {}
    selected = data.get("indices")
    if not isinstance(selected, list):
        return jsonify({"status": "error", "message": "Indices must be a list of strings."}), 400
        
    selected_upper = [str(x).upper() for x in selected if x]
    
    if not selected_upper:
        return jsonify({"status": "error", "message": "At least one index must be selected."}), 400
        
    supported = ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "BANKEX"]
    invalid = [x for x in selected_upper if x not in supported]
    if invalid:
        return jsonify({"status": "error", "message": f"Unsupported indices: {', '.join(invalid)}"}), 400
        
    db_helper.update_user_config(user_id, {"trading_indices": ",".join(selected_upper)})
    
    book = get_user_book(user_id)
    book.trading_indices = selected_upper
    total_config_capital = sum(CAPITAL_PER_INDEX.get(idx, 100000.0) for idx in selected_upper)
    saved_capital = book.total_capital
    
    for idx in selected_upper:
        if idx not in book.open:
            book.open[idx] = {}
        if idx not in book.day_pnl:
            book.day_pnl[idx] = 0.0
        if idx not in book.day_trades:
            book.day_trades[idx] = 0
        
        ratio = CAPITAL_PER_INDEX.get(idx, 100000.0) / total_config_capital
        book.capital[idx] = round(saved_capital * ratio, 2)
        
    book.total_capital = sum(book.capital[idx] for idx in selected_upper)

    log.info(f"[API] ⚙️ [{book.username}] Updated active trading indices: {', '.join(selected_upper)}")
    return jsonify({
        "status": "success", 
        "message": f"Successfully updated active trading indices to: {', '.join(selected_upper)}",
        "indices": selected_upper
    })

@_dashboard_app.route("/api/position/update", methods=["POST"])
@login_required
def api_position_update():
    user_id = session["user_id"]
    data = request.json or {}
    tid = data.get("tid")
    new_sl = data.get("sl")
    new_tp = data.get("tp")
    new_tsl = data.get("tsl")
    
    if not tid:
        return jsonify({"status": "error", "message": "Missing Position ID."}), 400
        
    book = get_user_book(user_id)
    found_pos: Optional["Pos"] = None
    for index in list(book.open.keys()):
        if book is not None and tid in book.open[index]:
            found_pos = book.open[index][tid]
            break
            
    if not found_pos:
        return jsonify({"status": "error", "message": "Active position not found."}), 404
        
    try:
        if new_sl is not None:
            found_pos.sl = round(float(new_sl), 1)
        if new_tp is not None:
            found_pos.tp = round(float(new_tp), 1)
        if new_tsl is not None:
            found_pos.trailing_sl_enabled = bool(new_tsl)
            
        db_helper.save_position(user_id, found_pos)
            
        log.info(f"[API] ✏️ [{book.username}] Manually updated position {tid}: SL={found_pos.sl} | TP={found_pos.tp} | TSL={found_pos.trailing_sl_enabled}")
        return jsonify({
            "status": "success", 
            "message": f"Successfully updated position {tid} | SL: {found_pos.sl} | TP: {found_pos.tp} | TSL: {'ON' if found_pos.trailing_sl_enabled else 'OFF'}",
            "sl": found_pos.sl,
            "tp": found_pos.tp,
            "tsl": found_pos.trailing_sl_enabled
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"Invalid inputs: {e}"}), 400

@_dashboard_app.route("/api/position/squareoff", methods=["POST"])
@login_required
def api_position_squareoff():
    user_id = session["user_id"]
    data = request.json or {}
    tid = data.get("tid")
    if not tid:
        return jsonify({"status": "error", "message": "Position TradeID is required."}), 400
    
    book = get_user_book(user_id)
    for index in list(book.open.keys()):
        if tid in book.open[index]:
            p = book.open[index][tid]
            real_ltp = 0.0
            if book.live_trading:
                real_ltp = _get_live_position_ltp(p, client=book.groww_client if p.broker == "GROWW" else book.dhan_client, live_trading=book.live_trading)
            if real_ltp <= 0:
                real_ltp = _get_nse_ltp(p.strike, p.opt, index)
            ltp = real_ltp if real_ltp > 0 else p.cur
            
            book._close(p, round(max(0.5, ltp - LIMIT_OUT), 1), "USER_SQUAREOFF")
            return jsonify({"status": "success", "message": f"Position {tid} successfully squared off."})
                
    return jsonify({"status": "error", "message": "Position not found."}), 404

def _check_ngrok():
    """Checks if a local Ngrok agent is running and prints the active public URL."""
    try:
        import urllib.request
        import json
        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2) as response:
            data = json.loads(response.read().decode())
            tunnels = data.get("tunnels", [])
            for t in tunnels:
                if t.get("proto") in ("http", "https"):
                    public_url = t.get("public_url")
                    log.info(f"[TUNNEL] 🌐 Active public tunnel detected: {public_url}")
                    return
    except Exception:
        pass

def _start_dashboard():
    """Start Flask dashboard in background thread."""
    global _dashboard_thread
    port = int(os.environ.get("PORT", 8000))
    
    # Check if port is already in use
    import socket
    port_in_use = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                port_in_use = True
    except Exception:
        pass
        
    if port_in_use:
        log.error("=" * 80)
        log.error(f"  [DASHBOARD] 🛑 PORT {port} IS ALREADY IN USE!")
        log.error("  Another instance of the trading server is already running.")
        log.error("  Please close all other Python trading sessions before starting a new one.")
        log.error("=" * 80)
        # Flush log handlers and exit immediately to prevent duplicate runs
        for h in log.handlers:
            try: h.flush()
            except: pass
        os._exit(1)

    try:
        if _dashboard_thread is None or not _dashboard_thread.is_alive():
            _dashboard_thread = threading.Thread(
                target=lambda: _dashboard_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
                daemon=True
            )
            _dashboard_thread.start()
            log.info(f"[DASHBOARD] Started on http://localhost:{port}")
            time.sleep(1)
            # Check for active local ngrok tunnel
            threading.Thread(target=_check_ngrok, daemon=True).start()
            # Kick off initial VIX background fetch immediately
            threading.Thread(target=_refresh_vix_cache, daemon=True).start()
            log.info("[VIX] Initial background VIX fetch started")
    except Exception as e:
        log.error(f"[DASHBOARD] Error: {e}")

def _stop_dashboard():
    """Stop dashboard (thread will die with app)."""
    log.info("[DASHBOARD] Stopping")
    # Flask thread is daemon, so it will stop with main app

def _stop(s, f):
    global _running
    _running = False
    _stop_dashboard()
signal.signal(signal.SIGINT,  _stop)
signal.signal(signal.SIGTERM, _stop)

# ─── TIME HELPERS ─────────────────────────────────────────────────────────────
def _now() -> datetime:       return datetime.now(IST)
def _hm(s: str) -> int:       h, m = map(int, s.split(":")); return h * 60 + m
def _now_hm() -> int:         n = _now(); return n.hour * 60 + n.minute
def _is_weekend() -> bool:    return _now().weekday() >= 5
def _is_pre() -> bool:        return not _is_weekend() and _now_hm() < _hm(MARKET_OPEN)
def _is_post() -> bool:       return not _is_weekend() and _now_hm() > _hm(MARKET_CLOSE)
def _in_session() -> bool:    return not _is_weekend() and _hm(MARKET_OPEN) <= _now_hm() <= _hm(MARKET_CLOSE)
def _is_noisy() -> bool:
    """Skip first 15 mins and last 60 mins."""
    hm = _now_hm()
    return hm < _hm(MARKET_OPEN) + 15 or hm > _hm(MARKET_CLOSE) - 60

def _sleep(s: int | float):
    # sleep in small 0.2 second increments to be highly responsive to stop signal
    end_time = time.time() + s
    while time.time() < end_time and _running:
        time.sleep(0.2)

def _expiry(index: str = "NIFTY") -> str:
    """Get dynamic index-aware expiry date string (YYYY-MM-DD) from ExpiryManager."""
    try:
        return ExpiryManager(index).get_expiry()
    except Exception as e:
        log.error(f"[EXPIRY] Error getting expiry for {index}: {e}")
        today = _now().date()
        days  = (1 - today.weekday()) % 7
        return (today + timedelta(days=days if days > 0 else 7)).strftime("%Y-%m-%d")

def _dte(index: str = "NIFTY") -> int:
    """Get DTE (Days to Expiry) for selected index-aware expiry."""
    try:
        today = _now().date()
        exp_str = ExpiryManager(index).get_expiry()
        exp_date = date.fromisoformat(exp_str)
        return max(1, (exp_date - today).days)
    except Exception as e:
        log.error(f"[EXPIRY] Error calculating DTE for {index}: {e}")
        return max(1, (date.fromisoformat(_expiry(index)) - _now().date()).days)

# ─── INDICATORS ───────────────────────────────────────────────────────────────
from typing import Any

def _ema(s: Any, n: int) -> Any:
    return s.ewm(span=n, adjust=False).mean()

def _rsi(s: Any, n: int = 14) -> Any:
    d = s.diff()
    g = d.clip(lower=0).ewm(com=n-1, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(com=n-1, min_periods=n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _atr(df: pd.DataFrame, n: int = 14) -> Any:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - pc).abs(),
                    (df["low"]  - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(com=n-1, min_periods=n).mean()

# ─── GROWW REAL PRICE FETCHER ───────────────────────────────────────────────────
_chain_cache: Dict[str, dict] = {}  # index → chain data
_chain_ts: Dict[str, datetime] = {}  # index → timestamp
_last_dhan_warn_ts: Dict[str, datetime] = {}  # index → last warn timestamp
CHAIN_TTL = 60

# Maps standard index name to (Spot API path, Option Chain API path)
INDEX_MAP = {
    "NIFTY":     ("exchange/NSE/segment/CASH/NIFTY/latest",  "nifty"),
    "BANKNIFTY": ("exchange/NSE/segment/CASH/BANKNIFTY/latest", "nifty-bank"),
    "FINNIFTY":  ("exchange/NSE/segment/CASH/FINNIFTY/latest",  "nifty-financial-services"),
    "MIDCPNIFTY":("exchange/NSE/segment/CASH/MIDCPNIFTY/latest", "nifty-midcap-select"),
    "SENSEX":    ("exchange/BSE/segment/CASH/SENSEX/latest", "sp-bse-sensex"),
    "BANKEX":    ("exchange/BSE/segment/CASH/BANKEX/latest", "sp-bse-bankex"),
}

GROWW_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

def _fetch_dhan_chain(index: str = "NIFTY") -> Optional[dict]:
    """Dedicated function to fetch underlying spot price and option chain from Dhan API."""
    try:
        from dhan_client import dhan
        dhan_ids = {
            "NIFTY": "13",
            "BANKNIFTY": "25",
            "FINNIFTY": "27",
            "MIDCPNIFTY": "50",
            "SENSEX": "51",
            "BANKEX": "52"
        }
        underlying_id = dhan_ids.get(index, "13")
        
        # 1. Fetch spot price (BSE indices are under IDX_B, NSE under IDX_I)
        segment = "IDX_B" if index in ("SENSEX", "BANKEX") else "IDX_I"
        ohlc = dhan.get_ohlc(underlying_id, segment)
        val = ohlc.get("close")
        if val is None:
            val = ohlc.get("last_price", 0.0)
        if val is None:
            val = 0.0
        spot = float(val)
        
        if spot <= 0:
            now = datetime.now()
            if index not in _last_dhan_warn_ts or (now - _last_dhan_warn_ts[index]).total_seconds() > 300:
                log.warning(f"[DHAN] Spot price is 0 or negative for {index}. (Dhan API may be rate-limited, unauthenticated, or market is closed).")
                _last_dhan_warn_ts[index] = now
            return None
            
        # 2. Fetch option chain
        expiry = _expiry(index)
        df = dhan.get_option_chain(underlying_id, expiry)
        if df.empty:
            now = datetime.now()
            if index not in _last_dhan_warn_ts or (now - _last_dhan_warn_ts[index]).total_seconds() > 300:
                log.warning(f"[DHAN] Option chain dataframe is empty for {index}")
                _last_dhan_warn_ts[index] = now
            return None
            
        records = []
        strikes = sorted(df["strike"].unique())
        for strike in strikes:
            ce_row = df[(df["strike"] == strike) & (df["option_type"] == "CE")]
            pe_row = df[(df["strike"] == strike) & (df["option_type"] == "PE")]
            ce_ltp = float(ce_row.iloc[0]["ltp"]) if not ce_row.empty else 0.0
            pe_ltp = float(pe_row.iloc[0]["ltp"]) if not pe_row.empty else 0.0
            if ce_ltp > 0 or pe_ltp > 0:
                records.append({
                    "strike": float(strike),
                    "ce_ltp": ce_ltp,
                    "pe_ltp": pe_ltp
                })
        
        if records:
            log.info(f"[DHAN] {index}: spot={spot:.2f} | {len(records)} strikes loaded via Dhan API")
            return {"spot": spot, "records": records}
    except Exception as e:
        now = datetime.now()
        if index not in _last_dhan_warn_ts or (now - _last_dhan_warn_ts[index]).total_seconds() > 300:
            log.warning(f"[DHAN] Error fetching option chain for {index}: {e}")
            _last_dhan_warn_ts[index] = now
    return None

_groww_session = None

def _get_groww_session():
    global _groww_session
    if _groww_session is None:
        import requests
        _groww_session = requests.Session()
        _groww_session.headers.update(GROWW_HEADERS)
    return _groww_session

def _simulate_groww_chain(spot: float, index: str) -> dict:
    import math
    if index in ("SENSEX", "BANKNIFTY", "BANKEX"):
        gap = 100
    elif index in ("NIFTY", "FINNIFTY"):
        gap = 50
    elif index == "MIDCPNIFTY":
        gap = 25
    else:
        gap = 50
    try:
        dte_val = _dte(index)
    except Exception:
        dte_val = 3
    T = max(1, dte_val) / 365.0
    r = 0.065
    try:
        vix = _get_cached_vix()
    except Exception:
        vix = 15.0
    sigma = vix / 100.0
    
    atm = round(spot / gap) * gap
    records = []
    
    for i in range(-15, 16):
        strike = float(atm + i * gap)
        
        # CE LTP (Black-Scholes approximation)
        ce_intrinsic = max(0.0, spot - strike)
        ce_time_value = spot * sigma * math.sqrt(T) * 0.4
        ce_ltp = max(0.5, round(ce_intrinsic + ce_time_value, 1))
        
        # PE LTP (Black-Scholes approximation)
        pe_intrinsic = max(0.0, strike - spot)
        pe_time_value = spot * sigma * math.sqrt(T) * 0.4
        pe_ltp = max(0.5, round(pe_intrinsic + pe_time_value, 1))
        
        records.append({
            "strike": strike,
            "ce_ltp": ce_ltp,
            "pe_ltp": pe_ltp
        })
    return {"spot": spot, "records": records}

# ─── GROWW SDK SYMBOL MAPPING ──────────────────────────────────────────────────
_GROWW_SDK_SYMBOL_MAP = {
    "NIFTY":     ("NSE", "NSE_NIFTY",  "NIFTY"),
    "BANKNIFTY": ("NSE", "NSE_BANKNIFTY", "BANKNIFTY"),
    "FINNIFTY":  ("NSE", "NSE_FINNIFTY",  "FINNIFTY"),
    "MIDCPNIFTY":("NSE", "NSE_MIDCPNIFTY", "MIDCPNIFTY"),
    "SENSEX":    ("BSE", "BSE_SENSEX",  "SENSEX"),
    "BANKEX":    ("BSE", "BSE_BANKEX",  "BANKEX"),
}

def _get_groww_sdk() -> Optional[Any]:
    """Get the authenticated Groww SDK instance if available."""
    try:
        from groww_client import groww
        if groww.authenticated and hasattr(groww._groww, 'get_ltp'):
            return groww._groww
    except Exception:
        pass
    return None

def _fetch_groww_sdk_spot(index: str) -> Optional[float]:
    """Fetch live spot price using the official Groww SDK (get_ltp)."""
    sdk = _get_groww_sdk()
    if sdk is None:
        return None
    
    mapping = _GROWW_SDK_SYMBOL_MAP.get(index)
    if not mapping:
        return None
    
    exchange, exchange_symbol, _ = mapping
    try:
        result = sdk.get_ltp(
            exchange_trading_symbols=(exchange_symbol,),
            segment='CASH'
        )
        if result and isinstance(result, dict):
            spot = float(result.get(exchange_symbol, 0))
            if spot > 0:
                return spot
    except Exception as e:
        log.debug(f"[GROWW-SDK] LTP error for {index}: {e}")
    return None

# Per-contract LTP lookup cache (avoid hammering the API on every poll)
_contract_ltp_cache: Dict[str, tuple] = {}  # contract_id -> (ltp, timestamp)
_CONTRACT_LTP_TTL = 2  # seconds

def _get_groww_contract_ltp(contract_id: str) -> float:
    """
    Fetch real-time LTP for a specific Groww contract ID directly from
    the Groww public contract quote/search API — no authentication required.
    
    This is the most accurate source because it fetches exactly the contract
    that was traded (matching what Groww broker shows), rather than looking
    up by strike in the general option chain (which may match the wrong expiry).
    """
    if not contract_id:
        return 0.0
    
    # Cache check
    cached = _contract_ltp_cache.get(contract_id)
    if cached:
        ltp_val, ts = cached
        if (time.time() - ts) < _CONTRACT_LTP_TTL:
            return ltp_val
    
    try:
        s = _get_groww_session()
        # Resolve exchange from the contract ID (SENSEX/BANKEX are BSE, others NSE)
        exchange = "BSE" if any(contract_id.startswith(idx) for idx in ("SENSEX", "BANKEX")) else "NSE"
        
        # New verified F&O live prices endpoint
        url = f"https://groww.in/v1/api/stocks_fo_data/v1/tr_live_prices/exchange/{exchange}/segment/FNO/{contract_id}/latest"
        r = s.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            ltp = float(data.get("ltp", 0.0) or 0.0)
            if ltp > 0:
                _contract_ltp_cache[contract_id] = (ltp, time.time())
                log.debug(f"[GROWW-CONTRACT] Direct LTP for {contract_id}: Rs.{ltp} via FNO live prices API")
                return ltp
    except Exception as e:
        log.debug(f"[GROWW-CONTRACT] Error fetching LTP for {contract_id}: {e}")
    
    return 0.0


def _fetch_groww_chain(index: str = "NIFTY") -> Optional[dict]:
    """Fetch underlying spot price and option chain. Uses SDK for spot in live mode, JSON API for chain."""
    if index not in INDEX_MAP:
        log.warning(f"[GROWW] Unsupported index: {index}")
        return None
        
    spot_path, chain_path = INDEX_MAP[index]
    
    # ─── Expiry Day Rollover ───
    # On the expiry day of this index, the unauthenticated Groww JSON API only returns today's expiring options (which are melting rapidly).
    # To avoid this theta decay, we roll over to the next week's expiry by transparently simulating the option chain.
    em = ExpiryManager(index)
    if em.is_expiry_day():
        next_exp_str = em.get_expiry()
        try:
            spot = None
            if _live_trading and _active_broker == "GROWW":
                spot = _fetch_groww_sdk_spot(index)
            if spot is None:
                s = _get_groww_session()
                spot_url = f"https://groww.in/v1/api/stocks_data/v1/tr_live_indices/{spot_path}"
                r_spot = s.get(spot_url, timeout=10)
                r_spot.raise_for_status()
                spot = float(r_spot.json().get("value", 0))
            
            if spot > 0:
                sim_chain = _simulate_groww_chain(spot, index)
                log.info(f"[GROWW] Rollover: Today is {index} expiry ({em._current_expiry(_now().date())}). "
                         f"Theta is melting, so rolling over to next expiry {next_exp_str}. "
                         f"Loaded simulated option chain at spot={spot:.2f}")
                return sim_chain
        except Exception as ex:
            log.warning(f"[GROWW] Rollover simulation failed for {index}: {ex}. Falling back to default chain.")

    try:
        # 1. Fetch Spot Price — use SDK when live GROWW, else JSON API
        spot = None
        data_source = "JSON API"
        if _live_trading and _active_broker == "GROWW":
            spot = _fetch_groww_sdk_spot(index)
            if spot:
                data_source = "Groww SDK"
        
        if spot is None:
            s = _get_groww_session()
            spot_url = f"https://groww.in/v1/api/stocks_data/v1/tr_live_indices/{spot_path}"
            r_spot = s.get(spot_url, timeout=10)
            r_spot.raise_for_status()
            spot = float(r_spot.json().get("value", 0))

        # 2. Fetch Option Chain (always JSON API — SDK contracts return 403)
        s = _get_groww_session()
        chain_url = f"https://groww.in/v1/api/option_chain_service/v1/option_chain/{chain_path}"
        r_chain = s.get(chain_url, timeout=15)
        r_chain.raise_for_status()
        cdata = r_chain.json().get("optionChain", {})
        
        rows = cdata.get("optionChains", [])
        records = []
        for row in rows:
            strike = row.get("strikePrice", 0) / 100.0  # Groww returns strike * 100
            ce = row.get("callOption", {})
            pe = row.get("putOption", {})
            
            # Sanity: valid strike range
            if index == "SENSEX" and not (50000 < strike < 120000): continue
            if index == "NIFTY" and not (10000 < strike < 40000): continue
            if index == "BANKNIFTY" and not (20000 < strike < 70000): continue
            if index == "FINNIFTY" and not (10000 < strike < 35000): continue
            if index == "MIDCPNIFTY" and not (5000 < strike < 25000): continue
            if index == "BANKEX" and not (30000 < strike < 85000): continue

            ce_ltp = float(ce.get("ltp", 0))
            pe_ltp = float(pe.get("ltp", 0))

            if ce_ltp > 0 or pe_ltp > 0:
                records.append({
                    "strike":  strike,
                    "ce_ltp":  ce_ltp,
                    "pe_ltp":  pe_ltp,
                    "ce_symbol": ce.get("growwContractId"),
                    "pe_symbol": pe.get("growwContractId"),
                    "ce_token": ce.get("token"),
                    "pe_token": pe.get("token"),
                })

        records.sort(key=lambda x: x["strike"])
        log.info(f"[GROWW] {index}: spot={spot:.2f} | {len(records)} strikes loaded via {data_source}")
        return {"spot": spot, "records": records}
    except Exception as e:
        log.warning(f"[GROWW] {index} chain fetch error: {e}. Falling back transparently to Yahoo Finance spot + simulated chain.")
        try:
            df = _fetch_ohlcv("5m", index)
            if df is not None and not df.empty:
                spot = float(df["close"].iloc[-1])
                if spot > 0:
                    sim_chain = _simulate_groww_chain(spot, index)
                    log.info(f"[GROWW] Transparently loaded simulated option chain for {index} at spot={spot:.2f}")
                    return sim_chain
        except Exception as ex:
            log.error(f"[GROWW] Simulated fallback failed for {index}: {ex}")
    return None

def _get_groww_contract_symbol(index: str, strike: float, opt: str) -> Optional[str]:
    """Resolve Groww Contract ID (trading symbol) from cached option chain."""
    chain = _fetch_nse_chain(index)
    if not chain:
        return None
        
    if index in ("SENSEX", "BANKNIFTY", "BANKEX"):
        gap = 100
    elif index in ("NIFTY", "FINNIFTY"):
        gap = 50
    elif index == "MIDCPNIFTY":
        gap = 25
    else:
        gap = 50
        
    for row in chain.get("records", []):
        if abs(row["strike"] - strike) < gap * 0.6:
            symbol = row.get("ce_symbol") if opt == "CE" else row.get("pe_symbol")
            if symbol:
                return symbol
    return None

def _fetch_nse_chain(index: str = "NIFTY") -> Optional[dict]:
    """
    Orchestrate fetching option chain from the active broker (Dhan or Groww) with dynamic TTL cache.
    Routes to dedicated helper functions and handles robust fallbacks.
    """
    global _chain_cache, _chain_ts
    now = datetime.now()

    # Dynamic TTL: 2 seconds if any active user has open positions, else 30 seconds
    ttl = 30
    try:
        for uid, book in list(active_books.items()):
            any_open = any(len(book.open[idx]) > 0 for idx in book.trading_indices if idx in book.open)
            if any_open:
                ttl = 2
                break
    except Exception:
        pass

    if (index in _chain_cache and index in _chain_ts and
            (now - _chain_ts[index]).total_seconds() < ttl):
        return _chain_cache.get(index)

    if index not in INDEX_MAP:
        log.warning(f"[ORCHESTRATOR] Unsupported index: {index}")
        return _chain_cache.get(index)

    res = None
    if _live_trading:
        # LIVE Trading Mode: Fetch data from the active live broker
        if _active_broker == "DHAN":
            res = _fetch_dhan_chain(index)
            if res is None:
                now = datetime.now()
                if index not in _last_dhan_warn_ts or (now - _last_dhan_warn_ts[index]).total_seconds() > 300:
                    log.warning(f"[DHAN] Failed to fetch. Falling back transparently to Groww JSON API...")
                    _last_dhan_warn_ts[index] = now
                res = _fetch_groww_chain(index)
        else:
            res = _fetch_groww_chain(index)
    else:
        # DEMO / Paper Trading Mode: Always fetch using free, public unauthenticated Groww JSON API
        res = _fetch_groww_chain(index)

    if res:
        _chain_cache[index] = res
        _chain_ts[index] = now
        return res
    
    return _chain_cache.get(index)

def _get_nse_ltp(strike: float, opt: str, index: str = "NIFTY") -> float:
    """Get real Groww LTP for a specific strike and index."""
    chain = _fetch_nse_chain(index)
    if not chain:
        return 0.0
    # Try exact match first, then nearest strike within gap
    if index in ("SENSEX", "BANKNIFTY", "BANKEX"):
        gap = 100
    elif index in ("NIFTY", "FINNIFTY"):
        gap = 50
    elif index == "MIDCPNIFTY":
        gap = 25
    else:
        gap = 50
        
    for row in chain.get("records", []):
        if abs(row["strike"] - strike) < gap * 0.6:
            ltp = row["ce_ltp"] if opt == "CE" else row["pe_ltp"]
            if ltp > 0:
                return ltp
    log.warning(f"[NSE-LTP] Could not find dynamic LTP for {index} strike {strike} {opt} in option chain.")
    return 0.0

def _get_nse_spot(index: str = "NIFTY") -> float:
    """Get Groww spot price for specified index."""
    chain = _fetch_nse_chain(index)
    return chain.get("spot", 0.0) if chain else 0.0

def _get_dhan_sec_id(index: str, strike: float, opt: str, expiry: str, client = None) -> Optional[str]:
    """Resolve Option Contract Security ID from Dhan option chain."""
    try:
        from dhan_client import dhan
        dhan_inst = client or dhan
        dhan_ids = {
            "NIFTY": "13",
            "BANKNIFTY": "25",
            "FINNIFTY": "27",
            "MIDCPNIFTY": "50",
            "SENSEX": "51",
            "BANKEX": "52"
        }
        underlying_id = dhan_ids.get(index, "13")
        
        log.info(f"[DHAN] Querying option chain for {index} expiry {expiry} | strike {strike} {opt}...")
        df = dhan_inst.get_option_chain(underlying_id, expiry)
        if df.empty:
            log.warning(f"[DHAN] Option chain for {index} expiry {expiry} returned empty.")
            return None
            
        if index in ("SENSEX", "BANKNIFTY", "BANKEX"):
            gap = 100
        elif index in ("NIFTY", "FINNIFTY"):
            gap = 50
        elif index == "MIDCPNIFTY":
            gap = 25
        else:
            gap = 50
            
        filtered = df[(df["option_type"] == opt) & (abs(df["strike"] - strike) < gap * 0.6)]
        if not filtered.empty:
            sec_id = filtered.iloc[0]["security_id"]
            log.info(f"[DHAN] Resolved {index} {strike} {opt} expiry {expiry} -> sec_id={sec_id}")
            return str(sec_id)
        else:
            log.warning(f"[DHAN] No matching contract found in option chain for {index} {strike} {opt}")
    except Exception as e:
        log.error(f"[DHAN] Error resolving security ID: {e}")
    return None

# ─── YAHOO DATA ───────────────────────────────────────────────────────────────
_yf_cache: Dict[Tuple[str, str], pd.DataFrame] = {}  # (index, interval) → data
_yf_ts:    Dict[Tuple[str, str], datetime]     = {}
YF_TTL = {"5m": 50, "15m": 50}

def _fetch_ohlcv(interval: str, index: str = "NIFTY") -> Optional[pd.DataFrame]:
    """Fetch OHLCV data for specified index. Uses Groww SDK in live mode, else Yahoo/JSON."""
    cache_key = (index, interval)
    now = datetime.now()
    if (cache_key in _yf_ts and
            (now - _yf_ts[cache_key]).total_seconds() < YF_TTL.get(interval, 60)):
        return _yf_cache.get(cache_key)

    raw = None
    config = INDEX_CONFIG.get(index, {})

    # Live GROWW mode: try SDK candles first for ALL indices
    if _live_trading and _active_broker == "GROWW":
        raw = _fetch_groww_sdk_ohlcv(interval, index)

    # Fallback to existing providers if SDK didn't return data
    if raw is None or raw.empty:
        if config.get("data_provider") in ("bse_selenium", "groww_selenium"):
            raw = _fetch_groww_ohlcv(interval, index)
        else:
            raw = _fetch_yfinance_ohlcv(interval, index)

    if raw is not None and not raw.empty:
        _yf_cache[cache_key] = raw
        _yf_ts[cache_key] = now
        return raw

    return _yf_cache.get(cache_key)

def _fetch_yfinance_ohlcv(interval: str, index: str) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from Yahoo Finance (NIFTY)."""
    try:
        import yfinance as yf
        config = INDEX_CONFIG[index]
        ticker = config["yf_symbol"]

        raw = yf.download(ticker, period="60d", interval=interval,
                          progress=False, auto_adjust=True, multi_level_index=False)
        if raw is None or raw.empty:
            return None

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0].lower() for c in raw.columns]
        else:
            raw.columns = [str(c).lower() for c in raw.columns]

        if "close" not in raw.columns:
            return None

        for col in ["open", "high", "low", "volume"]:
            if col not in raw.columns:
                raw[col] = raw["close"]

        if raw.index.tz is None: # type: ignore
            raw.index = raw.index.tz_localize("UTC").tz_convert("Asia/Kolkata") # type: ignore
        else:
            raw.index = raw.index.tz_convert("Asia/Kolkata") # type: ignore

        raw = raw.between_time("09:15", "15:30").dropna(subset=["close"])
        return raw if not raw.empty else None
    except Exception as e:
        log.debug(f"yfinance {index} {interval}: {e}")
        return None

def _fetch_groww_ohlcv(interval: str, index: str) -> Optional[pd.DataFrame]:
    """
    Fetch SENSEX OHLCV data.
    Primary:  Yahoo Finance with ^BSESN symbol (reliable, free)
    Fallback: yfinance BSE:SENSEX ticker

    Note: Groww's chart page uses complex JS canvas rendering that
    cannot be scraped via DOM — Yahoo Finance is the correct source
    for SENSEX OHLCV candle data.
    """
    try:
        import yfinance as yf

        # Yahoo Finance symbol for SENSEX
        for ticker_sym in ["^BSESN", "BSE:SENSEX"]:
            try:
                raw = yf.download(
                    ticker_sym, period="60d", interval=interval,
                    progress=False, auto_adjust=True, multi_level_index=False
                )
                if raw is None or raw.empty:
                    continue

                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = [c[0].lower() for c in raw.columns]
                else:
                    raw.columns = [str(c).lower() for c in raw.columns]

                if "close" not in raw.columns:
                    continue

                for col in ["open", "high", "low", "volume"]:
                    if col not in raw.columns:
                        raw[col] = raw["close"]

                if raw.index.tz is None: # type: ignore
                    raw.index = raw.index.tz_localize("UTC").tz_convert("Asia/Kolkata") # type: ignore
                else:
                    raw.index = raw.index.tz_convert("Asia/Kolkata") # type: ignore

                raw = raw.between_time("09:15", "15:30").dropna(subset=["close"])
                if not raw.empty:
                    log.debug(f"[GROWW-OHLCV] {index} {interval}: {len(raw)} rows via {ticker_sym}")
                    return raw
            except Exception as e:
                log.debug(f"[GROWW-OHLCV] {ticker_sym} failed: {e}")
                continue

        log.warning(f"[GROWW-OHLCV] All tickers failed for {index} {interval}")
        return None

    except Exception as e:
        log.debug(f"[GROWW-OHLCV] {index} {interval}: {e}")
        return None

def _fetch_groww_sdk_ohlcv(interval: str, index: str) -> Optional[pd.DataFrame]:
    """Fetch OHLCV candle data using the official Groww SDK (get_historical_candle_data)."""
    sdk = _get_groww_sdk()
    if sdk is None:
        return None
    
    mapping = _GROWW_SDK_SYMBOL_MAP.get(index)
    if not mapping:
        return None
    
    exchange, _, trading_symbol = mapping
    
    # Map interval string to minutes
    interval_map = {
        "1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60,
    }
    minutes = interval_map.get(interval)
    if minutes is None:
        return None
    
    try:
        end = datetime.now()
        # Fetch enough days for indicators (60 days for daily, 5 days for intraday)
        days_back = 60 if minutes >= 60 else 5
        start = end - timedelta(days=days_back)
        
        start_str = start.strftime('%Y-%m-%dT00:00:00')
        end_str = end.strftime('%Y-%m-%dT%H:%M:%S')
        
        result = sdk.get_historical_candle_data(
            trading_symbol=trading_symbol,
            exchange=exchange,
            segment='CASH',
            start_time=start_str,
            end_time=end_str,
            interval_in_minutes=minutes
        )
        
        if not result or not isinstance(result, dict):
            return None
        
        candles = result.get('candles', [])
        if not candles:
            return None
        
        # Convert SDK candles [timestamp, open, high, low, close, volume] to DataFrame
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        rows = []
        for c in candles:
            ts = datetime.fromtimestamp(c[0], tz=ist)
            rows.append({
                'open': float(c[1]),
                'high': float(c[2]),
                'low': float(c[3]),
                'close': float(c[4]),
                'volume': float(c[5]) if c[5] is not None else 0.0,
            })
        
        if not rows:
            return None
        
        # Build timestamps separately for index
        timestamps = [datetime.fromtimestamp(c[0], tz=ist) for c in candles]
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps))
        
        # Filter to market hours
        df = df.between_time("09:15", "15:30").dropna(subset=["close"])
        
        if not df.empty:
            log.info(f"[GROWW-SDK] {index} {interval}: {len(df)} candles loaded via Groww SDK")
            return df
    except Exception as e:
        log.debug(f"[GROWW-SDK] OHLCV error for {index} {interval}: {e}")
    return None

def _get_vix() -> float:
    """Fetch India VIX from multiple sources with fallbacks."""
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/",
    }

    # ── Source 1: NSE official API (most accurate, real-time) ──
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=4)  # warm cookie
        r = session.get(
            "https://www.nseindia.com/api/allIndices",
            headers={**headers, "Accept": "application/json"},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            for entry in data.get("data", []):
                if entry.get("index", "").upper() in ("INDIA VIX", "INDIAVIX"):
                    val = float(entry.get("last", 0))
                    if 5.0 < val < 60.0:
                        log.debug(f"[VIX] NSE API → {val}")
                        return val
    except Exception as e:
        log.debug(f"[VIX] NSE API failed: {e}")

    # ── Source 2: Groww stocks API ──
    try:
        r = requests.get(
            "https://groww.in/v1/api/stocks_data/v1/tr_live_indices/exchange/NSE/segment/CASH/INDIAVIX/latest",
            headers=headers,
            timeout=4,
        )
        if r.status_code == 200:
            j = r.json()
            # Groww may return "ltp", "close", "value", or "currentValue"
            for key in ("ltp", "close", "value", "currentValue", "lastPrice"):
                raw = j.get(key)
                if raw is not None:
                    val = float(raw)
                    if 5.0 < val < 60.0:
                        log.debug(f"[VIX] Groww API ({key}) → {val}")
                        return val
    except Exception as e:
        log.debug(f"[VIX] Groww API failed: {e}")

    # ── Source 3: Yahoo Finance ──
    try:
        import yfinance as yf
        import pandas as pd
        df = yf.download("^INDIAVIX", period="5d", interval="5m",
                         progress=False, auto_adjust=True, multi_level_index=False)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [str(c).lower() for c in df.columns]
            v = float(df["close"].iloc[-1])
            if 5.0 < v < 60.0:
                log.debug(f"[VIX] Yahoo Finance → {v}")
                return v
    except Exception as e:
        log.debug(f"[VIX] Yahoo Finance failed: {e}")

    log.warning("[VIX] All sources failed — using cached/default value 15.0")
    return 15.0

# ─── IMPROVED SIGNAL GENERATION ────────────────────────────────────────────────
_last_signal: Dict[str, Tuple[str, datetime]] = {}

def _signal(index: str = "NIFTY") -> Optional[dict]:
    """
    IMPROVED STRATEGY:
    1. Trend confirmation (EMA stack) on 15m
    2. Pullback entry on 3m (wait for consolidation)
    3. Multi-timeframe confirmation (3m, 5m, 15m agree)
    4. Dynamic SL/TP based on ATR
    5. Market quality filters (VIX, ATR, time)
    """
    global _last_signal

    # Fetch data for the correct index
    df5  = _fetch_ohlcv("5m",  index)
    df15 = _fetch_ohlcv("15m", index)

    if df5 is None or len(df5) < 21 or df15 is None or len(df15) < 21:
        return None

    # ─── MARKET QUALITY CHECKS ────────────────────────────────────────────────
    vix = _get_cached_vix()
    # More lenient: allow trading if within 10-25 (was 12-20)
    if vix < 10.0 or vix > 25.0:
        return None  # Only reject if extremely calm or panicked

    if _is_noisy():
        return None  # First 15 mins or last 60 mins

    # ─── TREND DETECTION (15m primary) ────────────────────────────────────────
    c15 = df15["close"]
    e9_15  = float(_ema(c15, 9).iloc[-1])
    e21_15 = float(_ema(c15, 21).iloc[-1])
    e50_15 = float(_ema(c15, 50).iloc[-1])
    atr15  = float(_atr(df15).iloc[-1])
    ltp15  = float(c15.iloc[-1])
    atr_pct = atr15 / ltp15 if ltp15 > 0 else 0

    # Trend check: must be strong (all three EMAs stacked)
    if e9_15 > e21_15 > e50_15:
        direction = "CALL"
    elif e9_15 < e21_15 < e50_15:
        direction = "PUT"
    else:
        return None  # No clear trend

    # ─── ENTRY LOGIC (5m) - BOTH PULLBACK + TREND CONTINUATION ────────────────
    c5     = df5["close"]
    e9_5   = float(_ema(c5, 9).iloc[-1])
    e21_5  = float(_ema(c5, 21).iloc[-1])
    cur    = df5.iloc[-1]
    prev   = df5.iloc[-2]
    cur_close  = float(cur["close"])
    prev_close = float(prev["close"])
    cur_high   = float(cur["high"])
    cur_low    = float(cur["low"])
    prev_high  = float(prev["high"])
    prev_low   = float(prev["low"])
    cur_vol    = float(cur["volume"])
    vol_avg    = df5["volume"].iloc[-20:].mean()

    entry_signal = False

    if direction == "CALL":
        # Condition 1: Classic pullback (price dips to EMA21, now reversing up)
        if prev_close <= e21_5 and cur_close > e9_5:
            entry_signal = True
        # Condition 2: Price consolidating between EMA9-EMA21 with volume
        elif e9_5 < cur_close < e21_5:
            if vol_avg == 0 or cur_vol > vol_avg * 0.8:  # Relaxed volume check with 0-volume fallback
                entry_signal = True
        # Condition 3: Strong trend continuation - price above EMA9, staying strong
        elif cur_close > e9_5 > e21_5:
            if cur_high > prev_high or cur_close > prev_close:  # Enter on higher high OR higher close
                entry_signal = True
    else:  # PUT
        # Condition 1: Classic pullback (price bounces to EMA21, now breaking down)
        if prev_close >= e21_5 and cur_close < e9_5:
            entry_signal = True
        # Condition 2: Price consolidating between EMA9-EMA21 with volume
        elif e9_5 > cur_close > e21_5:
            if vol_avg == 0 or cur_vol > vol_avg * 0.8:  # Relaxed volume check with 0-volume fallback
                entry_signal = True
        # Condition 3: Strong trend continuation - price below EMA9, staying strong
        elif cur_close < e9_5 < e21_5:
            if cur_low < prev_low or cur_close < prev_close:  # Enter on lower low OR lower close
                entry_signal = True

    if not entry_signal:
        return None

    # ─── ATR VOLATILITY CHECK ────────────────────────────────────────────────
    # More lenient: allow entry if trend is strong even with lower ATR
    if atr_pct < 0.003:  # 0.3% minimum (was 0.5%)
        # Only reject if both ATR is low AND trend is weak
        ema_sep_15m = abs(e9_15 - e21_15)
        if ema_sep_15m < 10:  # Weak trend
            return None

    # ─── COOLDOWN CHECK ──────────────────────────────────────────────────────
    if index in _last_signal:
        ld, lt = _last_signal[index]
        mins_ago = (_now() - lt).total_seconds() / 60
        if ld == direction and mins_ago < COOLDOWN_MIN:
            return None  # Still in cooldown

    # ─── DYNAMIC SL/TP (STRUCTURE + ATR BASED) ─────────────────────────────────
    idx_cfg    = INDEX_CONFIG[index]
    strike_gap = idx_cfg["strike_gap"]
    opt        = "CE" if direction == "CALL" else "PE"
    spot       = _get_nse_spot(index) or ltp15
    atm        = round(spot / strike_gap) * strike_gap
    strike     = atm

    real_ltp = _get_nse_ltp(strike, opt, index)
    if real_ltp <= 0:
        return None

    entry = round(real_ltp + LIMIT_IN, 1)

    # SL: Recent swing + ATR buffer translated to option premium
    recent_high_5m = float(df5["high"].iloc[-10:].max())
    recent_low_5m  = float(df5["low"].iloc[-10:].min())

    if direction == "CALL":
        underlying_risk = spot - recent_low_5m + atr15 * 0.5
    else:
        underlying_risk = recent_high_5m + atr15 * 0.5 - spot

    # Scale the risk to option premium (Delta is ~0.5 for ATM options)
    # Ensure option risk is between 10% and 40% of option premium
    premium_risk = max(entry * 0.10, min(entry * 0.40, underlying_risk * 0.5))

    sl   = round(entry - premium_risk, 1)
    risk = entry - sl
    tp   = round(entry + risk * 1.8, 1)

    # Sanity check on risk/reward
    if risk <= 0 or abs(tp - entry) / risk < 1.5:
        return None

    _last_signal[index] = (direction, _now())

    log.info(f"\n{'─'*55}")
    log.info(f"[STR] PULLBACK ENTRY → {direction}")
    log.info(f"[STR] {index} {strike}{opt} | LTP=Rs.{real_ltp}")
    log.info(f"[STR] Entry=Rs.{entry} | SL=Rs.{sl} | TP=Rs.{tp}")
    log.info(f"[STR] Risk/Reward: 1:{abs(tp-entry)/risk:.1f} | VIX={vix:.1f} | ATR%={atr_pct*100:.2f}%")
    log.info(f"{'─'*55}\n")

    return {
        "index":     index,
        "direction": direction,
        "strike":    strike,
        "opt":       opt,
        "real_ltp":  real_ltp,
        "entry":     entry,
        "sl":        sl,
        "tp":        tp,
        "expiry":    _expiry(index),
        "dte":       _dte(index),
        "spot":      spot,
        "e9_15":     e9_15,
        "e21_15":    e21_15,
        "atr_pct":   round(atr_pct * 100, 3),
    }

def calculate_charges_breakdown(price: float, quantity: int, is_buy: bool, index: str = "NIFTY") -> dict:
    """
    Calculate exact F&O option contract charges breakdown for NSE/BSE Options in India:
    - Brokerage: flat Rs. 20 per transaction
    - STT: 0.1% of premium value (sell-side only, raised in 2024 budget)
    - Exchange Transaction Charge: 0.0495% (NSE) or 0.0325% (BSE) of premium value
    - SEBI Turnover Fee: 0.0001% (Rs. 10/crore) of premium value
    - GST: 18% of (Brokerage + Exchange Transaction Charges + SEBI Fee)
    - Stamp Duty: 0.003% of premium value (buy-side only)
    """
    if price <= 0 or quantity <= 0:
        return {
            "brokerage": 0.0,
            "stt": 0.0,
            "exchange_charges": 0.0,
            "sebi_fee": 0.0,
            "gst": 0.0,
            "stamp_duty": 0.0,
            "total": 0.0
        }
        
    premium_value = price * quantity
    
    # 1. Brokerage
    brokerage = 20.0
    
    # 2. STT (0.1% on sell side premium only)
    stt = round(0.001 * premium_value, 2) if not is_buy else 0.0
    
    # 3. Exchange Transaction Charges (NSE vs BSE)
    is_bse = any(x in index.upper() for x in ["SENSEX", "BANKEX", "BSE"])
    txn_rate = 0.000325 if is_bse else 0.000495
    exchange_charges = round(txn_rate * premium_value, 2)
    
    # 4. SEBI Turnover Fee (0.0001% of premium)
    sebi_fee = round(0.000001 * premium_value, 2)
    
    # 5. GST (18% of Brokerage + Exchange txn charges + SEBI fee)
    gst = round(0.18 * (brokerage + exchange_charges + sebi_fee), 2)
    
    # 6. Stamp Duty (0.003% on buy side premium only)
    stamp_duty = round(0.00003 * premium_value, 2) if is_buy else 0.0
    
    total = round(brokerage + stt + exchange_charges + sebi_fee + gst + stamp_duty, 2)
    
    return {
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charges": exchange_charges,
        "sebi_fee": sebi_fee,
        "gst": gst,
        "stamp_duty": stamp_duty,
        "total": total
    }

def calculate_charges(price: float, quantity: int, is_buy: bool, index: str = "NIFTY") -> float:
    """
    Calculate exact dynamic charges for options transaction using the regulatory breakdown.
    """
    bd = calculate_charges_breakdown(price, quantity, is_buy, index)
    return round(bd["total"], 2)

def parse_fno_symbol(symbol: str) -> Optional[dict]:
    """
    Parse standard Indian F&O option contract symbols to extract details.
    Examples:
      Weekly: NIFTY2660221100CE -> NIFTY, 2026-06-02, 21100, CE
      Monthly: NIFTY26JUN22000CE -> NIFTY, Last Thursday of June 2026, 22000, CE
      Dhan/Human Weekly: NIFTY 11 Jun 23400 CE -> NIFTY, 2026-06-11, 23400, CE
      Dhan/Human Monthly: NIFTY Jun 23000 CE -> NIFTY, Last Thursday of June 2026, 23000, CE
    """
    import re
    from datetime import date, timedelta
    import calendar
    
    symbol = symbol.strip().upper()
    if not symbol:
        return None
        
    month_abbr_map = {
        'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
        'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
    }
    
    # Try token-based parsing if symbol contains spaces
    if " " in symbol:
        tokens = [t.strip() for t in symbol.split(" ") if t.strip()]
        if len(tokens) >= 4:
            index = tokens[0]
            opt = tokens[-1]
            if opt in ("CE", "PE"):
                mid_tokens = tokens[1:-1]
                day = None
                month = None
                year = date.today().year
                strike = None
                
                # Check for: Day Month Strike (e.g. "11", "JUN", "23400")
                if len(mid_tokens) == 3:
                    if mid_tokens[0].isdigit() and int(mid_tokens[0]) <= 31 and mid_tokens[1] in month_abbr_map:
                        day = int(mid_tokens[0])
                        month = month_abbr_map[mid_tokens[1]]
                        strike = float(mid_tokens[2])
                    elif mid_tokens[0] in month_abbr_map and mid_tokens[1].isdigit():
                        # Month Year Strike or Month Day Strike?
                        month = month_abbr_map[mid_tokens[0]]
                        year_val = int(mid_tokens[1])
                        year = 2000 + year_val if year_val < 100 else year_val
                        strike = float(mid_tokens[2])
                # Check for: Day Month Year Strike (e.g. "11", "JUN", "26", "23400")
                elif len(mid_tokens) == 4:
                    if mid_tokens[0].isdigit() and int(mid_tokens[0]) <= 31 and mid_tokens[1] in month_abbr_map:
                        day = int(mid_tokens[0])
                        month = month_abbr_map[mid_tokens[1]]
                        year_val = int(mid_tokens[2])
                        year = 2000 + year_val if year_val < 100 else year_val
                        strike = float(mid_tokens[3])
                # Check for: Month Strike (e.g. "JUN", "23000")
                elif len(mid_tokens) == 2:
                    if mid_tokens[0] in month_abbr_map:
                        month = month_abbr_map[mid_tokens[0]]
                        strike = float(mid_tokens[1])
                
                if month is not None and strike is not None:
                    if day is not None:
                        try:
                            expiry_date = date(year, month, day)
                            expiry_str = expiry_date.strftime("%Y-%m-%d")
                        except Exception:
                            expiry_str = f"{year:04d}-{month:02d}-{day:02d}"
                    else:
                        try:
                            last_day = calendar.monthrange(year, month)[1]
                            expiry_date = date(year, month, last_day)
                            while expiry_date.weekday() != 3: # 3 is Thursday
                                expiry_date -= timedelta(days=1)
                            expiry_str = expiry_date.strftime("%Y-%m-%d")
                        except Exception:
                            expiry_str = f"{year:04d}-{month:02d}-XX"
                    
                    return {
                        "index": index,
                        "strike": strike,
                        "opt": opt,
                        "expiry": expiry_str
                    }
                    
    # Try Weekly pattern: INDEX + YY + M (1 char) + DD (2 chars) + STRIKE (digits) + OPT (CE/PE)
    m_weekly = re.match(r"^([A-Z]+)(\d{2})([1-9OND])(\d{2})(\d+)(CE|PE)$", symbol)
    if m_weekly:
        index, year_str, month_str, day_str, strike_str, opt = m_weekly.groups()
        month_map = {'1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, 'O': 10, 'N': 11, 'D': 12}
        month = month_map.get(month_str, 1)
        day = int(day_str)
        year = 2000 + int(year_str)
        try:
            expiry_date = date(year, month, day)
            expiry_str = expiry_date.strftime("%Y-%m-%d")
        except Exception:
            expiry_str = f"20{year_str}-{month_str}-{day_str}"
        return {
            "index": index,
            "strike": float(strike_str),
            "opt": opt,
            "expiry": expiry_str
        }
        
    # Try Monthly pattern: INDEX + YY + MMM (3 chars) + STRIKE (digits) + OPT (CE/PE)
    m_monthly = re.match(r"^([A-Z]+)(\d{2})([A-Z]{3})(\d+)(CE|PE)$", symbol)
    if m_monthly:
        index, year_str, month_abbr, strike_str, opt = m_monthly.groups()
        month = month_abbr_map.get(month_abbr, 1)
        year = 2000 + int(year_str)
        try:
            last_day = calendar.monthrange(year, month)[1]
            expiry_date = date(year, month, last_day)
            while expiry_date.weekday() != 3: # 3 is Thursday
                expiry_date -= timedelta(days=1)
            expiry_str = expiry_date.strftime("%Y-%m-%d")
        except Exception:
            expiry_str = f"20{year_str}-{month_abbr}"
        return {
            "index": index,
            "strike": float(strike_str),
            "opt": opt,
            "expiry": expiry_str
        }
    return None

# ─── POSITION ─────────────────────────────────────────────────────────────────
@dataclass
class Pos:
    tid:        str
    index:      str           # NEW: which index (NIFTY/SENSEX)
    direction:  str
    strike:     float
    opt:        str
    expiry:     str
    lots:       int
    contracts:  int
    entry:      float
    sl:         float
    tp:         float
    entry_time: datetime
    entry_spot: float
    e9_15:      float
    e21_15:     float
    user_id:    int = 0
    is_open:    bool = True
    cur:        float = 0.0
    peak:       float = 0.0
    exit_px:    float = 0.0
    exit_time:  Optional[datetime] = None
    exit_reason:str   = ""
    pnl:        float = 0.0
    dhan_order_id: str = ""
    dhan_sec_id:   str = ""
    broker:        str = "DHAN"
    entry_charges: float = 0.0
    exit_charges:  float = 0.0
    charges:       float = 0.0
    brokerage:     float = 0.0
    gst:           float = 0.0
    stt:           float = 0.0
    stamp_duty:    float = 0.0
    exchange_charges: float = 0.0
    sebi_fee:      float = 0.0
    vix:           float = 15.0
    atr_pct:       float = 0.005
    guard_status:  str = "Disabled"
    predicted_win_prob: float = 100.0
    is_super_order: bool = False
    trailing_sl_enabled: bool = True
    is_live:       bool = False

    @property
    def cost(self):    return self.entry * self.contracts
    @property
    def pnl_pct(self): return self.pnl / self.cost * 100 if self.cost else 0

# ─── LIVE POSITION LTP FETCH ──────────────────────────────────────────────────
def _get_live_position_ltp(p: 'Pos', client = None, live_trading = False) -> float:
    """Fetch option live price from Groww or Dhan."""
    if not live_trading:
        return 0.0
    try:
        broker_name = getattr(p, "broker", "GROWW")
        if broker_name == "GROWW":
            contract_id = p.dhan_sec_id or p.dhan_order_id
            if contract_id and not contract_id.startswith("MOCK") and not contract_id.startswith("SYNC"):
                return _get_groww_contract_ltp(contract_id)
        elif broker_name == "DHAN" and client:
            exchange_segment = "BSE_FNO" if p.index == "SENSEX" else "NSE_FNO"
            if p.dhan_sec_id:
                return client.get_ltp(p.dhan_sec_id, exchange_segment)
    except Exception as e:
        log.error(f"[LTP] Error getting live position LTP: {e}")
    return 0.0

class Book:
    def __init__(self, user_id: int):
        self.user_id = user_id
        # Load user config from database
        u_config = db_helper.get_user_config(user_id)
        self.username = u_config.get("username", f"User_{user_id}")
        
        # User specific configurations
        self.trading_indices = [x.strip().upper() for x in u_config.get("trading_indices", "NIFTY,SENSEX").split(",") if x.strip()]
        self.capital_amount = u_config.get("capital", 200000.0)
        self.active_broker = u_config.get("active_broker", "GROWW")
        self.live_trading = bool(u_config.get("live_trading", 0))
        self.trading_active = bool(u_config.get("trading_active", 1))
        self.smart_filter_enabled = bool(u_config.get("smart_filter_enabled", 1))
        self.trailing_sl_enabled = bool(u_config.get("trailing_sl_enabled", 1))
        self.risk_per_trade_pct = u_config.get("risk_per_trade_pct", 0.05)
        self.target_per_trade_pct = u_config.get("target_per_trade_pct", 0.15)
        self.sl_on_premium_pct = u_config.get("sl_on_premium_pct", 0.05)
        self.tp_on_premium_pct = u_config.get("tp_on_premium_pct", 0.15)
        
        self.dhan_client_id = u_config.get("dhan_client_id", "")
        self.dhan_access_token = u_config.get("dhan_access_token", "")
        self.groww_client_id = u_config.get("groww_client_id", "")
        self.groww_pin = u_config.get("groww_pin", "")
        
        # Initialize the user's specific clients
        from dhan_client import DhanClient
        from groww_client import GrowwClientWrapper
        self.dhan_client = DhanClient(client_id=self.dhan_client_id, access_token=self.dhan_access_token) if (self.dhan_client_id and self.dhan_access_token) else None
        self.groww_client = GrowwClientWrapper(groww_client_id=self.groww_client_id, groww_pin=self.groww_pin) if (self.groww_client_id and self.groww_pin) else None
        
        self.max_trades = MAX_TRADES
        self.max_daily_trades_per_index = cfg.max_daily_trades_per_index
        self.max_open_per_index = cfg.max_open_per_index
        
        self.open:     Dict[str, Dict[str, Pos]] = {idx: {} for idx in self.trading_indices}
        self.closed:   List[Pos]      = []
        self._n        = 0
        self.day_pnl:  Dict[str, float] = {idx: 0.0 for idx in self.trading_indices}
        self.day_trades: Dict[str, int] = {idx: 0 for idx in self.trading_indices}
        
        # Load open positions from DB
        db_positions = db_helper.load_user_open_positions(user_id)
        for tid, pos_dict in db_positions.items():
            idx = pos_dict["index_name"]
            is_pos_live = bool(pos_dict.get("is_live", 0))
            if is_pos_live != self.live_trading:
                continue
            if idx not in self.open:
                self.open[idx] = {}
            if idx not in self.day_pnl:
                self.day_pnl[idx] = 0.0
            if idx not in self.day_trades:
                self.day_trades[idx] = 0
            
            p = Pos(
                tid=pos_dict["tid"], index=idx, direction=pos_dict["direction"],
                user_id=user_id, is_open=True,
                strike=pos_dict["strike"], opt=pos_dict["opt"], expiry=pos_dict["expiry"],
                lots=pos_dict["lots"], contracts=pos_dict["contracts"],
                entry=pos_dict["entry"], sl=pos_dict["sl"], tp=pos_dict["tp"],
                entry_time=pos_dict["entry_time"], entry_spot=pos_dict["entry_spot"],
                e9_15=pos_dict["e9_15"], e21_15=pos_dict["e21_15"],
                cur=pos_dict["cur"], peak=pos_dict["peak"],
                dhan_order_id=pos_dict["dhan_order_id"], dhan_sec_id=pos_dict["dhan_sec_id"],
                broker=pos_dict["broker"],
                entry_charges=pos_dict["entry_charges"], exit_charges=pos_dict["exit_charges"],
                charges=pos_dict["charges"], brokerage=pos_dict["brokerage"],
                gst=pos_dict["gst"], stt=pos_dict["stt"], stamp_duty=pos_dict["stamp_duty"],
                exchange_charges=pos_dict["exchange_charges"], sebi_fee=pos_dict["sebi_fee"],
                vix=pos_dict["vix"], atr_pct=pos_dict["atr_pct"],
                guard_status=pos_dict["guard_status"], predicted_win_prob=pos_dict["predicted_win_prob"],
                is_super_order=bool(pos_dict["is_super_order"]), trailing_sl_enabled=bool(pos_dict["trailing_sl_enabled"]),
                is_live=is_pos_live
            )
            self.open[idx][tid] = p
        
        # Load closed positions (trade history) from DB for statistics (filtered by mode)
        db_closed = db_helper.load_user_trade_history(user_id, is_live=self.live_trading)
        for pos_dict in db_closed:
            idx = pos_dict["index_name"]
            is_pos_live = bool(pos_dict.get("is_live", 0))
            p = Pos(
                tid=pos_dict["tid"], index=idx, direction=pos_dict["direction"],
                user_id=user_id, is_open=False,
                strike=pos_dict["strike"], opt=pos_dict["opt"], expiry=pos_dict["expiry"],
                lots=pos_dict["lots"], contracts=pos_dict["contracts"],
                entry=pos_dict["entry"], sl=pos_dict["sl"], tp=pos_dict["tp"],
                entry_time=pos_dict["entry_time"], entry_spot=pos_dict["entry_spot"],
                e9_15=pos_dict["e9_15"], e21_15=pos_dict["e21_15"],
                cur=pos_dict["cur"], peak=pos_dict["peak"],
                exit_px=pos_dict["exit_px"], exit_time=pos_dict["exit_time"],
                exit_reason=pos_dict["exit_reason"], pnl=pos_dict["pnl"],
                dhan_order_id=pos_dict["dhan_order_id"], dhan_sec_id=pos_dict["dhan_sec_id"],
                broker=pos_dict["broker"],
                entry_charges=pos_dict["entry_charges"], exit_charges=pos_dict["exit_charges"],
                charges=pos_dict["charges"], brokerage=pos_dict["brokerage"],
                gst=pos_dict["gst"], stt=pos_dict["stt"], stamp_duty=pos_dict["stamp_duty"],
                exchange_charges=pos_dict["exchange_charges"], sebi_fee=pos_dict["sebi_fee"],
                vix=pos_dict["vix"], atr_pct=pos_dict["atr_pct"],
                guard_status=pos_dict["guard_status"], predicted_win_prob=pos_dict["predicted_win_prob"],
                is_super_order=bool(pos_dict["is_super_order"]), trailing_sl_enabled=bool(pos_dict["trailing_sl_enabled"]),
                is_live=is_pos_live
            )
            self.closed.append(p)

        # Split capital across active trading indices
        total_config_capital = sum(CAPITAL_PER_INDEX.get(idx, 100000.0) for idx in self.trading_indices)
        self.capital: Dict[str, float] = {}
        for idx in self.trading_indices:
            ratio = CAPITAL_PER_INDEX.get(idx, 100000.0) / (total_config_capital or 100000.0)
            self.capital[idx] = round(self.capital_amount * ratio, 2)
            
        self.total_capital: float = sum(self.capital.values())
        
        # Calculate day PnL and day trades from history (matching today's date)
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        for idx in self.trading_indices:
            self.day_pnl[idx] = 0.0
            self.day_trades[idx] = 0
        
        for p in self.closed:
            exit_time_str = p.exit_time[:10] if isinstance(p.exit_time, str) else p.exit_time.strftime("%Y-%m-%d") if p.exit_time else ""
            if exit_time_str == today_str:
                if p.index not in self.day_pnl:
                    self.day_pnl[p.index] = 0.0
                if p.index not in self.day_trades:
                    self.day_trades[p.index] = 0
                self.day_pnl[p.index] += p.pnl
                self.day_trades[p.index] += 1
                
        self.cum_pnl = round(sum(p.pnl for p in self.closed), 2)
        self.profit_hit = False
        
        self.sync_live_positions()

    def update_total_capital(self, new_capital: float):
        """Dynamically update capital and re-evaluate splits."""
        total_config_capital = sum(CAPITAL_PER_INDEX.get(idx, 100000.0) for idx in self.trading_indices)
        self.capital = {}
        for idx in self.trading_indices:
            ratio = CAPITAL_PER_INDEX.get(idx, 100000.0) / (total_config_capital or 100000.0)
            self.capital[idx] = round(new_capital * ratio, 2)
        self.total_capital = sum(self.capital.values())
        self.cum_pnl = 0.0
        self.profit_hit = False
        
        # Update user config in DB
        db_helper.update_user_config(self.user_id, {"capital": self.total_capital})
        log.info(f"[CAPITAL] [{self.username}] Persisted newly updated capital of Rs.{self.total_capital:,.2f} to DB")

    def enter(self, sig: dict) -> Optional[Pos]:
        index = sig["index"]

        # Check position limits per index
        if len(self.open[index]) >= self.max_open_per_index:
            return None
        if self.day_trades[index] >= self.max_daily_trades_per_index:
            return None

        config = INDEX_CONFIG[index]
        lot_size = config["lot_size"]

        # Dynamic lots based on index capital or available broker balance
        index_capital = self.capital[index]
        lots_calculated = False
        lots = 1
        
        if self.live_trading:
            available_balance = 0.0
            if self.active_broker == "DHAN" and self.dhan_client:
                try:
                    broker_cap = self.dhan_client.get_broker_capital()
                    available_balance = broker_cap.get("available", 0.0)
                except Exception as e:
                    log.error(f"[RISK] [{self.username}] Failed to get live Dhan capital: {e}")
            elif self.active_broker == "GROWW" and self.groww_client:
                try:
                    broker_cap = self.groww_client.get_broker_capital()
                    available_balance = broker_cap.get("fno_available", 0.0) or broker_cap.get("available", 0.0)
                except Exception as e:
                    log.error(f"[RISK] [{self.username}] Failed to get live Groww capital: {e}")

            if available_balance > 0:
                try:
                    option_price = float(sig["entry"])
                    cost_per_lot = option_price * lot_size
                    lots = math.floor(available_balance / cost_per_lot)
                    if lots < 1:
                        lots = 1
                    lots_calculated = True
                    log.info(f"[RISK] [LIVE SIZING] [{self.username}] {index}: Balance=Rs.{available_balance:,.2f} | Option Price=Rs.{option_price:.2f} | Lots={lots}")
                except Exception as e:
                    log.error(f"[RISK] [{self.username}] Error calculating live lots: {e}")

        if not lots_calculated:
            if self.live_trading:
                # Dynamic index capital weight fallback
                if self.active_broker == "DHAN" and self.dhan_client:
                    try:
                        broker_cap = self.dhan_client.get_broker_capital()
                        if broker_cap.get("available", 0.0) > 0:
                            total_config_capital = sum(CAPITAL_PER_INDEX.get(idx, 100000.0) for idx in self.trading_indices)
                            ratio_weight = CAPITAL_PER_INDEX.get(index, 100000.0) / total_config_capital
                            index_capital = broker_cap["available"] * ratio_weight
                    except Exception:
                        pass
                elif self.active_broker == "GROWW" and self.groww_client:
                    try:
                        broker_cap = self.groww_client.get_broker_capital()
                        if broker_cap.get("available", 0.0) > 0:
                            total_config_capital = sum(CAPITAL_PER_INDEX.get(idx, 100000.0) for idx in self.trading_indices)
                            ratio_weight = CAPITAL_PER_INDEX.get(index, 100000.0) / total_config_capital
                            index_capital = broker_cap["available"] * ratio_weight
                    except Exception:
                        pass

            ratio = index_capital / CAPITAL_PER_INDEX[index]
            if   ratio >= 1.00: lots = 5
            elif ratio >= 0.80: lots = 4
            elif ratio >= 0.60: lots = 3
            elif ratio >= 0.40: lots = 2
            else:               lots = 1

        if self.live_trading and not lots_calculated:
            log.warning(f"[RISK] [{self.username}] ⚠️ Live balance fetch returned 0 or failed. Falling back to configured capital Rs.{index_capital:,.2f} for lot sizing.")
            ratio = index_capital / CAPITAL_PER_INDEX[index]
            if   ratio >= 1.00: lots = 5
            elif ratio >= 0.80: lots = 4
            elif ratio >= 0.60: lots = 3
            elif ratio >= 0.40: lots = 2
            else:               lots = 1
            lots_calculated = True

        contracts = lots * lot_size

        # Smart Signal Guard check
        guard_status_val = "Disabled"
        prob_pct_val = 100.0

        if self.smart_filter_enabled:
            vix_val = float(sig.get("vix") or 15.0)
            atr = float(sig.get("atr") or 0.0)
            spot = sig.get("spot") or sig.get("entry")
            spot_val = float(spot) if spot is not None else 0.0
            atr_pct = atr / spot_val if spot_val > 0.0 else 0.005
            e9_15 = float(sig.get("e9_15") or 0.0)
            e21_15 = float(sig.get("e21_15") or 0.0)
            
            should_block, prob_pct = smart_filter.evaluate_signal(
                index=index,
                direction=sig["direction"],
                strike=sig["strike"],
                entry_price=sig["entry"],
                lots=lots,
                vix=vix_val,
                atr_pct=atr_pct,
                ema9_15m=e9_15,
                ema21_15m=e21_15
            )
            
            if should_block:
                log.info(f"[SMART GUARD] [{self.username}] 🛡️ Blocked signal (Win prob={prob_pct}% < {cfg.smart_win_threshold*100}%).")
                return None
            
            guard_status_val = "Active" if smart_filter.trained else "Learning"
            prob_pct_val = prob_pct

        # Resolve dynamic contract and execute live order if live trading is active
        dhan_order_id = ""
        dhan_sec_id = ""
        broker_name = "DHAN"
        is_super = False
        if self.live_trading:
            broker_name = self.active_broker
            if broker_name == "DHAN":
                sec_id = _get_dhan_sec_id(index, sig["strike"], sig["opt"], sig["expiry"], client=self.dhan_client)
                if not sec_id:
                    log.error(f"[{broker_name}] [{self.username}] Aborting order entry — Security ID unresolved.")
                    return None
            else:
                resolved = _get_groww_contract_symbol(index, sig["strike"], sig["opt"])
                if resolved:
                    sec_id = resolved
                else:
                    sec_id = f"GRW_SEC_{int(sig['strike'])}_{sig['opt']}"
                
            # Place live order
            if broker_name == "DHAN":
                if cfg.dhan_super_order:
                    dhan_order_id = place_dhan_super_order(
                        sec_id=sec_id,
                        action="BUY",
                        quantity=contracts,
                        index=index,
                        entry_px=sig["entry"],
                        sl_px=sig["sl"],
                        tp_px=sig["tp"],
                        client_id=self.dhan_client_id,
                        access_token=self.dhan_access_token
                    )
                    if dhan_order_id:
                        is_super = True
                else:
                    dhan_order_id = place_dhan_order(sec_id, "BUY", contracts, index, client=self.dhan_client)
            else:
                dhan_order_id = place_groww_order(sec_id, "BUY", contracts, index, client=self.groww_client)
                
            if not dhan_order_id:
                log.error(f"[{broker_name}] [{self.username}] ❌ Live order placement failed. Aborting entry.")
                return None
            dhan_sec_id = sec_id

        self._n  += 1
        display_index = "ALGO_TRADING" if index == "NIFTY" else index
        tid       = f"{display_index}_{_now().strftime('%Y%m%d_%H%M%S')}_{self._n:03d}"
        entry_bd = calculate_charges_breakdown(sig["entry"], contracts, is_buy=True, index=index)
        spot_val = float(sig.get("spot") or sig.get("entry") or 0.0)
        p = Pos(
            tid=tid, index=index, user_id=self.user_id, is_open=True, direction=sig["direction"],
            strike=sig["strike"], opt=sig["opt"], expiry=sig["expiry"],
            lots=lots, contracts=contracts,
            entry=float(sig["entry"]), sl=float(sig["sl"]), tp=float(sig["tp"]),
            entry_time=_now(), entry_spot=spot_val,
            e9_15=float(sig.get("e9_15") or 0.0), e21_15=float(sig.get("e21_15") or 0.0),
            cur=float(sig["entry"]), peak=float(sig["entry"]),
            dhan_order_id=dhan_order_id, dhan_sec_id=dhan_sec_id,
            broker=broker_name,
            entry_charges=entry_bd["total"],
            charges=entry_bd["total"],
            brokerage=entry_bd["brokerage"],
            gst=entry_bd["gst"],
            stt=entry_bd["stt"],
            stamp_duty=entry_bd["stamp_duty"],
            exchange_charges=entry_bd["exchange_charges"],
            sebi_fee=entry_bd["sebi_fee"],
            vix=float(sig.get("vix") or 15.0),
            atr_pct=float(sig.get("atr") or 0.0) / spot_val if spot_val > 0.0 else 0.005,
            guard_status=guard_status_val,
            predicted_win_prob=prob_pct_val,
            is_super_order=is_super,
            trailing_sl_enabled=self.trailing_sl_enabled,
            is_live=self.live_trading
        )
        self.open[index][tid]  = p
        self.day_trades[index] += 1
        
        # Save position to DB
        db_helper.save_position(self.user_id, p)

        log.info(f"\n{'='*55}")
        log.info(f"  OPEN  {tid} | User: {self.username}")
        log.info(f"  {display_index} {int(p.strike)}{p.opt} | {p.direction}")
        log.info(f"  Entry=Rs.{p.entry} | SL=Rs.{p.sl} | TP=Rs.{p.tp}")
        log.info(f"{'='*55}\n")

        alert_entry(
            p.direction, p.strike, p.entry, p.sl, p.tp, 
            index=p.index, lots=p.lots, contracts=p.contracts,
            guard_status=p.guard_status, predicted_win_prob=p.predicted_win_prob
        )
        return p

    def sync_live_positions(self):
        """Synchronize open positions in the Book with the live broker (Groww/Dhan)."""
        if not self.live_trading:
            return

        now_time = time.time()
        if not hasattr(self, '_last_broker_sync_time'):
            self._last_broker_sync_time = 0.0
        if now_time - self._last_broker_sync_time < 3.0:
            return
        self._last_broker_sync_time = now_time

        if self.active_broker == "GROWW" and self.groww_client:
            try:
                if not self.groww_client.authenticated:
                    return
                pos_resp = self.groww_client.get_positions()
                if not pos_resp or not isinstance(pos_resp, dict):
                    return
                
                broker_positions = pos_resp.get("positions", [])
                active_symbols = set()
                
                for bp in broker_positions:
                    segment = bp.get("segment", "").upper()
                    if segment != "FNO": continue
                    symbol = bp.get("trading_symbol", "")
                    if not symbol: continue
                    qty = int(bp.get("quantity", 0))
                    if qty <= 0: continue
                    active_symbols.add(symbol)
                    
                    # Search self.open
                    found = False
                    for idx in list(self.open.keys()):
                        for tid, p in list(self.open[idx].items()):
                            if p.dhan_sec_id == symbol or p.dhan_order_id == symbol:
                                found = True
                                break
                                
                    if not found:
                        parsed = parse_fno_symbol(symbol)
                        if parsed:
                            index = parsed["index"]
                            if index not in self.open:
                                self.open[index] = {}
                            if index not in self.day_pnl:
                                self.day_pnl[index] = 0.0
                            if index not in self.day_trades:
                                self.day_trades[index] = 0
                                
                            self._n += 1
                            display_index = "ALGO_TRADING" if index == "NIFTY" else index
                            tid = f"{display_index}_{_now().strftime('%Y%m%d_%H%M%S')}_{self._n:03d}"
                            strike = parsed["strike"]
                            opt = parsed["opt"]
                            expiry = parsed["expiry"]
                            entry_px = float(bp.get("net_price", 0.0) or bp.get("credit_price", 0.0) or 100.0)
                            contracts = qty
                            lot_size = int(INDEX_CONFIG[index]["lot_size"]) if index in INDEX_CONFIG else 1
                            lots = max(1, contracts // lot_size)
                            sl = round(entry_px * (1.0 - self.sl_on_premium_pct), 1)
                            tp = round(entry_px * (1.0 + self.tp_on_premium_pct), 1)
                            
                            p = Pos(
                                tid=tid, index=index, user_id=self.user_id, is_open=True, direction=opt,
                                strike=strike, opt=opt, expiry=expiry, lots=lots, contracts=contracts,
                                entry=entry_px, sl=sl, tp=tp, entry_time=_now(), entry_spot=strike,
                                e9_15=0.0, e21_15=0.0, cur=entry_px, peak=entry_px,
                                dhan_order_id=bp.get("groww_order_id", f"SYNC_{symbol}"), dhan_sec_id=symbol,
                                broker="GROWW", vix=_get_cached_vix(), atr_pct=0.005,
                                is_super_order=False, trailing_sl_enabled=self.trailing_sl_enabled,
                                is_live=self.live_trading
                            )
                            self.open[index][tid] = p
                            db_helper.save_position(self.user_id, p)
                            log.info(f"[SYNC] 📥 Imported Groww position for {self.username}: {symbol} | Qty: {qty}")
                                
                # Reconcile closed positions
                for idx in list(self.open.keys()):
                    for tid, p in list(self.open[idx].items()):
                        if p.broker == "GROWW" and p.dhan_sec_id and p.dhan_sec_id not in active_symbols:
                            if not p.dhan_order_id.startswith("MOCK"):
                                log.info(f"[SYNC] 📤 Closing position {p.tid} ({p.dhan_sec_id}) for {self.username} (external exit).")
                                self._close(p, p.cur, "EXTERNAL_EXIT")
            except Exception as e:
                log.error(f"[SYNC] Error syncing Groww positions for {self.username}: {e}")

        elif self.active_broker == "DHAN" and self.dhan_client:
            try:
                sdk = getattr(self.dhan_client, "_dhan", None)
                if not sdk or not hasattr(sdk, "get_positions"):
                    return
                pos_resp = sdk.get_positions()
                if not pos_resp or not isinstance(pos_resp, dict) or pos_resp.get("status") != "success":
                    return
                
                broker_positions = pos_resp.get("data", [])
                active_symbols = set()
                
                for bp in broker_positions:
                    segment = bp.get("exchangeSegment", "").upper()
                    if "FNO" not in segment: continue
                    symbol = bp.get("tradingSymbol", "")
                    if not symbol: continue
                    qty = int(bp.get("netQty", 0))
                    if qty <= 0: continue
                    active_symbols.add(symbol)
                    
                    found = False
                    for idx in list(self.open.keys()):
                        for tid, p in list(self.open[idx].items()):
                            if p.dhan_sec_id == symbol or p.dhan_order_id == symbol:
                                found = True
                                break
                                
                    if not found:
                        parsed = parse_fno_symbol(symbol)
                        if parsed:
                            index = parsed["index"]
                            if index not in self.open:
                                self.open[index] = {}
                            if index not in self.day_pnl:
                                self.day_pnl[index] = 0.0
                            if index not in self.day_trades:
                                self.day_trades[index] = 0
                                
                            self._n += 1
                            display_index = "ALGO_TRADING" if index == "NIFTY" else index
                            tid = f"{display_index}_{_now().strftime('%Y%m%d_%H%M%S')}_{self._n:03d}"
                            strike = parsed["strike"]
                            opt = parsed["opt"]
                            expiry = parsed["expiry"]
                            entry_px = float(bp.get("buyAvg", 0.0) or bp.get("lastPrice", 100.0))
                            contracts = qty
                            lot_size = int(INDEX_CONFIG[index]["lot_size"]) if index in INDEX_CONFIG else 1
                            lots = max(1, contracts // lot_size)
                            sl = round(entry_px * (1.0 - self.sl_on_premium_pct), 1)
                            tp = round(entry_px * (1.0 + self.tp_on_premium_pct), 1)
                            
                            p = Pos(
                                tid=tid, index=index, user_id=self.user_id, is_open=True, direction=opt,
                                strike=strike, opt=opt, expiry=expiry, lots=lots, contracts=contracts,
                                entry=entry_px, sl=sl, tp=tp, entry_time=_now(), entry_spot=strike,
                                e9_15=0.0, e21_15=0.0, cur=entry_px, peak=entry_px,
                                dhan_order_id=f"SYNC_{symbol}", dhan_sec_id=symbol,
                                broker="DHAN", vix=_get_cached_vix(), atr_pct=0.005,
                                is_super_order=False, trailing_sl_enabled=self.trailing_sl_enabled,
                                is_live=self.live_trading
                            )
                            self.open[index][tid] = p
                            db_helper.save_position(self.user_id, p)
                            log.info(f"[SYNC] 📥 Imported Dhan position for {self.username}: {symbol} | Qty: {qty}")
                                
                for idx in list(self.open.keys()):
                    for tid, p in list(self.open[idx].items()):
                        if p.broker == "DHAN" and p.dhan_sec_id and p.dhan_sec_id not in active_symbols:
                            if not p.dhan_order_id.startswith("MOCK"):
                                log.info(f"[SYNC] 📤 Closing position {p.tid} ({p.dhan_sec_id}) for {self.username} (external exit).")
                                self._close(p, p.cur, "EXTERNAL_EXIT")
            except Exception as e:
                log.error(f"[SYNC] Error syncing Dhan positions for {self.username}: {e}")

    def mtm(self) -> List[Pos]:
        """Reprice and manage open positions across all indices."""
        closed = []
        for index in list(self.open.keys()):
            for tid, p in list(self.open[index].items()):
                real_ltp = 0.0
                if self.live_trading:
                    real_ltp = _get_live_position_ltp(p, client=self.groww_client if p.broker == "GROWW" else self.dhan_client, live_trading=self.live_trading)
                if real_ltp <= 0:
                    real_ltp = _get_nse_ltp(p.strike, p.opt, index)
                ltp = real_ltp if real_ltp > 0 else p.cur

                p.cur  = ltp
                p.peak = max(p.peak, ltp)

                if p.trailing_sl_enabled:
                    risk_dist = p.entry - p.sl
                    if risk_dist > 0:
                        trigger_level = p.entry + risk_dist * 0.3
                        if ltp >= trigger_level:
                            trail_sl = round(p.peak - risk_dist * 0.7, 1)
                            if trail_sl > p.sl:
                                p.sl = trail_sl
                                log.info(f"[TRAILING] 📈 {p.tid} ({self.username}) TSL active. Peak: Rs.{p.peak:.1f} | New SL: Rs.{p.sl:.1f}")

                if ltp <= p.sl:
                    closed.append(self._close(p, round(max(0.5, p.sl), 1), "STOP_LOSS"))
                elif ltp >= p.tp:
                    closed.append(self._close(p, round(max(0.5, ltp - LIMIT_OUT), 1), "TARGET"))
        return closed

    def exit_all(self, reason: str = "FORCE_EXIT"):
        for index in list(self.open.keys()):
            for tid, p in list(self.open[index].items()):
                real_ltp = _get_nse_ltp(p.strike, p.opt, index)
                ltp = real_ltp if real_ltp > 0 else p.cur
                self._close(p, round(max(0.5, ltp - LIMIT_OUT), 1), reason)

    def _close(self, p: Pos, exit_px: float, reason: str) -> Pos:
        index = p.index
        if p.tid not in self.open[index]:
            return p

        # Offsetting sell order
        if p.dhan_order_id and p.dhan_sec_id:
            broker_name = getattr(p, "broker", "DHAN")
            if broker_name == "DHAN":
                if getattr(p, "is_super_order", False):
                    if reason not in ("STOP_LOSS", "TARGET"):
                        cancel_dhan_super_order(p.dhan_order_id, access_token=self.dhan_access_token)
                        place_dhan_order(p.dhan_sec_id, "SELL", p.contracts, index, client=self.dhan_client)
                    else:
                        log.info(f"[DHAN-SUPER] Native TP/SL trigger hit for {self.username}.")
                else:
                    place_dhan_order(p.dhan_sec_id, "SELL", p.contracts, index, client=self.dhan_client)
            else:
                place_groww_order(p.dhan_sec_id, "SELL", p.contracts, index, client=self.groww_client)

        p.exit_px    = exit_px
        p.exit_time  = _now()
        p.exit_reason= reason
        p.is_open    = False
        
        exit_bd = calculate_charges_breakdown(exit_px, p.contracts, is_buy=False, index=p.index)
        p.exit_charges = exit_bd["total"]
        p.charges = round(p.entry_charges + p.exit_charges, 2)
        
        p.brokerage = round(p.brokerage + exit_bd["brokerage"], 2)
        p.gst = round(p.gst + exit_bd["gst"], 2)
        p.stt = round(p.stt + exit_bd["stt"], 2)
        p.stamp_duty = round(p.stamp_duty + exit_bd["stamp_duty"], 2)
        p.exchange_charges = round(p.exchange_charges + exit_bd["exchange_charges"], 2)
        p.sebi_fee = round(p.sebi_fee + exit_bd["sebi_fee"], 2)
        
        p.pnl        = round(((exit_px - p.entry) * p.contracts) - p.charges, 2)
        
        del self.open[index][p.tid]
        self.closed.append(p)
        if index not in self.day_pnl:
            self.day_pnl[index] = 0.0
        self.day_pnl[index] += p.pnl
        self.cum_pnl += p.pnl
        if index not in self.capital:
            self.capital[index] = 0.0
        self.capital[index] += p.pnl
        self.total_capital += p.pnl
        
        tag = "WIN" if p.pnl > 0 else "LOSS"
        display_index = "ALGO_TRADING" if index == "NIFTY" else index
        log.info(f"\n{'='*55}")
        log.info(f"  {tag}  {p.tid} | User: {self.username}")
        log.info(f"  {reason} | Net PnL=Rs.{p.pnl:+,.0f}")
        log.info(f"{'='*55}\n")
        
        # Save updated position details to DB
        db_helper.save_position(self.user_id, p)
        # Update user's capital config in DB
        db_helper.update_user_config(self.user_id, {"capital": self.total_capital})
        
        try:
            smart_filter.train_model()
        except Exception as ex:
            log.warning(f"[Guard] Error updating stats: {ex}")

        # Alerts
        if p.pnl > 0:
            alert_win(p.tid, p.pnl, p.pnl_pct, p.exit_px, lots=p.lots, contracts=p.contracts)
        else:
            alert_loss(p.tid, p.pnl, p.pnl_pct, p.exit_px, reason, lots=p.lots, contracts=p.contracts)

        return p

    def summary(self) -> dict:
        if not self.closed:
            return {"message": "No trades yet"}
        pnls = [t.pnl for t in self.closed]
        wins = [p for p in pnls if p > 0]
        loss = [p for p in pnls if p <= 0]
        n    = len(pnls)
        aw   = float(np.mean(wins)) if wins else 0.0
        al   = float(np.mean(loss)) if loss else 0.0
        return {
            "trades":    n,
            "wins":      len(wins),
            "losses":    len(loss),
            "win_rate":  f"{len(wins)/n*100:.1f}%" if n > 0 else "0%",
            "total_pnl": f"Rs.{sum(pnls):+,.0f}",
            "avg_win":   f"Rs.{aw:+,.0f}",
            "avg_loss":  f"Rs.{al:+,.0f}",
            "rr":        f"{abs(aw/al):.2f}" if al else "N/A",
            "capital":   f"Rs.{self.total_capital:,.0f}",
        }

# ─── MAIN ──────────────────────────────────────────────────────────────────────
def run():
    global _running, vix
    check_and_clear_expired_credentials()
    db_helper.init_db()
    _start_dashboard()

    log.info("=" * 60)
    log.info("  ALGO PULSE - MULTI-USER OPTIONS TRADER")
    log.info("  Pullback entries + Dynamic SL/TP + Multi-TF confirmation")
    log.info(f"  Hours={MARKET_OPEN}–{MARKET_CLOSE}")
    log.info(f"  VIX Range: {VIX_MIN}-{VIX_MAX} | ATR Min: {ATR_MIN_PCT*100}%")
    log.info("=" * 60)

    vix   = 15.0
    vix_t = None
    _done = False
    
    global_last_scan_time = 0.0
    book_sync_times: Dict[int, float] = {}
    book_scan_times: Dict[int, float] = {}

    while _running:
        now_s = _now().strftime("%H:%M:%S")

        if _is_weekend():
            log.info("[MAIN] Weekend — closed")
            _sleep(3600); continue

        if _is_pre():
            log.info(f"[MAIN] {now_s} Pre-market")
            _sleep(60); continue

        active_ids = db_helper.get_active_user_ids()

        if _is_post():
            if not _done:
                for uid in active_ids:
                    try:
                        book = get_user_book(uid)
                        any_open = any(len(book.open[idx]) > 0 for idx in book.open)
                        if any_open:
                            book.exit_all("FORCE_EXIT_EOD")
                    except Exception as e:
                        log.error(f"[MAIN] EOD post-market close failed for user {uid}: {e}")
                _done = True
            _sleep(60); _done = False; continue

        _done = False

        if vix_t is None or (_now() - vix_t).total_seconds() > 30:
            threading.Thread(target=_refresh_vix_cache, daemon=True).start()
            vix_t = _now()
        vix = _get_cached_vix()

        # Find all active indices across all active users
        active_indices = set()
        for uid in active_ids:
            try:
                book = get_user_book(uid)
                for idx in book.trading_indices:
                    active_indices.add(idx)
            except Exception as e:
                log.error(f"[MAIN] Error initializing user {uid} index set: {e}")

        # Fetch spot prices once globally
        spots = {}
        for idx in active_indices:
            try:
                spots[idx] = _get_nse_spot(idx)
            except Exception as e:
                log.debug(f"[MAIN] Error getting spot for {idx}: {e}")
                spots[idx] = 0.0

        # Scan signals once globally for the active indices every 30 seconds
        now_time = time.time()
        signals = {}
        if _in_session() and (now_time - global_last_scan_time >= 30):
            global_last_scan_time = now_time
            for idx in active_indices:
                try:
                    sig = _signal(idx)
                    if sig:
                        signals[idx] = sig
                except Exception as e:
                    log.error(f"[MAIN] Signal generation failed for {idx}: {e}")

        # Print unified console summary
        total_open_positions = 0
        active_usernames = []
        for uid in active_ids:
            try:
                book = get_user_book(uid)
                total_open_positions += sum(len(book.open[idx]) for idx in book.open)
                active_usernames.append(book.username)
            except:
                pass
                
        spots_str = " | ".join([f"{idx}={val:.0f}" for idx, val in spots.items() if val > 0])
        users_str = ", ".join(active_usernames) if active_usernames else "None"
        if active_ids:
            log.info(
                f"[MAIN] {now_s} | {spots_str or 'Waiting for market...'} | VIX={vix:.1f} | "
                f"Active Users: {users_str} | Open Pos: {total_open_positions}"
            )

        # Loop through users and execute respective trading cycles
        for uid in active_ids:
            try:
                book = get_user_book(uid)
                
                # Periodically sync positions (every 30 seconds)
                if book.live_trading and (now_time - book_sync_times.get(uid, 0.0) >= 30):
                    book_sync_times[uid] = now_time
                    book.sync_live_positions()
                    
                # Run MTM re-pricing
                book_open_count = sum(len(book.open[idx]) for idx in book.open)
                if book_open_count > 0:
                    book.mtm()
                    
                # End of Day force closure
                if _now_hm() >= _hm(MARKET_CLOSE):
                    if book_open_count > 0:
                        book.exit_all(f"FORCE_EXIT_{MARKET_CLOSE}")
                        
                # Evaluate Entry Signals
                if _in_session() and (now_time - book_scan_times.get(uid, 0.0) >= 30):
                    book_scan_times[uid] = now_time
                    for idx in book.trading_indices:
                        if (len(book.open[idx]) < book.max_open_per_index and
                            book.day_trades[idx] < book.max_daily_trades_per_index):
                            sig = signals.get(idx)
                            if sig:
                                book.enter(sig)
            except Exception as uid_err:
                log.error(f"[MAIN] Exception during trading loop for user ID {uid}: {uid_err}")

        # Sleep duration: 2s if there are active positions, else 10s
        sleep_dur = 2 if total_open_positions > 0 else 10
        _sleep(sleep_dur)

    active_ids = db_helper.get_active_user_ids()
    for uid in active_ids:
        try:
            book = get_user_book(uid)
            book.exit_all("SHUTDOWN")
            _show(book)
        except Exception as e:
            log.error(f"[MAIN] Shutdown exit failed for user ID {uid}: {e}")

def _show(book: Book):
    log.info("\n" + "=" * 60)
    log.info(f"  SESSION SUMMARY FOR {book.username}")
    log.info("=" * 60)
    log.info(f"  Total Capital: Rs.{book.total_capital:,.0f}")
    for idx in list(book.capital.keys()):
        display_idx = "ALGO TRADING" if idx == "NIFTY" else idx
        cap_val = book.capital.get(idx, 0.0)
        pnl_val = book.day_pnl.get(idx, 0.0)
        log.info(f"  {display_idx} Capital: Rs.{cap_val:,.0f} | DayPnL: Rs.{pnl_val:+,.0f}")
    log.info(f"  Cumulative PnL: Rs.{book.cum_pnl:+,.0f}")
    log.info(f"  Total Trades: {sum(book.day_trades.values())}")
    log.info("=" * 60 + "\n")

# ─── DASHBOARD ROUTES & HTML ──────────────────────────────────────────────────
# MUST be defined BEFORE if __name__ == "__main__" so Flask registers them
# before run() is called (run() is a blocking loop).

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ALGO PULSE | Multi-Index Option Trader</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#030712;
  --s:rgba(17, 24, 39, 0.65);
  --card:rgba(22, 28, 45, 0.6);
  --border:rgba(255, 255, 255, 0.08);
  --green:#00ff88;
  --red:#ff007f;
  --blue:#00f0ff;
  --gold:#ffe600;
  --text:#f3f4f6;
  --muted:#718096;
  --accent:#9d4edd;
  --pink:#ff00e4;
}
body{
  background:var(--bg);
  color:var(--text);
  font-family:'Inter',sans-serif;
  min-height:100vh;
  position:relative;
  overflow-x:hidden;
}
/* Cyber Ambient Glimmer Background */
body::before {
  content: '';
  position: fixed;
  top: -20%;
  left: -20%;
  width: 140%;
  height: 140%;
  background: radial-gradient(circle at 15% 20%, rgba(157, 78, 221, 0.22) 0%, transparent 45%),
              radial-gradient(circle at 85% 80%, rgba(0, 240, 255, 0.18) 0%, transparent 50%),
              radial-gradient(circle at 50% 50%, rgba(255, 0, 127, 0.15) 0%, transparent 55%);
  z-index: -1;
  pointer-events: none;
  animation: floatBg 25s ease-in-out infinite alternate;
}
@keyframes floatBg {
  0% { transform: translate(0,0) scale(1); }
  100% { transform: translate(-3%, -3%) scale(1.06); }
}
.hdr{
  background:rgba(9, 13, 26, 0.8);
  backdrop-filter:blur(20px);
  -webkit-backdrop-filter:blur(20px);
  border-bottom:1px solid var(--border);
  padding:14px 28px;
  display:flex;
  align-items:center;
  justify-content:space-between;
  position:sticky;
  top:0;
  z-index:100;
  box-shadow:0 10px 30px rgba(0,0,0,0.5);
}
.hdr-left{display:flex;align-items:center;gap:12px}
.logo-container {
  position: relative;
  width: 40px;
  height: 40px;
  perspective: 300px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.logo-3d {
  width: 26px;
  height: 26px;
  position: relative;
  transform-style: preserve-3d;
  animation: rotateLogo 12s linear infinite;
}
.cube-face {
  position: absolute;
  width: 26px;
  height: 26px;
  background: rgba(13, 17, 28, 0.9);
  border: 1.5px solid var(--accent);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  box-shadow: 0 0 10px rgba(157, 78, 221, 0.4), inset 0 0 6px rgba(0, 240, 255, 0.3);
  backdrop-filter: blur(2px);
  user-select: none;
  backface-visibility: visible;
}
.face-front  { transform: rotateY(  0deg) translateZ(13px); border-color: var(--pink); box-shadow: 0 0 10px rgba(255, 0, 228, 0.5); }
.face-back   { transform: rotateY(180deg) translateZ(13px); border-color: var(--blue); box-shadow: 0 0 10px rgba(0, 240, 255, 0.5); }
.face-right  { transform: rotateY( 90deg) translateZ(13px); border-color: var(--green); box-shadow: 0 0 10px rgba(0, 255, 136, 0.5); }
.face-left   { transform: rotateY(-90deg) translateZ(13px); border-color: var(--gold); box-shadow: 0 0 10px rgba(255, 230, 0, 0.5); }
.face-top    { transform: rotateX( 90deg) translateZ(13px); border-color: var(--accent); box-shadow: 0 0 10px rgba(157, 78, 221, 0.5); }
.face-bottom { transform: rotateX(-90deg) translateZ(13px); border-color: #ff007f; box-shadow: 0 0 10px rgba(255, 0, 127, 0.5); }

.neon-reflection {
  position: absolute;
  width: 60px;
  height: 60px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255, 0, 228, 0.35) 0%, rgba(0, 240, 255, 0.15) 40%, transparent 70%);
  filter: blur(8px);
  z-index: -1;
  pointer-events: none;
  animation: reflectPulse 6s ease-in-out infinite;
}
@keyframes rotateLogo {
  0% { transform: rotateX(0deg) rotateY(0deg) rotateZ(0deg); }
  100% { transform: rotateX(360deg) rotateY(360deg) rotateZ(360deg); }
}
@keyframes reflectPulse {
  0%, 100% {
    transform: scale(0.85);
    background: radial-gradient(circle, rgba(255, 0, 228, 0.35) 0%, rgba(0, 240, 255, 0.15) 45%, transparent 75%);
  }
  50% {
    transform: scale(1.15);
    background: radial-gradient(circle, rgba(0, 240, 255, 0.45) 0%, rgba(255, 0, 228, 0.2) 45%, transparent 75%);
  }
}
.hdr-title{
  font-size:17px;
  font-weight:800;
  background: linear-gradient(90deg, #00f0ff 0%, #ff00e4 50%, #ffe600 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  text-shadow: 0 0 20px rgba(255, 0, 228, 0.15);
}
.hdr-sub{font-size:10px;color:var(--muted);font-weight:600;letter-spacing:0.5px;}
.live-badge{
  display:flex;
  align-items:center;
  gap:8px;
  background:rgba(0, 255, 136, 0.08);
  border:1px solid rgba(0, 255, 136, 0.25);
  border-radius:20px;
  padding:6px 14px;
  font-size:11px;
  font-weight:700;
  color:var(--green);
  box-shadow:0 0 10px rgba(0, 255, 136, 0.08);
}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1);}50%{opacity:.3;transform:scale(1.2);}}
.wrap{max-width:1600px;margin:0 auto;padding:20px 28px}
.ticker{
  background:var(--s);
  backdrop-filter:blur(10px);
  -webkit-backdrop-filter:blur(10px);
  border:1px solid var(--border);
  border-radius:12px;
  padding:14px 24px;
  display:flex;
  gap:0;
  margin-bottom:20px;
  flex-wrap:wrap;
  box-shadow:0 8px 32px rgba(0,0,0,0.3);
}
.t-item{display:flex;flex-direction:column;gap:4px;padding:0 24px;border-right:1px solid var(--border)}
.t-item:first-child{padding-left:0}
.t-item:last-child{border-right:none}
.t-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;font-weight:700}
.t-val{font-size:20px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.t-item:nth-child(1) .t-val{color:var(--blue);text-shadow:0 0 10px rgba(0,240,255,0.3)}
.t-item:nth-child(2) .t-val{text-shadow:0 0 10px rgba(255,255,255,0.05)}
.t-item:nth-child(3) .t-val{color:var(--gold);text-shadow:0 0 10px rgba(255,230,0,0.3)}
.t-item:nth-child(4) .t-val{text-shadow:0 0 10px rgba(255,255,255,0.05)}

.metrics{display:grid;grid-template-columns:repeat(8,1fr);gap:14px;margin-bottom:20px}
.mc{
  background:var(--card);
  backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);
  border:1px solid var(--border);
  border-radius:12px;
  padding:18px;
  position:relative;
  overflow:hidden;
  box-shadow:0 8px 24px rgba(0,0,0,0.35);
  transition:all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.mc:hover{
  transform:translateY(-4px);
}
.mc.blue:hover { border-color: var(--blue); box-shadow: 0 10px 25px rgba(0, 240, 255, 0.25); }
.mc.purple:hover { border-color: var(--pink); box-shadow: 0 10px 25px rgba(255, 0, 228, 0.3); }
.mc.green:hover { border-color: var(--green); box-shadow: 0 10px 25px rgba(0, 255, 136, 0.25); }
.mc.gold:hover { border-color: var(--gold); box-shadow: 0 10px 25px rgba(255, 230, 0, 0.25); }
.mc.red:hover { border-color: var(--red); box-shadow: 0 10px 25px rgba(255, 0, 127, 0.3); }
.mc.teal:hover { border-color: #00ffcc; box-shadow: 0 10px 25px rgba(0, 255, 204, 0.25); }
.mc.orange:hover { border-color: #ff8c00; box-shadow: 0 10px 25px rgba(255, 140, 0, 0.25); }
.mc::before{content:'';position:absolute;top:0;left:0;right:0;height:4px}
.mc.blue::before{background:linear-gradient(90deg, var(--blue), #0072ff);}.mc.green::before{background:linear-gradient(90deg, var(--green), #00b300);}.mc.red::before{background:linear-gradient(90deg, var(--red), #ff0033);}.mc.gold::before{background:linear-gradient(90deg, var(--gold), #ff8000);}.mc.purple::before{background:linear-gradient(90deg, var(--pink), var(--accent));}.mc.teal::before{background:linear-gradient(90deg, #00ffcc, #00a896);}.mc.orange::before{background:linear-gradient(90deg, #ff8c00, #ff4500);}
.mc-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;font-weight:700;margin-bottom:8px}
.mc-val{font-size:22px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.mc.blue .mc-val{color:var(--blue);text-shadow:0 0 10px rgba(0,240,255,0.25);}
.mc.purple .mc-val{color:#f5d0ff;text-shadow:0 0 10px rgba(255,0,228,0.25);}
.mc.green .mc-val{color:var(--green);text-shadow:0 0 10px rgba(0,255,136,0.25);}
.mc.gold .mc-val{color:var(--gold);text-shadow:0 0 10px rgba(255,230,0,0.25);}
.mc.red .mc-val{color:var(--red);text-shadow:0 0 10px rgba(255,0,127,0.25);}
.mc.teal .mc-val{color:#00ffcc;text-shadow:0 0 10px rgba(0,255,204,0.25);}
.mc.orange .mc-val{color:#ff9f43;text-shadow:0 0 10px rgba(255,140,0,0.25);}

.mc-sub{font-size:11px;color:var(--muted);margin-top:5px;font-weight:600;}
.charts{display:grid;grid-template-columns:1.4fr 1fr;gap:16px;margin-bottom:20px}
.chart-box{height:300px;position:relative;width:100%}

.eq-glow{position:absolute;inset:0;border-radius:10px;pointer-events:none;background:radial-gradient(ellipse at 50% 110%,rgba(0,255,136,.18) 0%,transparent 70%);animation:glowPulse 3s ease-in-out infinite}
@keyframes glowPulse{0%,100%{opacity:.6}50%{opacity:1}}
#eqSpark{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;border-radius:10px}
.bottom{display:grid;grid-template-columns:1fr;gap:16px}

.index-break{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;margin-bottom:24px}
@media(max-width:980px){
  .index-break {
    grid-template-columns: 1fr;
  }
}
.idx-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px}
.idx-title{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1px}
.idx-title.nifty{color:var(--blue);text-shadow:0 0 6px rgba(0,240,255,0.25)}
.idx-title.sensex{color:var(--gold);text-shadow:0 0 6px rgba(255,230,0,0.25)}
.idx-pnl{font-size:13px;font-weight:700;font-family:'JetBrains Mono',monospace;white-space:nowrap}
.card.nifty-card{border-color:rgba(0,240,255,.22);background:rgba(0,240,255,0.015)}
.card.sensex-card{border-color:rgba(255,230,0,.22);background:rgba(255,230,0,0.015)}
.table-scroll{max-height:260px;overflow-y:auto;overflow-x:auto;scrollbar-width:thin;scrollbar-color:var(--accent) transparent}
.table-scroll::-webkit-scrollbar{width:6px}
.table-scroll::-webkit-scrollbar-track{background:transparent}
.table-scroll::-webkit-scrollbar-thumb{background:rgba(157,78,221,0.4);border-radius:10px}
.table-scroll::-webkit-scrollbar-thumb:hover{background:rgba(157,78,221,0.8)}
.table-scroll th{position:sticky;top:0;background:#0d1222;z-index:10;box-shadow:inset 0 -1px 0 rgba(255,255,255,0.08)}
.index-table-scroll{max-height:148px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--accent) transparent}
.index-table-scroll::-webkit-scrollbar{width:6px}
.index-table-scroll::-webkit-scrollbar-track{background:transparent}
.index-table-scroll::-webkit-scrollbar-thumb{background:rgba(157,78,221,0.4);border-radius:10px}
.index-table-scroll::-webkit-scrollbar-thumb:hover{background:rgba(157,78,221,0.8)}
.index-table-scroll th{position:sticky;top:0;background:#0d1222;z-index:10;box-shadow:inset 0 -1px 0 rgba(255,255,255,0.08)}
table{width:100%;border-collapse:collapse}
thead tr{border-bottom:1px solid var(--border)}
th{padding:8px 10px;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.2px;text-align:left;font-weight:800}
tbody tr{border-bottom:1px solid rgba(255,255,255,.04);transition:background 0.2s ease;}
tbody tr:hover{background:rgba(255,255,255,.03)}
td{padding:6px 10px;font-size:10.5px;font-family:'JetBrains Mono',monospace;color:#e2e8f0}
.empty{color:var(--muted);text-align:center;padding:32px;font-size:12px;}
.badge{display:inline-block;padding:3px 8px;border-radius:4px;font-size:10px;font-weight:800;letter-spacing:0.5px;}
.badge.call{background:rgba(0,240,255,0.08);border:1px solid rgba(0,240,255,0.2);color:var(--blue)}.badge.put{background:rgba(255,0,127,0.08);border:1px solid rgba(255,0,127,0.2);color:var(--red)}
.g{color:var(--green);text-shadow:0 0 5px rgba(0,255,136,0.15)}.r{color:var(--red);text-shadow:0 0 5px rgba(255,0,127,0.15)}.m{color:var(--muted)}
.term{
  background:rgba(5, 7, 13, 0.85);
  backdrop-filter:blur(10px);
  -webkit-backdrop-filter:blur(10px);
  border:1px solid var(--border);
  border-radius:10px;
  padding:16px;
  height:280px;
  overflow-y:auto;
  font-family:'JetBrains Mono',monospace;
  font-size:11.5px;
  line-height:1.75;
  box-shadow:inset 0 4px 20px rgba(0,0,0,0.8);
  color:#cbd5e1;
}
.term::-webkit-scrollbar{width:4px}.term::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.1);border-radius:2px;}
.ll{white-space:pre-wrap;word-break:break-all}.ll.win{color:var(--green)}.ll.loss{color:var(--red)}
.refresh-bar{
  position:fixed;
  bottom:16px;
  right:20px;
  background:rgba(17, 24, 39, 0.85);
  backdrop-filter:blur(8px);
  border:1px solid var(--border);
  border-radius:16px;
  padding:6px 14px;
  font-size:11px;
  color:var(--muted);
  font-weight:600;
  box-shadow:0 4px 15px rgba(0,0,0,0.3);
}
@media(max-width:1100px){.metrics{grid-template-columns:repeat(3,1fr)}.charts,.bottom{grid-template-columns:1fr}}
@media(max-width:768px){.metrics{grid-template-columns:repeat(2,1fr);gap:10px}.mc{padding:12px}.mc-val{font-size:17px}.mc-lbl{font-size:9px;margin-bottom:6px}.mc-sub{font-size:10px;margin-top:4px}}
@media(max-width:400px){.metrics{grid-template-columns:repeat(2,1fr);gap:8px}.mc{padding:10px}.mc-val{font-size:14px}.mc-sub{font-size:9px}}

.stop-btn {
  background: linear-gradient(135deg, #ff0055 0%, #aa0033 100%);
  color: #fff;
  border: 1px solid rgba(255, 0, 85, 0.4);
  border-radius: 20px;
  padding: 8px 18px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 0 12px rgba(255, 0, 85, 0.25);
}
.stop-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 0 20px rgba(255, 0, 85, 0.5);
  background: linear-gradient(135deg, #ff3377 0%, #cc0044 100%);
}
.stop-btn:active {
  transform: translateY(1px);
}
.stop-btn.disabled {
  background: var(--s);
  color: var(--muted);
  border-color: var(--border);
  box-shadow: none;
  cursor: not-allowed;
  opacity: 0.6;
}
.reset-btn {
  background: linear-gradient(135deg, #37474f 0%, #1e252b 100%);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 8px 18px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.3);
}
.reset-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 0 15px rgba(255, 255, 255, 0.08);
  background: linear-gradient(135deg, #455a64 0%, #2c383f 100%);
  border-color: rgba(255,255,255,0.2);
}
.reset-btn:active {
  transform: translateY(1px);
}
.download-btn {
  background: linear-gradient(135deg, #00b0ff 0%, #0072ff 100%);
  color: #000;
  border: none;
  border-radius: 20px;
  padding: 8px 18px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 0 10px rgba(0, 176, 255, 0.2);
}
.download-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 0 15px rgba(0, 176, 255, 0.4);
  filter: brightness(1.1);
}
.download-btn:active {
  transform: translateY(1px);
}
.start-btn {
  background: linear-gradient(135deg, var(--green) 0%, var(--blue) 100%);
  color: #030712;
  border: 1px solid rgba(0, 255, 136, 0.4);
  border-radius: 20px;
  padding: 8px 18px;
  font-size: 11px;
  font-weight: 800;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 0 12px rgba(0, 255, 136, 0.3);
}
.start-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 0 20px rgba(0, 255, 136, 0.6);
  background: linear-gradient(135deg, #33ffa3 0%, #33f0ff 100%);
}
.start-btn:active {
  transform: translateY(1px);
}
.start-btn.disabled {
  background: var(--s);
  color: var(--muted);
  border-color: var(--border);
  box-shadow: none;
  cursor: not-allowed;
  opacity: 0.6;
}
.pulse-badge {
  background: rgba(0, 255, 136, 0.1);
  color: var(--green);
  border: 1px solid rgba(0, 255, 136, 0.25);
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 700;
  animation: bgPulse 2s infinite;
}
@keyframes bgPulse {
  0%, 100% { background: rgba(0, 255, 136, 0.1); box-shadow: 0 0 5px rgba(0,255,136,0.1); }
  50% { background: rgba(0, 255, 136, 0.25); box-shadow: 0 0 12px rgba(0,255,136,0.3); }
}
.open-pos-card {
  border: 1px solid rgba(0, 255, 136, 0.35);
  box-shadow: 0 0 20px rgba(0, 255, 136, 0.08);
  background: rgba(0, 255, 136, 0.015);
}

/* iOS Toggle Switch Styles */
.switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
}
.switch input { 
  opacity: 0;
  width: 0;
  height: 0;
}
.slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: #1f2937;
  border: 1px solid var(--border);
  transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
}
.slider:before {
  position: absolute;
  content: "";
  height: 16px;
  width: 16px;
  left: 3px;
  bottom: 3px;
  background-color: #9ca3af;
  transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
  border-radius: 50%;
  box-shadow: 0 1px 3px rgba(0,0,0,0.4);
}
input:checked + .slider {
  background-color: rgba(124, 77, 255, 0.25);
  border-color: rgba(124, 77, 255, 0.6);
  box-shadow: 0 0 10px rgba(124, 77, 255, 0.3);
}
input:checked + .slider:before {
  transform: translateX(20px);
  background-color: #a78bfa;
  box-shadow: 0 0 8px #a78bfa;
}
.slider.round {
  border-radius: 24px;
}

/* Mode Selector Styles */
.mode-selector {
  display: flex;
  background: rgba(13, 17, 28, 0.8);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 3px;
  gap: 3px;
  box-shadow: inset 0 1px 4px rgba(0,0,0,0.5);
}
.mode-btn {
  background: transparent;
  color: var(--muted);
  border: none;
  padding: 9px 18px;
  border-radius: 22px;
  font-size: 12.5px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  align-items: center;
  gap: 6px;
}
.mode-btn:hover {
  color: var(--text);
}
.mode-btn.active#demoModeBtn {
  background: linear-gradient(135deg, rgba(0, 240, 255, 0.25) 0%, rgba(0, 114, 255, 0.25) 100%);
  color: var(--blue);
  border: 1px solid rgba(0, 240, 255, 0.4);
  box-shadow: 0 0 16px rgba(0, 240, 255, 0.3);
}
.mode-btn.active#liveModeBtn {
  background: linear-gradient(135deg, rgba(255, 0, 127, 0.25) 0%, rgba(255, 94, 0, 0.25) 100%);
  color: #ff007f;
  border: 1px solid rgba(255, 0, 127, 0.4);
  box-shadow: 0 0 24px rgba(255, 0, 127, 0.45);
  animation: livePulse 2s infinite;
}
@keyframes livePulse {
  0%, 100% { box-shadow: 0 0 16px rgba(255, 0, 127, 0.3); }
  50% { box-shadow: 0 0 32px rgba(255, 0, 127, 0.65); }
}
/* Broker Selector Styles */
.broker-selector {
  display: flex;
  background: rgba(13, 17, 28, 0.8);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 3px;
  gap: 3px;
  box-shadow: inset 0 1px 4px rgba(0,0,0,0.5);
}
.broker-btn {
  background: transparent;
  color: var(--muted);
  border: none;
  padding: 9px 18px;
  border-radius: 22px;
  font-size: 12.5px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  align-items: center;
  gap: 8px;
}
.broker-icon {
  width: 18px;
  height: 18px;
  display: inline-block;
  vertical-align: middle;
  transition: transform 0.2s ease;
  flex-shrink: 0;
}
.broker-btn:hover .broker-icon {
  transform: scale(1.15) rotate(5deg);
}
.broker-btn:hover {
  color: var(--text);
}
.broker-btn.active#dhanBrokerBtn {
  background: linear-gradient(135deg, rgba(0, 255, 136, 0.2) 0%, rgba(0, 229, 255, 0.2) 100%);
  color: var(--green);
  border: 1px solid rgba(0, 255, 136, 0.4);
  box-shadow: 0 0 12px rgba(0, 255, 136, 0.25);
}
.broker-btn.active#growwBrokerBtn {
  background: linear-gradient(135deg, rgba(157, 78, 221, 0.25) 0%, rgba(255, 0, 228, 0.25) 100%);
  color: #d68aff;
  border: 1px solid rgba(157, 78, 221, 0.4);
  box-shadow: 0 0 12px rgba(157, 78, 221, 0.25);
}

/* Premium Glassmorphism Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(3, 7, 18, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.3s ease;
}
.modal-overlay.active {
  opacity: 1;
  pointer-events: auto;
}
.modal-container {
  background: rgba(22, 28, 45, 0.95);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 32px;
  width: 480px;
  max-width: 90%;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6), inset 0 0 15px rgba(0, 240, 255, 0.1);
  transform: translateY(-30px) scale(0.95);
  transition: transform 0.3s cubic-bezier(0.18, 0.89, 0.32, 1.28);
  color: var(--text);
}
.modal-overlay.active .modal-container {
  transform: translateY(0) scale(1);
}
.modal-hdr {
  font-size: 20px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.modal-hdr span {
  background: linear-gradient(135deg, var(--green) 0%, var(--blue) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.modal-desc {
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 24px;
  line-height: 1.5;
}
.form-group {
  margin-bottom: 18px;
}
.form-group label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  margin-bottom: 6px;
}
.form-group input {
  width: 100%;
  background: rgba(9, 13, 26, 0.7);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.form-group input:focus {
  outline: none;
  border-color: var(--blue);
  box-shadow: 0 0 10px rgba(0, 240, 255, 0.25);
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 28px;
}
.modal-btn-cancel {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 18px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
}
.modal-btn-cancel:hover {
  background: rgba(255,255,255,0.05);
  color: var(--text);
}
.modal-btn-connect {
  background: linear-gradient(135deg, var(--green) 0%, var(--blue) 100%);
  border: none;
  border-radius: 8px;
  padding: 10px 24px;
  color: #030712;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
  box-shadow: 0 0 15px rgba(0, 255, 136, 0.35);
}
.modal-btn-connect:hover {
  transform: translateY(-1px);
  box-shadow: 0 0 20px rgba(0, 255, 136, 0.5);
}
.modal-btn-connect:active {
  transform: translateY(0);
}
.modal-btn-connect:disabled {
  background: var(--border);
  color: var(--muted);
  box-shadow: none;
  cursor: not-allowed;
  transform: none;
}

/* Indices Multi-Select Dropdown Styles */
.indices-dropdown {
  position: relative;
  display: inline-block;
}
.dropdown-btn {
  background: rgba(13, 17, 28, 0.85);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 8px 16px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 0 10px rgba(0,0,0,0.3);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.dropdown-btn:hover {
  border-color: rgba(0, 240, 255, 0.4);
  box-shadow: 0 0 12px rgba(0, 240, 255, 0.15);
  transform: translateY(-1px);
}
.dropdown-btn .chevron {
  font-size: 8px;
  color: var(--muted);
  transition: transform 0.3s ease;
}
.indices-dropdown.active .dropdown-btn .chevron {
  transform: rotate(180deg);
  color: var(--blue);
}
.dropdown-content {
  display: none;
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 10px;
  min-width: 220px;
  background: rgba(6, 8, 18, 0.98);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid rgba(0, 240, 255, 0.35);
  border-radius: 16px;
  padding: 12px 0;
  z-index: 100;
  box-shadow: 0 15px 45px rgba(0,0,0,0.85), 
              0 0 20px rgba(0, 240, 255, 0.25), 
              0 0 40px rgba(255, 0, 127, 0.15);
  animation: dropdownFadeIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  overflow: hidden;
}
@keyframes dropdownFadeIn {
  from { opacity: 0; transform: translateY(-8px) scale(0.96); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.dropdown-content.show {
  display: block;
}
.dropdown-content label {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 20px;
  font-size: 13px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  cursor: pointer;
  color: var(--muted);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  user-select: none;
  letter-spacing: 0.5px;
}
.dropdown-content label:hover {
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-color) !important;
  text-shadow: 0 0 10px var(--glow-color);
}
.dropdown-content label:has(input:checked) {
  color: var(--text-color) !important;
  text-shadow: 0 0 10px var(--glow-color);
}
.dropdown-content label:has(input:checked) span {
  color: var(--text-color) !important;
  text-shadow: 0 0 10px var(--glow-color);
}
.dropdown-content input[type="checkbox"] {
  appearance: none;
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.25);
  border-radius: 6px;
  outline: none;
  background: rgba(255, 255, 255, 0.02);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
}
.dropdown-content label:hover input[type="checkbox"] {
  border-color: var(--glow-color);
  box-shadow: 0 0 8px var(--glow-color);
}
.dropdown-content input[type="checkbox"]:checked {
  background: var(--glow-color);
  border-color: var(--glow-color);
  box-shadow: 0 0 12px var(--glow-color);
}
.dropdown-content input[type="checkbox"]:checked::after {
  content: "✔";
  color: #030712;
  font-size: 11px;
  font-weight: 900;
}

/* Card Container Styling */
.card {
  background: var(--card);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 22px 24px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  flex-direction: column;
}
.card:hover {
  border-color: rgba(255, 255, 255, 0.15);
  box-shadow: 0 10px 30px rgba(0,0,0,0.45);
}

/* Card Header/Title Gap Styling */
.card-title {
  font-size: 13.5px;
  font-weight: 800;
  color: var(--text);
  text-transform: uppercase;
  letter-spacing: 1.2px;
  margin-bottom: 22px; /* Perfect spacing gap below the text to separate it from tables and charts! */
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* Premium Chart Selector Tabs */
.chart-tabs {
  display: flex;
  gap: 8px;
  background: rgba(9, 13, 26, 0.6);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 3px;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.chart-tab {
  background: transparent;
  border: none;
  border-radius: 16px;
  padding: 5px 14px;
  color: var(--muted);
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  outline: none;
}
.chart-tab.active {
  background: rgba(0, 240, 255, 0.15);
  color: var(--blue);
  text-shadow: 0 0 8px rgba(0, 240, 255, 0.5);
  box-shadow: 0 0 10px rgba(0, 240, 255, 0.1);
}
.chart-tab:hover:not(.active) {
  color: var(--text);
  background: rgba(255, 255, 255, 0.04);
}
.squareoff-btn {
  background: linear-gradient(135deg, #ff9f43 0%, #ff6b6b 100%);
  color: #030712;
  border: 1px solid rgba(255, 159, 67, 0.4);
  border-radius: 20px;
  padding: 8px 18px;
  font-size: 11px;
  font-weight: 800;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 0 12px rgba(255, 159, 67, 0.3);
}
.squareoff-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 0 20px rgba(255, 159, 67, 0.6);
  background: linear-gradient(135deg, #ffb366 0%, #ff8585 100%);
}
.squareoff-btn:active {
  transform: translateY(1px);
}

/* Auth Screen Styles */
#authApp {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  padding: 20px;
  position: relative;
  z-index: 10;
}
.auth-card {
  background: var(--card);
  backdrop-filter: blur(25px);
  -webkit-backdrop-filter: blur(25px);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 40px;
  width: 100%;
  max-width: 440px;
  box-shadow: 0 20px 50px rgba(0,0,0,0.5), inset 0 0 20px rgba(157, 78, 221, 0.1);
  position: relative;
  overflow: hidden;
}
.auth-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 4px;
  background: linear-gradient(90deg, var(--pink), var(--accent), var(--blue));
}
.auth-title {
  font-size: 24px;
  font-weight: 700;
  text-align: center;
  margin-bottom: 8px;
  color: var(--text);
  text-shadow: 0 0 10px rgba(255, 255, 255, 0.1);
}
.auth-sub {
  font-size: 13px;
  color: var(--muted);
  text-align: center;
  margin-bottom: 30px;
}
.auth-tabs {
  display: flex;
  background: rgba(0, 0, 0, 0.25);
  border-radius: 10px;
  padding: 4px;
  margin-bottom: 25px;
  border: 1px solid rgba(255, 255, 255, 0.03);
}
.auth-tab {
  flex: 1;
  background: transparent;
  border: none;
  border-radius: 8px;
  padding: 10px 0;
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
}
.auth-tab.active {
  background: var(--accent);
  color: #fff;
  box-shadow: 0 0 15px rgba(157, 78, 221, 0.5);
}
.auth-group {
  margin-bottom: 20px;
  position: relative;
}
.auth-group label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
  margin-bottom: 8px;
}
.auth-group input {
  width: 100%;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 16px;
  color: var(--text);
  font-family: inherit;
  font-size: 14px;
  transition: all 0.3s ease;
}
.auth-group input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 12px rgba(157, 78, 221, 0.25);
  background: rgba(0, 0, 0, 0.45);
}
.auth-btn {
  width: 100%;
  background: linear-gradient(135deg, var(--accent), var(--pink));
  border: none;
  border-radius: 10px;
  padding: 14px;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 20px rgba(157, 78, 221, 0.4);
  margin-top: 10px;
}
.auth-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 30px rgba(255, 0, 228, 0.6);
  filter: brightness(1.1);
}
.auth-btn:active {
  transform: none;
}
.auth-alert {
  padding: 12px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 20px;
  display: none;
}
.auth-alert.error {
  background: rgba(255, 0, 127, 0.1);
  border: 1px solid rgba(255, 0, 127, 0.25);
  color: var(--red);
}
.auth-alert.success {
  background: rgba(0, 255, 136, 0.1);
  border: 1px solid rgba(0, 255, 136, 0.25);
  color: var(--green);
}
</style>
</head>
<body>
<div id="authApp" style="display:flex;">
  <div class="auth-card">
    <div class="logo-container" style="margin: 0 auto 20px auto;">
      <div class="neon-reflection"></div>
      <div class="logo-3d">
        <div class="cube-face face-front">⚡</div>
        <div class="cube-face face-back">⚡</div>
        <div class="cube-face face-right">⚡</div>
        <div class="cube-face face-left">⚡</div>
        <div class="cube-face face-top">⚡</div>
        <div class="cube-face face-bottom">⚡</div>
      </div>
    </div>
    <div class="auth-title">⚡ ALGO PULSE</div>
    <div class="auth-sub">Connect and automate your trading portfolios</div>
    
    <div class="auth-alert error" id="authErrorAlert"></div>
    <div class="auth-alert success" id="authSuccessAlert"></div>
    
    <div class="auth-tabs">
      <button class="auth-tab active" id="loginTabBtn" onclick="switchAuthTab('login')">Login</button>
      <button class="auth-tab" id="signupTabBtn" onclick="switchAuthTab('signup')">Sign Up</button>
    </div>
    
    <form id="authForm" onsubmit="handleAuthSubmit(event)">
      <div class="auth-group">
        <label for="authUsername">Username</label>
        <input type="text" id="authUsername" required placeholder="Enter username">
      </div>
      <div class="auth-group">
        <label for="authPassword">Password</label>
        <input type="password" id="authPassword" required placeholder="Enter password">
      </div>
      <button class="auth-btn" id="authSubmitBtn" type="submit">Sign In</button>
    </form>
  </div>
</div>

<div id="dashboardApp" style="display:none;">
<div class="hdr">
  <div class="hdr-left">
    <div class="logo-container">
      <div class="neon-reflection"></div>
      <div class="logo-3d">
        <div class="cube-face face-front">⚡</div>
        <div class="cube-face face-back">⚡</div>
        <div class="cube-face face-right">⚡</div>
        <div class="cube-face face-left">⚡</div>
        <div class="cube-face face-top">⚡</div>
        <div class="cube-face face-bottom">⚡</div>
      </div>
    </div>
    <div>
      <div class="hdr-title">ALGO PULSE</div>
      <div class="hdr-sub" id="hdrSub">DEMO · REAL-TIME PAPER TRADING</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <span style="font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace" id="luTime"></span>
    <div class="mode-selector">
      <button id="demoModeBtn" class="mode-btn active" onclick="switchMode('DEMO')">🎮 DEMO</button>
      <button id="liveModeBtn" class="mode-btn" onclick="switchMode('LIVE')">⚡ LIVE</button>
    </div>
    <!-- Multi-select Dropdown for indices -->
    <div class="indices-dropdown" id="indicesDropdown">
      <button class="dropdown-btn" onclick="toggleIndicesDropdown(event)">
        <span>📈 INDICES (2)</span>
        <span class="chevron">▼</span>
      </button>
      <div class="dropdown-content" id="indicesDropdownContent">
        <label style="--glow-color: #00f0ff; --text-color: #00f0ff;"><input type="checkbox" value="NIFTY" checked onchange="handleIndexChange()"> <span>⬡ NIFTY</span></label>
        <label style="--glow-color: #ffe600; --text-color: #ffe600;"><input type="checkbox" value="SENSEX" checked onchange="handleIndexChange()"> <span>⬡ SENSEX</span></label>
        <label style="--glow-color: #ff007f; --text-color: #ff007f;"><input type="checkbox" value="BANKNIFTY" onchange="handleIndexChange()"> <span>⬡ BANKNIFTY</span></label>
        <label style="--glow-color: #00ff88; --text-color: #00ff88;"><input type="checkbox" value="FINNIFTY" onchange="handleIndexChange()"> <span>⬡ FINNIFTY</span></label>
        <label style="--glow-color: #00a2ff; --text-color: #00a2ff;"><input type="checkbox" value="MIDCPNIFTY" onchange="handleIndexChange()"> <span>⬡ MIDCPNIFTY</span></label>
        <label style="--glow-color: #ffa500; --text-color: #ffa500;"><input type="checkbox" value="BANKEX" onchange="handleIndexChange()"> <span>⬡ BANKEX</span></label>
      </div>
    </div>
    <div class="live-badge"><div class="dot"></div>LIVE</div>
    <button id="startBtn" class="start-btn" onclick="startTrading()" style="display:none;">▶️ START ALGO</button>
    <button id="stopBtn" class="stop-btn" onclick="stopTrading()">🛑 STOP ALGO</button>
    <button id="squareoffBtn" class="squareoff-btn" onclick="openSquareoffSelectModal()">⚡ SQUARE OFF</button>
    <button id="downloadBtn" class="download-btn" onclick="downloadTrades()">📥 DOWNLOAD TRADES</button>
    <button id="resetBtn" class="reset-btn" disabled style="opacity: 0.4; cursor: not-allowed; pointer-events: none;" onclick="resetTradingLog()">🔄 RESET LOGS</button>
  </div>
</div>
<div class="wrap">
<div class="metrics"><div class="mc blue"><div class="mc-lbl">Capital</div><div class="mc-val" id="mCap">—</div><div class="mc-sub" id="mCapSub">Base: —</div></div><div class="mc purple"><div class="mc-lbl">Total P&L</div><div class="mc-val" id="mPnl">—</div><div class="mc-sub" id="mPnlPct">—</div></div><div class="mc orange" style="cursor:pointer;" onclick="showOverallCharges()"><div class="mc-lbl">Total Charges</div><div class="mc-val" id="mCharges">—</div><div class="mc-sub" id="mChargesSub">—</div></div><div class="mc green"><div class="mc-lbl">Win Rate</div><div class="mc-val g" id="mWR">—</div><div class="mc-sub" id="mWL">— W / — L</div></div><div class="mc gold"><div class="mc-lbl">Avg Win</div><div class="mc-val g" id="mAW">—</div><div class="mc-sub" id="mRR">R:R —</div></div><div class="mc red"><div class="mc-lbl">Avg Loss</div><div class="mc-val r" id="mAL">—</div><div class="mc-sub" id="mTC">— trades</div></div><div class="mc teal"><div class="mc-lbl">Best Trade</div><div class="mc-val g" id="mBest">—</div><div class="mc-sub" id="mWorst">Worst: —</div></div><div class="mc purple" id="vixCard"><div class="mc-lbl">India VIX</div><div class="mc-val" id="mVix">—</div><div class="mc-sub" id="mVixSub">Real-time Volatility</div></div></div>

<!-- Smart Signal Guard Control Card -->
<div class="card smart-card" style="margin-bottom:20px; border-color: rgba(124, 77, 255, 0.35); background: linear-gradient(135deg, rgba(124, 77, 255, 0.02) 0%, rgba(0, 240, 255, 0.01) 100%);">
  <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:12px; margin-bottom:16px;">
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="font-size:18px;">🛡️</span>
      <span style="font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:1.5px; color:#c7d2fe; text-shadow:0 0 10px rgba(124, 77, 255, 0.4)">Smart Signal Guard</span>
    </div>
    <!-- Custom Switch Toggle -->
    <div style="display:flex; align-items:center; gap:10px;">
      <span style="font-size:10px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:0.8px;">Guard Status</span>
      <label class="switch">
        <input type="checkbox" id="smartToggleInput" onchange="toggleSmartFilter(this.checked)">
        <span class="slider round"></span>
      </label>
    </div>
  </div>
  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:16px;">
    <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; padding:10px; text-align:center;">
      <div style="font-size:9px; color:var(--muted); text-transform:uppercase; font-weight:700; margin-bottom:4px; letter-spacing:0.5px;">Status</div>
      <div style="font-size:14px; font-weight:800; font-family:'JetBrains Mono',monospace;" id="smartStatus">—</div>
    </div>
    <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; padding:10px; text-align:center;">
      <div style="font-size:9px; color:var(--muted); text-transform:uppercase; font-weight:700; margin-bottom:4px; letter-spacing:0.5px;">Historical Trades</div>
      <div style="font-size:14px; font-weight:800; font-family:'JetBrains Mono',monospace; color:var(--blue);" id="smartSamples">—</div>
    </div>
    <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; padding:10px; text-align:center;">
      <div style="font-size:9px; color:var(--muted); text-transform:uppercase; font-weight:700; margin-bottom:4px; letter-spacing:0.5px;">Win / Loss</div>
      <div style="font-size:14px; font-weight:800; font-family:'JetBrains Mono',monospace; color:var(--green);" id="smartRatio">—</div>
    </div>
    <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; padding:10px; text-align:center;">
      <div style="font-size:9px; color:var(--muted); text-transform:uppercase; font-weight:700; margin-bottom:4px; letter-spacing:0.5px;">Engine Accuracy</div>
      <div style="font-size:14px; font-weight:800; font-family:'JetBrains Mono',monospace; color:var(--gold);" id="smartAccuracy">—</div>
    </div>
    <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; padding:10px; text-align:center;">
      <div style="font-size:9px; color:var(--muted); text-transform:uppercase; font-weight:700; margin-bottom:4px; letter-spacing:0.5px;">Blocked Signals</div>
      <div style="font-size:14px; font-weight:800; font-family:'JetBrains Mono',monospace; color:var(--red);" id="smartFiltered">—</div>
    </div>
  </div>
</div>


<!-- Active Open Positions -->
<div class="card open-pos-card" id="openPosCard" style="margin-bottom:20px;display:none;">
  <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
    <span>⚡ ACTIVE POSITIONS</span>
    <span class="pulse-badge" id="activeCount">0 ACTIVE</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Index</th>
        <th>Contract</th>
        <th>Expiry</th>
        <th>Time</th>
        <th>Dir</th>
        <th>Qty</th>
        <th>Entry Px</th>
        <th>LTP</th>
        <th>SL / TP</th>
        <th>Max Risk/Reward</th>
        <th>Charges</th>
        <th>Current P&L</th>
      </tr>
    </thead>
    <tbody id="openTbl">
      <!-- Open positions injected here -->
    </tbody>
  </table>
</div>

<div class="charts"><div class="card eq-wrap"><div class="card-title" style="display:flex;justify-content:space-between;align-items:center;"><span>📊 PERFORMANCE TRAJECTORY</span><div class="chart-tabs"><button id="btnEquityWeb" class="chart-tab active" onclick="switchWebChart('equity')">📈 Equity Curve</button><button id="btnDrawdownWeb" class="chart-tab" onclick="switchWebChart('drawdown')">📉 Drawdown</button></div></div><div class="chart-box" style="position:relative;" id="equityWebChartContainer"><canvas id="eq"></canvas><canvas id="eqSpark"></canvas><div class="eq-glow"></div></div><div class="chart-box" style="position:relative;display:none;" id="drawdownWebChartContainer"><canvas id="ddChart"></canvas></div></div><div class="card"><div class="card-title">Win/Loss Per Trade</div><div class="chart-box" style="position:relative;"><canvas id="wl"></canvas></div></div></div>

<div class="bottom"><div class="card"><div class="card-title">Trade History</div><div class="table-scroll"><table><thead><tr><th>Index</th><th>Date</th><th>Entry Time</th><th>Exit Time</th><th>Dir</th><th>Strike</th><th>Entry</th><th>Exit</th><th>Charges</th><th>PnL (Net)</th><th>Reason</th></tr></thead><tbody id="tTbl"><tr><td colspan="11" class="empty">No trades yet</td></tr></tbody></table></div></div></div>

<div class="index-break" id="indexBreakContainer">
  <!-- Dynamic index cards will be generated here -->
</div>
</div>
<div class="refresh-bar" id="rb">Auto-refresh every 5s</div>

<!-- Broker Connection Modal -->
<div class="modal-overlay" id="credentialsModal">
  <div class="modal-container">
    <div class="modal-hdr">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2C6.48 2 2 6.48 2 12C2 17.52 6.48 22 12 22C17.52 22 22 17.52 22 12C22 6.48 17.52 2 12 2ZM11 16H13V18H11V16ZM11 6H13V14H11V6Z" fill="#00ff88"/>
      </svg>
      <span>Connect Broker</span>
    </div>
    <div class="modal-desc">
      Please provide your broker credentials to activate live trading. These credentials will be securely loaded and saved.
    </div>
    <div class="form-group">
      <label for="modalClientId">Client ID / Email</label>
      <input type="text" id="modalClientId" placeholder="Enter your Client ID">
    </div>
    <div class="form-group" style="margin-bottom: 10px;">
      <label for="modalAccessToken">Access Token / Password</label>
      <input type="password" id="modalAccessToken" placeholder="Enter your Access Token / PIN">
    </div>
    <div id="modalErrorMsg" style="color: var(--red); font-size: 12px; font-weight: 600; min-height: 18px; margin-top: 8px;"></div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" onclick="closeCredentialsModal()">Cancel</button>
      <button class="modal-btn-connect" id="modalConnectBtn" onclick="submitCredentials()">Connect Broker</button>
    </div>
  </div>
</div>
</div>

<script>
// Auth State Management
let currentAuthTab = 'login';

function switchAuthTab(tab) {
  currentAuthTab = tab;
  document.getElementById('loginTabBtn').classList.toggle('active', tab === 'login');
  document.getElementById('signupTabBtn').classList.toggle('active', tab === 'signup');
  document.getElementById('authSubmitBtn').textContent = tab === 'login' ? 'Sign In' : 'Sign Up';
  
  // Clear alerts
  document.getElementById('authErrorAlert').style.display = 'none';
  document.getElementById('authSuccessAlert').style.display = 'none';
}

function showAuthScreen() {
  document.getElementById('authApp').style.display = 'flex';
  document.getElementById('dashboardApp').style.display = 'none';
  if (window.autoRefreshInterval) {
    clearInterval(window.autoRefreshInterval);
    window.autoRefreshInterval = null;
  }
}

function showDashboardScreen(username) {
  document.getElementById('authApp').style.display = 'none';
  document.getElementById('dashboardApp').style.display = 'block';
  
  // Add user badge & logout button to the header
  const hdrTitle = document.querySelector('.hdr-title');
  if (hdrTitle && !document.getElementById('userBadge')) {
    const badge = document.createElement('span');
    badge.id = 'userBadge';
    badge.style.cssText = "font-size: 11px; background: var(--accent); color: #fff; padding: 2px 8px; border-radius: 20px; margin-left: 10px; font-weight: 700; box-shadow: 0 0 10px rgba(157, 78, 221, 0.4); text-transform: uppercase;";
    badge.textContent = username;
    hdrTitle.appendChild(badge);
    
    const logoutBtn = document.createElement('button');
    logoutBtn.style.cssText = "background: rgba(255, 0, 127, 0.1); border: 1px solid rgba(255, 0, 127, 0.3); border-radius: 8px; padding: 6px 12px; color: var(--red); font-size: 11px; font-weight: 700; cursor: pointer; transition: all 0.3s; margin-left: 10px;";
    logoutBtn.textContent = 'LOGOUT';
    logoutBtn.onclick = logoutUser;
    hdrTitle.appendChild(logoutBtn);
  }
  
  if (!window.autoRefreshInterval) {
    load();
    window.autoRefreshInterval = setInterval(load, 3000);
  }
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  const u = document.getElementById('authUsername').value.trim();
  const p = document.getElementById('authPassword').value.trim();
  
  const errAlert = document.getElementById('authErrorAlert');
  const succAlert = document.getElementById('authSuccessAlert');
  errAlert.style.display = 'none';
  succAlert.style.display = 'none';
  
  const endpoint = currentAuthTab === 'login' ? '/api/login' : '/api/register';
  
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p })
    });
    const data = await res.json();
    
    if (res.ok) {
      if (currentAuthTab === 'login') {
        succAlert.textContent = "Success! Access granted.";
        succAlert.style.display = 'block';
        setTimeout(() => {
          showDashboardScreen(u);
        }, 800);
      } else {
        succAlert.textContent = "Account created! You can now log in.";
        succAlert.style.display = 'block';
        setTimeout(() => {
          switchAuthTab('login');
          document.getElementById('authPassword').value = '';
        }, 1200);
      }
    } else {
      errAlert.textContent = data.message || "An error occurred.";
      errAlert.style.display = 'block';
    }
  } catch (e) {
    errAlert.textContent = "Network error. Try again.";
    errAlert.style.display = 'block';
  }
}

async function logoutUser() {
  try {
    await fetch('/api/logout', { method: 'POST' });
  } catch(e) {}
  
  const badge = document.getElementById('userBadge');
  if (badge) badge.remove();
  const btns = document.querySelectorAll('.hdr-title button');
  btns.forEach(b => b.remove());
  
  showAuthScreen();
}

async function checkAuthStatus() {
  try {
    const res = await fetch('/api/auth/status');
    const d = await res.json();
    if (d.authenticated) {
      showDashboardScreen(d.username);
    } else {
      showAuthScreen();
    }
  } catch(e) {
    showAuthScreen();
  }
}

let eqC=null, wlC=null, ddC=null;

function switchWebChart(type) {
  const btnEq = document.getElementById('btnEquityWeb');
  const btnDd = document.getElementById('btnDrawdownWeb');
  const boxEq = document.getElementById('equityWebChartContainer');
  const boxDd = document.getElementById('drawdownWebChartContainer');
  
  if (type === 'equity') {
    btnEq.classList.add('active');
    btnDd.classList.remove('active');
    boxEq.style.display = 'block';
    boxDd.style.display = 'none';
  } else {
    btnDd.classList.add('active');
    btnEq.classList.remove('active');
    boxDd.style.display = 'block';
    boxEq.style.display = 'none';
  }
}

let globalDhanCredentials = null;
let globalGrowwCredentials = null;

function toggleIndicesDropdown(event) {
  event.stopPropagation();
  const dropdown = document.getElementById('indicesDropdown');
  const content = document.getElementById('indicesDropdownContent');
  const isActive = dropdown.classList.contains('active');
  
  dropdown.classList.toggle('active', !isActive);
  content.classList.toggle('show', !isActive);
}

async function handleIndexChange() {
  const checkboxes = document.querySelectorAll('#indicesDropdownContent input[type="checkbox"]');
  const checked = Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.value);
  
  if (checked.length === 0) {
    alert("⚠️ At least one trading index must be selected!");
    // Default back to NIFTY and SENSEX to prevent empty state
    document.querySelectorAll('#indicesDropdownContent input[type="checkbox"]').forEach(cb => {
      if (cb.value === "NIFTY" || cb.value === "SENSEX") cb.checked = true;
    });
    return;
  }
  
  try {
    const r = await fetch('/api/indices/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ indices: checked })
    }).then(res => res.json());
    
    if (r.status === 'success') {
      const btnText = document.querySelector('#indicesDropdown .dropdown-btn span');
      if (btnText) {
        btnText.textContent = '📈 INDICES (' + checked.length + ')';
      }
      load(); // Dynamic dashboard refresh
    } else {
      alert("❌ Failed to update indices: " + r.message);
    }
  } catch (e) {
    console.error("Error updating indices:", e);
  }
}

// Global click outside listener to close dropdown securely
window.addEventListener('click', function(e) {
  const drop = document.getElementById('indicesDropdown');
  if (drop && !drop.contains(e.target)) {
    drop.classList.remove('active');
    const content = document.getElementById('indicesDropdownContent');
    if (content) content.classList.remove('show');
  }
});

function showCredentialsModal(broker, onSuccessCallback) {
  const modal = document.getElementById('credentialsModal');
  const hdrSpan = modal.querySelector('.modal-hdr span');
  const desc = modal.querySelector('.modal-desc');
  const clientIdLabel = modal.querySelectorAll('.form-group label')[0];
  const clientIdInput = document.getElementById('modalClientId');
  const tokenLabel = modal.querySelectorAll('.form-group label')[1];
  const tokenInput = document.getElementById('modalAccessToken');
  const errorMsg = document.getElementById('modalErrorMsg');
  
  errorMsg.textContent = '';
  clientIdInput.value = '';
  tokenInput.value = '';
  
  modal.broker = broker;
  modal.onSuccess = onSuccessCallback;
  
  hdrSpan.textContent = 'Connect to GROWW';
  desc.textContent = 'Please provide your Groww API Key and API Secret / Token to activate live trading on Groww.';
  clientIdLabel.textContent = 'Groww API Key (Client ID)';
  clientIdInput.placeholder = 'Enter your Groww Client ID / API Key';
  tokenLabel.textContent = 'Access Token / API Secret';
  tokenInput.placeholder = 'Enter your Groww Access Token / PIN';
  tokenInput.type = 'password';
  
  modal.active = true;
  modal.classList.add('active');
}

function closeCredentialsModal() {
  const modal = document.getElementById('credentialsModal');
  modal.classList.remove('active');
}

async function submitCredentials() {
  const modal = document.getElementById('credentialsModal');
  const broker = modal.broker || 'GROWW';
  const clientId = document.getElementById('modalClientId').value.trim();
  const token = document.getElementById('modalAccessToken').value.trim();
  const errorMsg = document.getElementById('modalErrorMsg');
  const connectBtn = document.getElementById('modalConnectBtn');
  
  if (!clientId) {
    errorMsg.style.color = 'var(--red)';
    errorMsg.textContent = '❌ Client ID is required.';
    return;
  }
  
  let tokenToSend = token;
  const isDhan = broker === 'DHAN';
  const credentialsObj = isDhan ? globalDhanCredentials : globalGrowwCredentials;
  
  if (!token && credentialsObj && credentialsObj.has_token) {
    tokenToSend = 'REUSE_SAVED_TOKEN';
  }
  
  if (!tokenToSend) {
    errorMsg.style.color = 'var(--red)';
    errorMsg.textContent = isDhan ? '❌ Access Token is required.' : '❌ API Secret / Token is required.';
    return;
  }
  
  errorMsg.style.color = 'var(--muted)';
  errorMsg.textContent = '⏳ Connecting and validating...';
  connectBtn.disabled = true;
  connectBtn.textContent = 'Connecting...';
  
  try {
    const r = await fetch('/api/broker/credentials', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ broker: broker, client_id: clientId, access_token: tokenToSend })
    }).then(res => res.json());
    
    if (r.status === 'success') {
      errorMsg.style.color = 'var(--green)';
      errorMsg.textContent = '✅ Connected successfully!';
      
      if (isDhan) {
        globalDhanCredentials = { client_id: clientId, has_token: true };
      } else {
        globalGrowwCredentials = { client_id: clientId, has_token: true };
      }
      
      setTimeout(() => {
        closeCredentialsModal();
        connectBtn.disabled = false;
        connectBtn.textContent = 'Connect Broker';
        
        const modal = document.getElementById('credentialsModal');
        if (modal.onSuccess) {
          modal.onSuccess();
        }
      }, 1000);
    } else {
      errorMsg.style.color = 'var(--red)';
      errorMsg.textContent = '❌ ' + r.message;
      connectBtn.disabled = false;
      connectBtn.textContent = 'Connect Broker';
    }
  } catch(e) {
    errorMsg.style.color = 'var(--red)';
    errorMsg.textContent = '❌ Connection failed: ' + e.message;
    connectBtn.disabled = false;
    connectBtn.textContent = 'Connect Broker';
  }
}

async function toggleSmartFilter(enabled) {
  try {
    const r = await fetch('/api/smart_filter/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled })
    }).then(res => res.json());
    
    if (r.status === 'success') {
      load();
    } else {
      alert("Error toggling Smart Signal Guard: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  }
}

async function toggleTrailingSL(enabled) {
  try {
    const r = await fetch('/api/trailing_sl/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled })
    }).then(res => res.json());
    
    if (r.status === 'success') {
      load();
    } else {
      alert("Error toggling Trailing Stop Loss: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  }
}

const fi = n => new Intl.NumberFormat('en-IN').format(Math.round(+n||0));
const fp = n => { const v=+n||0; return (v>=0?'+':'')+'Rs.'+fi(v); };
const pc = n => (+n||0)>=0?'g':'r';

async function switchMode(targetMode) {
  if (targetMode === 'LIVE') {
    openLiveBrokerSelection();
  } else {
    if (!confirm("Switch back to DEMO Paper Trading mode?")) {
      return;
    }
    await activateLiveMode(targetMode);
  }
}

function openLiveBrokerSelection() {
  document.getElementById('liveBrokerSelectModal').style.display = 'flex';
}
function closeLiveBrokerSelection() {
  document.getElementById('liveBrokerSelectModal').style.display = 'none';
}
async function selectLiveBroker(broker) {
  closeLiveBrokerSelection();
  
  const firstConfirm = confirm(`⚠️ WARNING: You are about to enable LIVE TRADING mode on ${broker}!\n\nThis will place real money orders on ${broker} for NIFTY and SENSEX options contracts. Are you absolutely sure you want to proceed?`);
  if (!firstConfirm) return;
  
  const secondPrompt = prompt(`🛡️ DOUBLE CONFIRMATION REQUIRED:\n\nPlease type the word 'CONFIRM' (in all capital letters) to activate live trading on ${broker}:`);
  if (secondPrompt !== 'CONFIRM') {
    alert("Live mode activation aborted.");
    return;
  }
  
  try {
    const r = await fetch('/api/broker', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ broker: broker })
    }).then(res => res.json());
    
    if (r.status === 'success') {
      showCredentialsModal(broker, async () => {
        await activateLiveMode('LIVE');
      });
    } else {
      alert("Error switching broker: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  }
}

async function activateLiveMode(targetMode) {
  try {
    const r = await fetch('/api/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: targetMode })
    }).then(res => res.json());

    if (r.status === 'success') {
      alert(r.message);
      load();
    } else {
      alert("Error switching mode: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  }
}

async function switchBroker(targetBroker) {
  const liveModeBtn = document.getElementById('liveModeBtn');
  const isLiveActive = liveModeBtn && liveModeBtn.classList.contains('active');
  
  if (isLiveActive) {
    showCredentialsModal(targetBroker, async () => {
      await activateBroker(targetBroker);
    });
    return;
  }
  
  await activateBroker(targetBroker);
}

async function activateBroker(targetBroker) {
  try {
    const r = await fetch('/api/broker', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ broker: targetBroker })
    }).then(res => res.json());
    if (r.status === 'success') {
      alert(r.message);
      load();
    } else {
      alert("Error switching broker: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  }
}

async function startTrading() {
  if (!confirm("Are you sure you want to START automated algo scanning?")) {
    return;
  }
  const btn = document.getElementById('startBtn');
  btn.disabled = true;
  btn.textContent = "▶️ STARTING...";
  try {
    const r = await fetch('/api/start', { method: 'POST' }).then(res => res.json());
    if (r.status === 'started') {
      btn.style.display = "none";
      alert(r.message);
      load(); // Reload data immediately
    } else {
      alert("Error starting: " + r.message);
      btn.disabled = false;
      btn.textContent = "▶️ START ALGO";
    }
  } catch(e) {
    alert("Connection error: " + e.message);
    btn.disabled = false;
    btn.textContent = "▶️ START ALGO";
  }
}

async function stopTrading() {
  if (!confirm("Are you sure you want to STOP automated trading? (Active positions will remain open)")) {
    return;
  }
  const btn = document.getElementById('stopBtn');
  btn.disabled = true;
  btn.textContent = "🛑 STOPPING...";
  try {
    const r = await fetch('/api/stop', { method: 'POST' }).then(res => res.json());
    if (r.status === 'stopped') {
      btn.style.display = "none";
      alert(r.message);
      load(); // Reload data immediately
    } else {
      alert("Error stopping: " + r.message);
      btn.disabled = false;
      btn.textContent = "🛑 STOP ALGO";
    }
  } catch(e) {
    alert("Connection error: " + e.message);
    btn.disabled = false;
    btn.textContent = "🛑 STOP ALGO";
  }
}

async function squareOffAll() {
  if (!confirm("⚠️ CAUTION: Are you sure you want to SQUARE OFF and close all active positions immediately? This will place exit market orders for all active contracts!")) {
    return;
  }
  const btn = document.getElementById('squareoffBtn');
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "⚡ EXITING...";
  try {
    const r = await fetch('/api/squareoff', { method: 'POST' }).then(res => res.json());
    if (r.status === 'success') {
      alert(r.message);
      load(); // Reload data immediately
    } else {
      alert("Error squaring off: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

function promptUpdateCapital() {
  // Disabled by user request
  return;
}
function closeCapitalModal() {
  document.getElementById('capitalModal').style.display = 'none';
}
async function submitCapitalUpdate() {
  const inputVal = document.getElementById('capitalInputVal').value.trim();
  const errorEl = document.getElementById('capitalModalError');
  const btn = document.getElementById('capitalUpdateModalBtn');
  
  if (!inputVal) {
    errorEl.textContent = '❌ Please enter a capital amount.';
    return;
  }
  const newCap = parseFloat(inputVal);
  if (isNaN(newCap) || newCap <= 0) {
    errorEl.textContent = '❌ Capital must be a positive number.';
    return;
  }
  
  errorEl.textContent = '⏳ Updating...';
  btn.disabled = true;
  
  try {
    const r = await fetch('/api/capital/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ capital: newCap })
    }).then(res => res.json());
    
    if (r.status === 'success') {
      closeCapitalModal();
      load(); // Reload dashboard stats instantly!
    } else {
      errorEl.textContent = '❌ ' + r.message;
    }
  } catch(e) {
    errorEl.textContent = '❌ Connection error: ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

function downloadTrades() {
  window.open('/api/trades/download', '_blank');
}

function resetTradingLog() {
  // Disabled by user request
  return;
}

function showResetConfirmModal() {
  document.getElementById('resetConfirmModal').style.display = 'flex';
}

function closeResetConfirmModal() {
  document.getElementById('resetConfirmModal').style.display = 'none';
}

async function confirmResetTradingLog() {
  closeResetConfirmModal();
  const btn = document.getElementById('resetBtn');
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "🔄 RESETTING...";
  try {
    const r = await fetch('/api/reset', { method: 'POST' }).then(res => res.json());
    if (r.status === 'success') {
      alert(r.message);
      load(); // Reload data immediately
    } else {
      alert("Error resetting: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

async function load(){
  try{
    const d = await fetch('/api/data').then(r=>r.json());
    window.lastLoadedData = d;
    if (d.dhan_credentials) {
      globalDhanCredentials = d.dhan_credentials;
    }
    if (d.groww_credentials) {
      globalGrowwCredentials = d.groww_credentials;
    }
    
    // Dynamically synchronize selected indices checkboxes from backend config
    if (d.trading_indices) {
      document.querySelectorAll('#indicesDropdownContent input[type="checkbox"]').forEach(cb => {
        cb.checked = d.trading_indices.includes(cb.value);
      });
      const btnText = document.querySelector('#indicesDropdown .dropdown-btn span');
      if (btnText) {
        btnText.textContent = '📈 INDICES (' + d.trading_indices.length + ')';
      }
    }

    document.getElementById('mCap').textContent='Rs.'+fi(d.stats.capital);
    // Show Groww balance breakdown when in live mode
    if (d.groww_balance && d.live_trading && d.active_broker === 'GROWW') {
      let subParts = [];
      if (d.groww_balance.clear_cash > 0) subParts.push('Cash: Rs.' + fi(d.groww_balance.clear_cash));
      if (d.groww_balance.fno_available > 0) subParts.push('F&O: Rs.' + fi(d.groww_balance.fno_available));
      if (d.groww_balance.equity_available > 0) subParts.push('Equity: Rs.' + fi(d.groww_balance.equity_available));
      if (d.groww_balance.collateral > 0) subParts.push('Collateral: Rs.' + fi(d.groww_balance.collateral));
      document.getElementById('mCapSub').textContent = subParts.length > 0 ? subParts.join(' | ') : 'Groww Live Balance';
    } else {
      document.getElementById('mCapSub').textContent='Base: Rs.'+fi(d.stats.base_capital);
    }
    const pe=document.getElementById('mPnl');
    pe.textContent=fp(d.stats.total_pnl); pe.className='mc-val '+pc(d.stats.total_pnl);
    document.getElementById('mPnlPct').textContent=((d.stats.total_pnl/d.stats.base_capital*100).toFixed(2)>=0?'+':'')+((d.stats.total_pnl/d.stats.base_capital*100).toFixed(2))+'%';
    document.getElementById('mCharges').textContent='Rs.'+fi(d.stats.total_charges);
    document.getElementById('mChargesSub').textContent='Real-time Deducted';
    document.getElementById('mWR').textContent=(d.stats.win_rate||0)+'%';
    document.getElementById('mWL').textContent=d.stats.wins+' W / '+d.stats.losses+' L';
    document.getElementById('mAW').textContent=fp(d.stats.avg_win);
    document.getElementById('mRR').textContent='R:R '+(d.stats.rr||0);
    document.getElementById('mAL').textContent=fp(d.stats.avg_loss);
    document.getElementById('mTC').textContent=(d.stats.total_trades||0)+' trades';
    document.getElementById('mBest').textContent=fp(d.stats.best);
    document.getElementById('mWorst').textContent='Worst: '+fp(d.stats.worst);
    
    // Update dynamic VIX card
    if (d.vix !== undefined) {
      document.getElementById('mVix').textContent=d.vix.toFixed(2);
      document.getElementById('mVixSub').textContent = d.vix > 18 ? '🔥 High Volatility' : d.vix < 12 ? '💤 Low Volatility' : '⚖️ Normal Volatility';
    }
    
    // Update Mode Selector UI
    const demoModeBtn = document.getElementById('demoModeBtn');
    const liveModeBtn = document.getElementById('liveModeBtn');
    const hdrSub = document.getElementById('hdrSub');
    
    // Update Broker Selector UI
    const dhanBrokerBtn = document.getElementById('dhanBrokerBtn');
    const growwBrokerBtn = document.getElementById('growwBrokerBtn');
    if (dhanBrokerBtn && growwBrokerBtn) {
      if (d.active_broker === 'GROWW') {
        dhanBrokerBtn.classList.remove('active');
        growwBrokerBtn.classList.add('active');
      } else {
        growwBrokerBtn.classList.remove('active');
        dhanBrokerBtn.classList.add('active');
      }
    }

    // Capital card — disable click-to-update in live mode
    const capCard = document.querySelector('.mc.blue');
    if (d.live_trading) {
      window._isLiveMode = true;
      demoModeBtn.classList.remove('active');
      liveModeBtn.classList.add('active');
      if (capCard) { capCard.style.cursor = 'default'; capCard.title = 'Live broker balance (read-only)'; }
      if (d.active_broker === 'GROWW') {
        hdrSub.innerHTML = '<span style="color:#7c4dff;font-weight:700;text-shadow:0 0 10px rgba(124, 77, 255, 0.45)">⚡ LIVE · REAL MONEY GROWW TRADING</span>';
      } else {
        hdrSub.innerHTML = '<span style="color:#ff6e00;font-weight:700;animation:livePulse 2s infinite">⚡ LIVE · REAL MONEY DHAN TRADING</span>';
      }
    } else {
      window._isLiveMode = false;
      liveModeBtn.classList.remove('active');
      demoModeBtn.classList.add('active');
      if (capCard) { capCard.style.cursor = 'pointer'; capCard.title = 'Click to update paper capital'; }
      hdrSub.innerHTML = '🎮 DEMO · REAL-TIME PAPER TRADING';
    }
    
    // Start/Stop Button Status Update
    const stopBtn = document.getElementById('stopBtn');
    const startBtn = document.getElementById('startBtn');
    const liveBadge = document.querySelector('.live-badge');
    if (d.running) {
      startBtn.style.display = 'none';
      stopBtn.style.display = 'flex';
      stopBtn.className = "stop-btn";
      stopBtn.textContent = "🛑 STOP ALGO";
      stopBtn.disabled = false;
      if (liveBadge) {
        liveBadge.innerHTML = '<div class="dot"></div>LIVE';
        liveBadge.style.color = 'var(--green)';
        liveBadge.style.borderColor = 'rgba(0, 230, 118, 0.2)';
      }
    } else {
      startBtn.style.display = 'flex';
      startBtn.disabled = false;
      startBtn.textContent = "▶️ START ALGO";
      stopBtn.style.display = 'none';
      if (liveBadge) {
        liveBadge.innerHTML = '<div class="dot" style="background:var(--muted);box-shadow:none;animation:none;"></div>OFFLINE';
        liveBadge.style.color = 'var(--muted)';
        liveBadge.style.borderColor = 'var(--border)';
      }
    }
    


    // Update Smart Signal Guard Status Card
    if (d.smart_status) {
      const smart = d.smart_status;
      const toggleInput = document.getElementById('smartToggleInput');
      if (toggleInput) {
        toggleInput.checked = smart.enabled;
      }
      
      const smartStat = document.getElementById('smartStatus');
      if (smartStat) {
        smartStat.textContent = smart.status;
        if (smart.status.includes("ACTIVE") || smart.status.includes("PROTECTED")) {
          smartStat.style.color = "var(--green)";
          smartStat.style.textShadow = "0 0 8px rgba(0, 255, 136, 0.3)";
        } else {
          smartStat.style.color = "#ff9f43";
          smartStat.style.textShadow = "0 0 8px rgba(255, 159, 67, 0.3)";
        }
      }
      
      document.getElementById('smartSamples').textContent = smart.total_samples;
      document.getElementById('smartRatio').textContent = smart.wins + ' W / ' + smart.losses + ' L';
      document.getElementById('smartAccuracy').textContent = smart.accuracy;
      document.getElementById('smartFiltered').textContent = smart.filtered_count + ' Blocked';
    }
    
    // Open positions handling
    const opc = document.getElementById('openPosCard');
    const ot = document.getElementById('openTbl');
    const ac = document.getElementById('activeCount');
    if (!d.open_positions || !d.open_positions.length) {
      opc.style.display = 'none';
    } else {
      opc.style.display = 'block';
      ac.textContent = d.open_positions.length + ' ACTIVE';
      ot.innerHTML = d.open_positions.map(p => {
    const w = +p.pnl >= 0;
        const idxColors = {
          'NIFTY': 'var(--blue)', 'SENSEX': 'var(--gold)', 'BANKNIFTY': '#ff4757',
          'FINNIFTY': '#2ed573', 'MIDCPNIFTY': '#1e90ff', 'BANKEX': '#ffa502'
        };
        const idxColor = idxColors[p.index] || '#ff00e4';
        const idx = `<span style="color:${idxColor};font-weight:700">${p.index}</span>`;
        const chgVal = p.charges ? 'Rs.' + fi(p.charges) : '—';
        const maxLoss = Math.round((p.entry - p.sl) * p.contracts);
        const maxProfit = Math.round((p.tp - p.entry) * p.contracts);
        const dirClass = (p.opt || p.direction || '').toUpperCase() === 'CE' ? 'call' : 'put';
        const contractLabel = p.contract_sym || `${p.index} ${p.strike}${p.opt}`;
        const expiryLabel = p.expiry || '—';
        const ltpColor = (+p.cur >= +p.entry) ? 'var(--green)' : 'var(--red)';
        return `<tr>
          <td>${idx}</td>
          <td style="font-size:10px;color:#c7d2fe;font-weight:700;font-family:'JetBrains Mono',monospace;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${contractLabel}">${contractLabel}</td>
          <td class="m" style="font-size:10px;">${expiryLabel}</td>
          <td class="m">${p.entry_time}</td>
          <td><span class="badge ${dirClass}">${p.opt || p.direction}</span></td>
          <td>${p.contracts} (${p.lots}L)</td>
          <td>Rs.${p.entry}</td>
          <td style="color:${ltpColor};font-weight:700">Rs.${p.cur}</td>
          <td class="m">
            <div style="display:flex;align-items:center;gap:6px;justify-content:center;">
              <span style="font-size:11px;color:var(--muted)">SL:</span>
              <input type="number" step="0.1" value="${p.sl}" id="sl-${p.tid}" style="width:48px;background:rgba(255,255,255,0.05);border:1px solid var(--border);border-radius:4px;color:#fff;padding:2px 4px;font-size:11px;font-family:monospace;text-align:center;">
              <span style="font-size:11px;color:var(--muted)">TP:</span>
              <input type="number" step="0.1" value="${p.tp}" id="tp-${p.tid}" style="width:48px;background:rgba(255,255,255,0.05);border:1px solid var(--border);border-radius:4px;color:#fff;padding:2px 4px;font-size:11px;font-family:monospace;text-align:center;">
              <label style="display:flex;align-items:center;gap:3px;cursor:pointer;user-select:none;font-size:11px;color:#00f0ff;" title="Use Trailing Stop Loss">
                <input type="checkbox" id="tsl-${p.tid}" ${p.trailing_sl_enabled ? 'checked' : ''} style="cursor:pointer;accent-color:#00f0ff;">
                <span>TSL</span>
              </label>
              <button onclick="updateSlTp('${p.tid}')" style="background:#ff9f43;border:none;border-radius:4px;color:#000;padding:2px 6px;font-size:10px;font-weight:700;cursor:pointer;transition:all 0.2s;" onmouseover="this.style.filter='brightness(1.1)'" onmouseout="this.style.filter='none'">✏️</button>
              <button onclick="squareoffPosition('${p.tid}', '${contractLabel}', ${p.contracts}, ${p.cur})" style="background:#ff4757;border:none;border-radius:4px;color:#fff;padding:2px 6px;font-size:10px;font-weight:700;cursor:pointer;transition:all 0.2s;margin-left:4px;" onmouseover="this.style.filter='brightness(1.1)'" onmouseout="this.style.filter='none'">❌ Exit</button>
            </div>
          </td>
          <td class="m" style="line-height:1.45; text-align:center;">
            <div style="font-weight:700;"><span class="r">-₹${fi(maxLoss)}</span></div>
            <div style="font-weight:700;"><span class="g">+₹${fi(maxProfit)}</span></div>
          </td>
          <td class="m" style="cursor:pointer;text-decoration:underline;color:var(--blue);" onclick="showChargesDetails('${p.tid} Position', ${p.brokerage}, ${p.gst}, ${p.stt}, ${p.stamp_duty}, ${p.exchange_charges}, ${p.sebi_fee}, ${p.charges})">${chgVal}</td>
          <td class="${w?'g':'r'}" style="font-weight:700;font-size:12px;">${fp(p.pnl)} (${(p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct.toFixed(2)}%)</td>
        </tr>`;
      }).join('');
    }
    
    const formatTime = ts => {
      if (!ts) return '—';
      const m = String(ts).match(/\d{2}:\d{2}:\d{2}/);
      return m ? m[0] : ts;
    };
    const tb=document.getElementById('tTbl');
    const tradeRow = t => {
      const w=+t.pnl>0;
      let idx = t.index_name || 'NIFTY';
      const idxColors = {
        'NIFTY': 'var(--blue)',
        'SENSEX': 'var(--gold)',
        'BANKNIFTY': '#ff4757',
        'FINNIFTY': '#2ed573',
        'MIDCPNIFTY': '#1e90ff',
        'BANKEX': '#ffa502'
      };
      const color = idxColors[idx] || '#ff00e4';
      const idxBadge=`<span style="color:${color};font-size:9px;font-weight:700">${idx}</span>`;
      const chg = t.charges ? 'Rs.' + fi(t.charges) : '—';
      return `<tr><td>${idxBadge}</td><td class="m">${t.date||''}</td><td class="m">${formatTime(t.entry_time)}</td><td class="m">${formatTime(t.exit_time)}</td><td><span class="badge ${(t.direction||'').toLowerCase()}">${t.direction||''}</span></td><td>${t.strike||''}${t.opt||''}<div style="font-size:9px;color:rgba(255,255,255,0.45);font-family:monospace;margin-top:2px;">Exp: ${t.expiry||'—'}</div></td><td>Rs.${t.entry||0}</td><td>${t.exit?'Rs.'+t.exit:'—'}</td><td class="m" style="cursor:pointer;text-decoration:underline;color:var(--blue);" onclick="showChargesDetails('${t.tid} Realized', ${t.brokerage||0}, ${t.gst||0}, ${t.stt||0}, ${t.stamp_duty||0}, ${t.exchange_charges||0}, ${t.sebi_fee||0}, ${t.charges||0})">${chg}</td><td class="${w?'g':'r'}">${fp(t.pnl)}</td><td class="m">${(t.reason||'').replace(/_/g,' ')}</td></tr>`;
    };
    if(!d.trades||!d.trades.length){
      tb.innerHTML='<tr><td colspan="11" class="empty">No trades yet</td></tr>';
    } else {
      tb.innerHTML=[...d.trades].reverse().map(tradeRow).join('');
    }
    // Index breakdown tables
    const indexRow = t => {
      const w=+t.pnl>0;
      const chg = t.charges ? 'Rs.' + fi(t.charges) : '—';
      return `<tr><td class="m">${formatTime(t.entry_time)}</td><td class="m">${formatTime(t.exit_time)}</td><td><span class="badge ${(t.direction||'').toLowerCase()}">${t.direction||''}</span></td><td>${t.strike||''}${t.opt||''}<div style="font-size:9px;color:rgba(255,255,255,0.45);font-family:monospace;margin-top:2px;">Exp: ${t.expiry||'—'}</div></td><td>Rs.${t.entry||0}</td><td>${t.exit?'Rs.'+t.exit:'—'}</td><td class="m" style="cursor:pointer;text-decoration:underline;color:var(--blue);" onclick="showChargesDetails('${t.tid} Realized', ${t.brokerage||0}, ${t.gst||0}, ${t.stt||0}, ${t.stamp_duty||0}, ${t.exchange_charges||0}, ${t.sebi_fee||0}, ${t.charges||0})">${chg}</td><td class="${w?'g':'r'}">${fp(t.pnl)}</td><td class="m">${(t.reason||'').replace(/_/g,' ')}</td></tr>`;
    };
    const allTrades=d.trades||[];
    
    // Dynamically render index cards
    const container = document.getElementById('indexBreakContainer');
    if (container && d.trading_indices) {
      if (container.children.length !== d.trading_indices.length) {
        container.innerHTML = d.trading_indices.map(idx => {
          const displayName = idx === 'NIFTY' ? 'nifty trade' : idx === 'SENSEX' ? 'sensex trade' : idx + ' Trades';
          const className = idx.toLowerCase() + '-card';
          const titleClass = idx.toLowerCase();
          return `
            <div class="card ${className}">
              <div class="idx-hdr">
                <span class="idx-title ${titleClass}">⬡ ${displayName}</span>
                <span class="idx-pnl" id="${idx.toLowerCase()}Pnl">—</span>
              </div>
              <div class="index-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Entry Time</th>
                      <th>Exit Time</th>
                      <th>Dir</th>
                      <th>Strike</th>
                      <th>Entry</th>
                      <th>Exit</th>
                      <th>Charges</th>
                      <th>PnL (Net)</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody id="${idx.toLowerCase()}Tbl">
                    <tr><td colspan="9" class="empty">No trades yet</td></tr>
                  </tbody>
                </table>
              </div>
            </div>`;
        }).join('');
      }
    }
    
    if (d.trading_indices) {
      d.trading_indices.forEach(idx => {
        const displayName = idx === 'NIFTY' ? 'ALGO TRADING' : idx;
        const idxTrades = allTrades.filter(t => {
          if (idx === 'NIFTY') {
            return (t.tid || '').startsWith('ALGO_TRADING') || (t.tid || '').startsWith('NIFTY_DEEP_ALGO') || (t.tid || '').startsWith('NIFTY');
          }
          return (t.tid || '').startsWith(idx);
        });
        const idxPnl = idxTrades.reduce((s, t) => s + (+t.pnl || 0), 0);
        const pnlEl = document.getElementById(`${idx.toLowerCase()}Pnl`);
        if (pnlEl) {
          pnlEl.textContent = fp(idxPnl);
          pnlEl.className = 'idx-pnl ' + (idxPnl >= 0 ? 'g' : 'r');
        }
        const tblEl = document.getElementById(`${idx.toLowerCase()}Tbl`);
        if (tblEl) {
          tblEl.innerHTML = idxTrades.length 
            ? [...idxTrades].reverse().slice(0, 15).map(indexRow).join('') 
            : '<tr><td colspan="9" class="empty">No trades yet</td></tr>';
        }
      });
    }
    const labels=(d.equity||[]).map((_,i)=>'#'+(i+1));
    const eqData=d.equity||[];
    const pnls=(d.trades||[]).map(t=>+t.pnl);
    // 3D Equity chart
    const lastVal=eqData.length?eqData[eqData.length-1]:0;
    if(eqC){eqC.destroy();eqC=null;}
    
    const ctxEq = document.getElementById('eq').getContext('2d');
    const positiveGrad = ctxEq.createLinearGradient(0,0,0,190);
    positiveGrad.addColorStop(0,'rgba(0,230,118,0.35)');
    positiveGrad.addColorStop(1,'rgba(0,230,118,0.01)');
    
    const negativeGrad = ctxEq.createLinearGradient(0,0,0,190);
    negativeGrad.addColorStop(0,'rgba(255,61,113,0.01)');
    negativeGrad.addColorStop(1,'rgba(255,61,113,0.35)');

    eqC=new Chart(document.getElementById('eq'),{
        type:'line',
        data:{
            labels,
            datasets:[{
                data:eqData,
                borderColor:'#00e676',
                segment: {
                    borderColor: ctx => {
                        const p0Val = ctx.p0.parsed.y;
                        const p1Val = ctx.p1.parsed.y;
                        return (p0Val < 0 || p1Val < 0) ? '#ff3d71' : '#00e676';
                    }
                },
                backgroundColor: positiveGrad,
                fill: {
                    target: 'origin',
                    above: positiveGrad,
                    below: negativeGrad
                },
                borderWidth:2.5,
                tension:0.42,
                pointRadius:eqData.map((_,i)=>i===eqData.length-1?6:3),
                pointBackgroundColor:eqData.map(v=>v>=0?'#00e676':'#ff3d71'),
                pointBorderColor:eqData.map(v=>v>=0?'rgba(0,230,118,0.5)':'rgba(255,61,113,0.5)'),
                pointBorderWidth:2,
                pointShadowBlur:12,
                pointHoverRadius:8,
                pointHoverBackgroundColor:'#fff'
            }]
        },
        options:{
            responsive:true,
            maintainAspectRatio:false,
            animation:{duration:800,easing:'easeInOutQuart'},
            plugins:{
                legend:{display:false},
                tooltip:{
                    backgroundColor:'rgba(10,13,20,0.95)',
                    borderColor:'rgba(0,230,118,0.4)',
                    borderWidth:1,
                    titleColor:'#00e676',
                    bodyColor:'#e8eaf6',
                    titleFont:{family:'JetBrains Mono',size:11},
                    bodyFont:{family:'JetBrains Mono',size:10},
                    callbacks:{label:ctx=>' Rs.'+Math.round(ctx.raw).toLocaleString('en-IN')}
                }
            },
            scales:{
                x:{display:false},
                y:{
                    grid:{
                        color:ctx=>ctx.tick.value===0?'rgba(255,61,113,0.5)':'rgba(255,255,255,0.04)',
                        lineWidth:ctx=>ctx.tick.value===0?2:0.5
                    },
                    border:{color:'transparent'},
                    ticks:{
                        color:'#546e7a',
                        font:{family:'JetBrains Mono',size:10},
                        callback:v=>'₹'+v.toLocaleString('en-IN')
                    }
                }
            }
        }
    });

    // ─── 3D DRAWDOWN TRAJECTORY CHART ─────────────────────────────
    if(ddC){ddC.destroy();ddC=null;}
    
    let peakVal = 0;
    let ddData = [];
    for (let val of eqData) {
        if (val > peakVal) peakVal = val;
        ddData.push(val - peakVal);
    }
    if (ddData.length === 0) ddData = [0];

    const ctxDd = document.getElementById('ddChart').getContext('2d');
    const ddGrad = ctxDd.createLinearGradient(0,0,0,190);
    ddGrad.addColorStop(0, 'rgba(255, 61, 113, 0.01)');
    ddGrad.addColorStop(1, 'rgba(255, 61, 113, 0.35)');

    ddC = new Chart(document.getElementById('ddChart'), {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: ddData,
                borderColor: '#ff3d71',
                backgroundColor: ddGrad,
                borderWidth: 2.5,
                fill: true,
                tension: 0.42,
                pointRadius: ddData.map((_,i)=>i===ddData.length-1?6:3),
                pointBackgroundColor: '#ff3d71',
                pointBorderColor: 'rgba(255, 61, 113, 0.5)',
                pointBorderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 800, easing: 'easeInOutQuart' },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(10,13,20,0.95)',
                    borderColor: 'rgba(255, 61, 113, 0.4)',
                    borderWidth: 1,
                    titleColor: '#ff3d71',
                    bodyColor: '#e8eaf6',
                    titleFont: { family: 'JetBrains Mono', size: 11 },
                    bodyFont: { family: 'JetBrains Mono', size: 10 },
                    callbacks: { label: ctx => ' Rs.' + Math.round(ctx.raw).toLocaleString('en-IN') }
                }
            },
            scales: {
                x: { display: false },
                y: {
                    grid: {
                        color: ctx => ctx.tick.value === 0 ? 'rgba(255, 61, 113, 0.55)' : 'rgba(255, 255, 255, 0.04)',
                        lineWidth: ctx => ctx.tick.value === 0 ? 2 : 0.5
                    },
                    border: { color: 'transparent' },
                    ticks: {
                        color: '#546e7a',
                        font: { family: 'JetBrains Mono', size: 10 },
                        callback: v => '₹' + v.toLocaleString('en-IN')
                    }
                }
            }
        }
    });
    // Sparkle particle on latest point
    (function spawnSpark(){
      const sc=document.getElementById('eqSpark'); if(!sc||!eqData.length) return;
      const ctx2=sc.getContext('2d'); sc.width=sc.offsetWidth; sc.height=sc.offsetHeight;
      const particles=[]; const N=18;
      const cx=sc.width-16, cy=sc.height*0.18;
      for(let i=0;i<N;i++) particles.push({x:cx,y:cy,vx:(Math.random()-0.5)*2.2,vy:(Math.random()-1.4)*2,life:1,r:Math.random()*2+1,c:lastVal>=0?'0,230,118':'255,68,68'});
      let af;
      function draw(){
        ctx2.clearRect(0,0,sc.width,sc.height);
        let alive=false;
        for(const p of particles){
          p.x+=p.vx; p.y+=p.vy; p.vy+=0.07; p.life-=0.025;
          if(p.life<=0) continue; alive=true;
          ctx2.beginPath(); ctx2.arc(p.x,p.y,p.r*p.life,0,Math.PI*2);
          ctx2.fillStyle='rgba('+p.c+','+p.life.toFixed(2)+')';
          ctx2.shadowBlur=8; ctx2.shadowColor='rgba('+p.c+',0.8)'; ctx2.fill();
        }
        if(alive) af=requestAnimationFrame(draw); else ctx2.clearRect(0,0,sc.width,sc.height);
      }
      cancelAnimationFrame(af); draw();
    })();
    if (wlC) {
      wlC.destroy();
      wlC = null;
    }
    wlC = new Chart(document.getElementById('wl'), {
      type: 'bar',
      data: {
        labels: pnls.map((_, i) => '#' + (i + 1)),
        datasets: [{
          data: pnls,
          backgroundColor: pnls.map(v => v >= 0 ? 'rgba(0,230,118,0.7)' : 'rgba(255,68,68,0.7)'),
          borderColor: pnls.map(v => v >= 0 ? '#00e676' : '#ff4444'),
          borderWidth: 1,
          borderRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false }
        },
        scales: {
          x: {
            display: true,
            ticks: {
              color: '#546e7a',
              font: { family: 'JetBrains Mono', size: 9 }
            }
          },
          y: {
            grid: { color: 'rgba(255,255,255,0.04)' },
            ticks: {
              color: '#546e7a',
              font: { family: 'JetBrains Mono', size: 10 }
            }
          }
        }
      }
    });
    lastStats = d.stats;
  } catch(e){ document.getElementById('rb').textContent = 'Error'; }
}
let lastStats = null;

async function updateSlTp(tid) {
  const slVal = parseFloat(document.getElementById(`sl-${tid}`).value);
  const tpVal = parseFloat(document.getElementById(`tp-${tid}`).value);
  const tslVal = document.getElementById(`tsl-${tid}`).checked;
  
  if (isNaN(slVal) || isNaN(tpVal) || slVal < 0 || tpVal < 0) {
    alert("Please enter valid positive numbers for SL and TP.");
    return;
  }
  
  try {
    const r = await fetch('/api/position/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tid: tid, sl: slVal, tp: tpVal, tsl: tslVal })
    }).then(res => res.json());
    
    if (r.status === 'success') {
      alert(r.message);
      load();
    } else {
      alert("Error updating SL/TP/TSL: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  }
}

async function squareoffPosition(tid, contractLabel, contracts, ltp) {
  const label = contractLabel || tid;
  const ltpStr = ltp ? ` @ Rs.${ltp}` : '';
  const qtyStr = contracts ? ` (${contracts} contracts)` : '';
  if (!confirm(`⚠️ SQUARE OFF CONFIRMATION\n\nContract: ${label}${qtyStr}\nCurrent LTP: Rs.${ltp || '—'}\n\nAre you sure you want to EXIT this position now on Groww?\nThis will place a live SELL order at market price.`)) {
    return;
  }
  try {
    const r = await fetch('/api/position/squareoff', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tid: tid })
    }).then(res => res.json());
    
    if (r.status === 'success') {
      alert(r.message);
      load();
    } else {
      alert("Error squaring off position: " + r.message);
    }
  } catch(e) {
    alert("Connection error: " + e.message);
  }
}
function showChargesDetails(label, brokerage, gst, stt, stamp_duty, exchange_charges, sebi_fee, total) {
  const modal = document.getElementById('chargesModal');
  const body = document.getElementById('modalBody');
  body.innerHTML = `
    <div style="font-size:11.5px; color:var(--muted); margin-bottom:8px;">Scope: <span style="color:#ff9f43; font-weight:700;">${label}</span></div>
    <div style="display:flex; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.04); padding:8px 0;">
      <span style="font-size:12.5px; color:#c7d2fe;">Commission Brokerage</span>
      <span style="font-family:'JetBrains Mono',monospace; font-size:12.5px; font-weight:700; color:#fff;">Rs.${brokerage.toFixed(2)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.04); padding:8px 0;">
      <span style="font-size:12.5px; color:#c7d2fe;">Securities Transaction Tax (STT)</span>
      <span style="font-family:'JetBrains Mono',monospace; font-size:12.5px; font-weight:700; color:#fff;">Rs.${stt.toFixed(2)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.04); padding:8px 0;">
      <span style="font-size:12.5px; color:#c7d2fe;">Exchange Transaction Charges</span>
      <span style="font-family:'JetBrains Mono',monospace; font-size:12.5px; font-weight:700; color:#fff;">Rs.${exchange_charges.toFixed(2)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.04); padding:8px 0;">
      <span style="font-size:12.5px; color:#c7d2fe;">GST (Goods & Services Tax 18%)</span>
      <span style="font-family:'JetBrains Mono',monospace; font-size:12.5px; font-weight:700; color:#fff;">Rs.${gst.toFixed(2)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.04); padding:8px 0;">
      <span style="font-size:12.5px; color:#c7d2fe;">Stamp Duty</span>
      <span style="font-family:'JetBrains Mono',monospace; font-size:12.5px; font-weight:700; color:#fff;">Rs.${stamp_duty.toFixed(2)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.04); padding:8px 0;">
      <span style="font-size:12.5px; color:#c7d2fe;">SEBI Turnover Fee</span>
      <span style="font-family:'JetBrains Mono',monospace; font-size:12.5px; font-weight:700; color:#fff;">Rs.${sebi_fee.toFixed(2)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; margin-top:12px; border-top:1px solid rgba(255,140,0,0.3); padding-top:12px;">
      <span style="font-size:13.5px; font-weight:700; color:var(--text);">TOTAL DEDUCTED</span>
      <span style="font-family:'JetBrains Mono',monospace; font-size:14px; font-weight:800; color:#ff9f43;">Rs.${total.toFixed(2)}</span>
    </div>
  `;
  modal.style.display = 'flex';
}
function closeChargesModal() {
  document.getElementById('chargesModal').style.display = 'none';
}
function showOverallCharges() {
  if (lastStats) {
    showChargesDetails(
      'Session Total (All Trades)',
      lastStats.total_brokerage || 0,
      lastStats.total_gst || 0,
      lastStats.total_stt || 0,
      lastStats.total_stamp_duty || 0,
      lastStats.total_exchange_charges || 0,
      lastStats.total_sebi_fee || 0,
      lastStats.total_charges || 0
    );
  }
}
window.onclick = function(event) {
  const modal = document.getElementById('chargesModal');
  const capModal = document.getElementById('capitalModal');
  const brokerSelectModal = document.getElementById('liveBrokerSelectModal');
  const resetConfirmModal = document.getElementById('resetConfirmModal');
  const sqModal = document.getElementById('squareoffSelectModal');
  if (event.target == modal) {
    modal.style.display = 'none';
  }
  if (event.target == capModal) {
    capModal.style.display = 'none';
  }
  if (event.target == brokerSelectModal) {
    brokerSelectModal.style.display = 'none';
  }
  if (event.target == resetConfirmModal) {
    resetConfirmModal.style.display = 'none';
  }
  if (event.target == sqModal) {
    sqModal.style.display = 'none';
  }
}

function openSquareoffSelectModal() {
  if (!window.lastLoadedData || !window.lastLoadedData.open_positions || window.lastLoadedData.open_positions.length === 0) {
    alert("No active positions to square off.");
    return;
  }
  const sel = document.getElementById('squareoffSelect');
  sel.innerHTML = '';
  
  const optAll = document.createElement('option');
  optAll.value = 'ALL';
  optAll.textContent = '⚡ SQUARE OFF ALL POSITIONS';
  sel.appendChild(optAll);
  
  window.lastLoadedData.open_positions.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.tid;
    const label = p.contract_sym || `${p.index} ${p.strike}${p.opt}`;
    opt.textContent = `${label} (${p.contracts} contracts)`;
    sel.appendChild(opt);
  });
  
  document.getElementById('squareoffSelectModal').style.display = 'flex';
  updateSquareoffModalDetails();
}

function closeSquareoffSelectModal() {
  document.getElementById('squareoffSelectModal').style.display = 'none';
}

function updateSquareoffModalDetails() {
  const sel = document.getElementById('squareoffSelect');
  const val = sel.value;
  const container = document.getElementById('squareoffDetailsContainer');
  const btn = document.getElementById('squareoffActionBtn');
  
  if (val === 'ALL') {
    container.innerHTML = `
      <div style="font-size: 13px; color: #ff4757; font-weight: 700; text-align: center; padding: 10px 0;">
        ⚠️ WARNING: This will close ALL ${window.lastLoadedData.open_positions.length} active positions immediately!
      </div>
      <div style="font-size: 11.5px; color: var(--muted); text-align: center; line-height: 1.4;">
        Market sell/exit orders will be placed for all open contracts at current market rates.
      </div>
    `;
    btn.textContent = 'Square Off All';
    btn.style.background = 'linear-gradient(135deg, #ff4757, #ff6b81)';
    btn.style.boxShadow = '0 0 15px rgba(255, 71, 87, 0.35)';
  } else {
    const p = window.lastLoadedData.open_positions.find(pos => pos.tid === val);
    if (p) {
      const isCall = (p.opt || p.direction || '').toUpperCase() === 'CE';
      const typeBadge = `<span class="badge ${isCall ? 'call' : 'put'}" style="padding: 2px 6px; font-size: 9.5px; border-radius: 4px;">${p.opt || p.direction}</span>`;
      const pnlVal = +p.pnl >= 0;
      const pnlColor = pnlVal ? 'var(--green)' : 'var(--red)';
      const pnlSign = +p.pnl >= 0 ? '+' : '';
      const contractLabel = p.contract_sym || `${p.index} ${p.strike}${p.opt}`;
      
      container.innerHTML = `
        <div style="display: flex; justify-content: space-between; border-bottom: 1px solid rgba(255,255,255,0.04); padding-bottom: 8px;">
          <span style="font-size: 12.5px; color: #c7d2fe; font-weight: 600;">Contract ID / Symbol</span>
          <span style="font-family: monospace; font-size: 12.5px; font-weight: 700; color: #fff;">${contractLabel}</span>
        </div>
        <div style="display: flex; justify-content: space-between; border-bottom: 1px solid rgba(255,255,255,0.04); padding: 6px 0;">
          <span style="font-size: 12.5px; color: #c7d2fe;">Type &amp; Lots</span>
          <span style="font-size: 12.5px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 6px;">
            ${typeBadge} <span>${p.contracts} Qty (${p.lots} L)</span>
          </span>
        </div>
        <div style="display: flex; justify-content: space-between; border-bottom: 1px solid rgba(255,255,255,0.04); padding: 6px 0;">
          <span style="font-size: 12.5px; color: #c7d2fe;">Entry Price</span>
          <span style="font-family: monospace; font-size: 12.5px; font-weight: 700; color: #fff;">Rs.${p.entry.toFixed(2)}</span>
        </div>
        <div style="display: flex; justify-content: space-between; border-bottom: 1px solid rgba(255,255,255,0.04); padding: 6px 0;">
          <span style="font-size: 12.5px; color: #c7d2fe;">Current LTP</span>
          <span style="font-family: monospace; font-size: 12.5px; font-weight: 700; color: ${+p.cur >= +p.entry ? 'var(--green)' : 'var(--red)'};">Rs.${p.cur.toFixed(2)}</span>
        </div>
        <div style="display: flex; justify-content: space-between; border-bottom: 1px solid rgba(255,255,255,0.04); padding: 6px 0;">
          <span style="font-size: 12.5px; color: #c7d2fe;">Unrealized P&amp;L</span>
          <span style="font-family: monospace; font-size: 13px; font-weight: 800; color: ${pnlColor};">${pnlSign}₹${p.pnl.toFixed(2)} (${pnlSign}${p.pnl_pct.toFixed(2)}%)</span>
        </div>
        <div style="display: flex; justify-content: space-between; padding-top: 6px;">
          <span style="font-size: 12.5px; color: #c7d2fe;">SL / TP Targets</span>
          <span style="font-family: monospace; font-size: 12px; font-weight: 700; color: #ffa502;">SL: Rs.${p.sl.toFixed(1)} | TP: Rs.${p.tp.toFixed(1)}</span>
        </div>
      `;
      btn.textContent = 'Exit Selected Contract';
      btn.style.background = 'linear-gradient(135deg, #ffa502, #ffb142)';
      btn.style.boxShadow = '0 0 15px rgba(255, 165, 0, 0.35)';
    } else {
      container.innerHTML = `<div style="font-size: 12px; color: var(--muted); text-align: center;">Select a position to view details</div>`;
      btn.textContent = 'Square Off';
    }
  }
}

async function executeSquareoffFromModal() {
  const sel = document.getElementById('squareoffSelect');
  const val = sel.value;
  
  if (val === 'ALL') {
    closeSquareoffSelectModal();
    await squareOffAll();
  } else {
    const p = window.lastLoadedData.open_positions.find(pos => pos.tid === val);
    if (!p) return;
    const label = p.contract_sym || `${p.index} ${p.strike}${p.opt}`;
    if (!confirm(`⚠️ EXIT CONFIRMATION\n\nContract: ${label}\nCurrent LTP: Rs.${p.cur}\n\nAre you sure you want to EXIT this contract now?`)) {
      return;
    }
    
    const btn = document.getElementById('squareoffActionBtn');
    const oldText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'EXITING...';
    
    try {
      const r = await fetch('/api/position/squareoff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tid: val })
      }).then(res => res.json());
      
      if (r.status === 'success') {
        alert(r.message);
        closeSquareoffSelectModal();
        load();
      } else {
        alert("Error squaring off position: " + r.message);
      }
    } catch(e) {
      alert("Connection error: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  }
}

checkAuthStatus();
</script>

<!-- Modal for Selecting Contract to Square Off -->
<div id="squareoffSelectModal" style="display:none; position:fixed; z-index:200; left:0; top:0; width:100%; height:100%; overflow:auto; background-color:rgba(0,0,0,0.75); backdrop-filter:blur(5px); align-items:center; justify-content:center;">
  <div class="card" style="width:460px; background:rgba(9, 13, 26, 0.95); border:1px solid rgba(255, 71, 87, 0.4); box-shadow:0 0 30px rgba(255, 71, 87, 0.25); border-radius:16px; padding:24px; position:relative;">
    <span onclick="closeSquareoffSelectModal()" style="position:absolute; right:18px; top:12px; font-size:20px; font-weight:700; color:var(--muted); cursor:pointer; transition:color 0.2s;" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='var(--muted)'">&times;</span>
    <div class="idx-title" style="color:#ff4757; font-size:15px; font-weight:800; border-bottom:1px solid rgba(255,255,255,0.08); padding-bottom:10px; margin-bottom:16px; text-transform:uppercase; letter-spacing:1px; display:flex; align-items:center; gap:8px;">
      <span>⚡ Square Off Positions</span>
    </div>
    
    <div style="display:flex; flex-direction:column; gap:16px;">
      <div style="display:flex; flex-direction:column; gap:6px;">
        <label for="squareoffSelect" style="font-size:10.5px; font-weight:800; text-transform:uppercase; letter-spacing:1px; color:var(--muted);">Select Position / Contract</label>
        <select id="squareoffSelect" onchange="updateSquareoffModalDetails()" style="width:100%; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:10px; color:#fff; font-size:13.5px; outline:none; cursor:pointer;">
          <!-- Options will be populated dynamically -->
        </select>
      </div>
      
      <div id="squareoffDetailsContainer" style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; padding:14px; min-height:80px; display:flex; flex-direction:column; gap:10px;">
        <!-- Injected details go here -->
      </div>
      
      <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:8px;">
        <button onclick="closeSquareoffSelectModal()" style="background:transparent; border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:10px 18px; color:var(--muted); font-size:12.5px; font-weight:700; cursor:pointer; transition:all 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.03)'; this.style.color='#fff';" onmouseout="this.style.background='transparent'; this.style.color='var(--muted)';">Cancel</button>
        <button id="squareoffActionBtn" onclick="executeSquareoffFromModal()" style="background:linear-gradient(135deg, #ff4757, #ff6b81); border:none; border-radius:8px; padding:10px 22px; color:#ffffff; font-size:12.5px; font-weight:800; cursor:pointer; transition:transform 0.2s, brightness 0.2s; box-shadow:0 0 15px rgba(255, 71, 87, 0.35);" onmouseover="this.style.transform='translateY(-1px)'; this.style.filter='brightness(1.1)';" onmouseout="this.style.transform='none'; this.style.filter='none';">Square Off</button>
      </div>
    </div>
  </div>
</div>

<!-- Modal for Charges Breakdown -->
<div id="chargesModal" style="display:none; position:fixed; z-index:200; left:0; top:0; width:100%; height:100%; overflow:auto; background-color:rgba(0,0,0,0.75); backdrop-filter:blur(5px); align-items:center; justify-content:center;">
  <div class="card" style="width:450px; background:rgba(9, 13, 26, 0.95); border:1px solid rgba(255, 140, 0, 0.4); box-shadow:0 0 30px rgba(255, 140, 0, 0.25); border-radius:16px; padding:24px; position:relative;">
    <span onclick="closeChargesModal()" style="position:absolute; right:18px; top:12px; font-size:20px; font-weight:700; color:var(--muted); cursor:pointer; transition:color 0.2s;" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='var(--muted)'">&times;</span>
    <div class="idx-title" style="color:#ff9f43; font-size:15px; font-weight:800; border-bottom:1px solid rgba(255,255,255,0.08); padding-bottom:10px; margin-bottom:16px; text-transform:uppercase; letter-spacing:1px;">📊 Charges Breakdown</div>
    <div style="display:flex; flex-direction:column; gap:12px;" id="modalBody">
      <!-- Injected breakdown goes here -->
    </div>
  </div>
</div>

<!-- Modal for Live Broker Selection (Groww Only) -->
<div id="liveBrokerSelectModal" style="display:none; position:fixed; z-index:200; left:0; top:0; width:100%; height:100%; overflow:auto; background-color:rgba(0,0,0,0.8); backdrop-filter:blur(8px); align-items:center; justify-content:center;">
  <div class="card" style="width:440px; background:rgba(13, 17, 28, 0.98); border:1px solid rgba(124, 77, 255, 0.4); box-shadow:0 25px 60px rgba(0,0,0,0.8), 0 0 30px rgba(124,77,255,0.2); border-radius:20px; padding:32px; position:relative;">
    <span onclick="closeLiveBrokerSelection()" style="position:absolute; right:20px; top:15px; font-size:24px; font-weight:700; color:var(--muted); cursor:pointer; transition:color 0.2s;" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='var(--muted)'">&times;</span>
    <div class="idx-title" style="color:#7c4dff; font-size:16px; font-weight:800; border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:12px; margin-bottom:24px; text-transform:uppercase; letter-spacing:1.5px; display:flex; align-items:center; gap:8px;">
      <span>⚡ Activate Live Trading — Groww</span>
    </div>
    <p style="font-size:13px; color:var(--muted); line-height:1.6; margin-bottom:28px;">
      You are about to activate <strong style="color:#d68aff">LIVE GROWW TRADING</strong>. Real money orders will be placed via your Groww account. Please ensure your API credentials are ready.
    </p>
    
    <div style="display:flex; justify-content:center; margin-bottom:12px;">
      <!-- Groww Choice Card -->
      <div onclick="selectLiveBroker('GROWW')" style="background:rgba(124, 77, 255, 0.05); border:1.5px solid rgba(124, 77, 255, 0.4); border-radius:16px; padding:28px 40px; text-align:center; cursor:pointer; transition:all 0.3s cubic-bezier(0.4, 0, 0.2, 1); display:flex; flex-direction:column; align-items:center; gap:16px; box-shadow:0 4px 20px rgba(0,0,0,0.3);" onmouseover="this.style.borderColor='rgba(124, 77, 255, 0.9)'; this.style.background='rgba(124, 77, 255, 0.1)'; this.style.boxShadow='0 0 30px rgba(124, 77, 255, 0.35)'; this.style.transform='translateY(-4px)';" onmouseout="this.style.borderColor='rgba(124, 77, 255, 0.4)'; this.style.background='rgba(124, 77, 255, 0.05)'; this.style.boxShadow='0 4px 20px rgba(0,0,0,0.3)'; this.style.transform='none';">
        <svg style="width:56px; height:56px;" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <g clip-path="url(#growwModalClip2)">
            <rect x="0" y="0" width="24" height="24" fill="#5367F5"/>
            <path d="M 0 14.5 L 9 10.5 L 14.5 13 L 24 7.5 L 24 24 L 0 24 Z" fill="#00D09C"/>
          </g>
          <defs>
            <clipPath id="growwModalClip2">
              <circle cx="12" cy="12" r="11"/>
            </clipPath>
          </defs>
        </svg>
        <div>
          <div style="font-size:20px; font-weight:900; color:#fff; margin-bottom:6px; text-shadow:0 0 15px rgba(124,77,255,0.5);">GROWW</div>
          <div style="font-size:11px; color:#d68aff; font-weight:700; text-transform:uppercase; letter-spacing:1px;">Official Trade API</div>
          <div style="font-size:10px; color:var(--green); font-weight:600; margin-top:4px;">&#x2713; F&amp;O Options Trading</div>
        </div>
      </div>
    </div>
    
  </div>
</div>

<!-- Modal for Capital Update -->
<div id="capitalModal" style="display:none; position:fixed; z-index:200; left:0; top:0; width:100%; height:100%; overflow:auto; background-color:rgba(0,0,0,0.75); backdrop-filter:blur(5px); align-items:center; justify-content:center;">
  <div class="card" style="width:450px; background:rgba(9, 13, 26, 0.95); border:1px solid rgba(0, 240, 255, 0.4); box-shadow:0 0 30px rgba(0, 240, 255, 0.25); border-radius:16px; padding:24px; position:relative;">
    <span onclick="closeCapitalModal()" style="position:absolute; right:18px; top:12px; font-size:20px; font-weight:700; color:var(--muted); cursor:pointer; transition:color 0.2s;" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='var(--muted)'">&times;</span>
    <div class="idx-title" style="color:#00f0ff; font-size:15px; font-weight:800; border-bottom:1px solid rgba(255,255,255,0.08); padding-bottom:10px; margin-bottom:16px; text-transform:uppercase; letter-spacing:1px;">💰 Update Active Capital</div>
    <div style="display:flex; flex-direction:column; gap:16px;">
      <p style="font-size:12.5px; color:#c7d2fe; line-height:1.5; margin:0;">
        Specify your target active paper capital (Rs.). The orchestrator will dynamically re-evaluate position limits and lot splits across active trading indices.
      </p>
      <div style="display:flex; flex-direction:column; gap:6px;">
        <label for="capitalInputVal" style="font-size:10.5px; font-weight:800; text-transform:uppercase; letter-spacing:1px; color:var(--muted);">New Capital Amount (Rs.)</label>
        <input type="number" id="capitalInputVal" placeholder="e.g. 200000" style="width:100%; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:8px; padding:12px; color:#fff; font-family:'JetBrains Mono',monospace; font-size:14px; outline:none; transition:border-color 0.2s;" onfocus="this.style.borderColor='rgba(0,240,255,0.6)'" onblur="this.style.borderColor='rgba(255,255,255,0.08)'">
      </div>
      <div id="capitalModalError" style="color:var(--red); font-size:11.5px; font-weight:600; min-height:15px;"></div>
      <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:4px;">
        <button onclick="closeCapitalModal()" style="background:transparent; border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:10px 18px; color:var(--muted); font-size:12.5px; font-weight:700; cursor:pointer; transition:all 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.03)'; this.style.color='#fff';" onmouseout="this.style.background='transparent'; this.style.color='var(--muted)';">Cancel</button>
        <button id="capitalUpdateModalBtn" onclick="submitCapitalUpdate()" style="background:linear-gradient(135deg, #00b0ff, #00e5ff); border:none; border-radius:8px; padding:10px 22px; color:#060b18; font-size:12.5px; font-weight:800; cursor:pointer; transition:transform 0.2s, brightness 0.2s; box-shadow:0 0 15px rgba(0, 240, 255, 0.35);" onmouseover="this.style.transform='translateY(-1px)'; this.style.filter='brightness(1.1)';" onmouseout="this.style.transform='none'; this.style.filter='none';">Update Capital</button>
      </div>
    </div>
  </div>
</div>

<!-- Modal for Reset Confirmation -->
<div id="resetConfirmModal" style="display:none; position:fixed; z-index:200; left:0; top:0; width:100%; height:100%; overflow:auto; background-color:rgba(2, 3, 6, 0.93); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px); align-items:center; justify-content:center;">
  <div class="card" style="width:450px; background:rgba(6, 8, 18, 0.98); border:1.5px solid #ff007f; box-shadow:0 0 35px rgba(255, 0, 127, 0.25), 0 0 70px rgba(0, 240, 255, 0.15), inset 0 0 18px rgba(255, 0, 127, 0.1); border-radius:20px; padding:28px; position:relative;">
    <span onclick="closeResetConfirmModal()" style="position:absolute; right:20px; top:15px; font-size:24px; font-weight:700; color:var(--muted); cursor:pointer; transition:all 0.2s;" onmouseover="this.style.color='#ff007f'; this.style.textShadow='0 0 8px #ff007f';" onmouseout="this.style.color='var(--muted)'; this.style.textShadow='none';">&times;</span>
    <div class="idx-title" style="color:#ff007f; font-size:16px; font-weight:900; border-bottom:1px solid rgba(255,0,127,0.25); padding-bottom:12px; margin-bottom:20px; text-transform:uppercase; letter-spacing:1.5px; display:flex; align-items:center; gap:8px; text-shadow:0 0 12px rgba(255,0,127,0.65);">
      <span>⚡ CRITICAL SYSTEM RESET</span>
    </div>
    <div style="display:flex; flex-direction:column; gap:18px;">
      <div style="font-size:10px; font-weight:800; color:#00f0ff; text-transform:uppercase; letter-spacing:2px; text-shadow:0 0 8px rgba(0,240,255,0.5);">[ WARNING: DESTRUCTIVE ACTION ]</div>
      <p style="font-size:13.5px; color:#c7d2fe; line-height:1.6; margin:0; font-family:'Inter', sans-serif;">
        Are you absolutely sure you want to **RESET** all trade history records, live logs, and active capital balances to their default initial state?
      </p>
      <div style="background:rgba(255, 0, 127, 0.05); border:1.5px solid rgba(255, 0, 127, 0.35); border-left:5px solid #ff007f; padding:14px; border-radius:6px; font-size:12px; color:#ff9cc2; line-height:1.6; box-shadow:inset 0 0 10px rgba(255, 0, 127, 0.1);">
        <strong>ATTENTION:</strong> This operation is permanent and irreversible. All historical P&L analytics, capital allocations, and equity curve coordinates will be wiped clean.
      </div>
      <div style="display:flex; justify-content:flex-end; gap:14px; margin-top:10px;">
        <button onclick="closeResetConfirmModal()" style="background:transparent; border:1.5px solid rgba(0, 240, 255, 0.3); border-radius:8px; padding:11px 20px; color:#00f0ff; font-size:12.5px; font-weight:700; cursor:pointer; transition:all 0.3s; text-shadow:0 0 6px rgba(0,240,255,0.3);" onmouseover="this.style.background='rgba(0, 240, 255, 0.1)'; this.style.borderColor='#00f0ff'; this.style.boxShadow='0 0 15px rgba(0, 240, 255, 0.45)';" onmouseout="this.style.background='transparent'; this.style.borderColor='rgba(0, 240, 255, 0.3)'; this.style.boxShadow='none';">Cancel Session</button>
        <button id="modalResetConfirmBtn" onclick="confirmResetTradingLog()" style="background:linear-gradient(135deg, #ff007f, #ff00e4); border:none; border-radius:8px; padding:11px 24px; color:#ffffff; font-size:12.5px; font-weight:800; cursor:pointer; transition:all 0.3s; box-shadow:0 0 20px rgba(255, 0, 127, 0.6); text-shadow:0 0 8px rgba(255,255,255,0.55);" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 0 32px rgba(255, 0, 228, 0.95)'; this.style.filter='brightness(1.1)';" onmouseout="this.style.transform='none'; this.style.boxShadow='0 0 20px rgba(255, 0, 127, 0.6)'; this.style.filter='none';">Execute Reset</button>
      </div>
    </div>
  </div>
</div>

</body>
</html>"""

@_dashboard_app.route("/")
def dashboard_index():
    return DASHBOARD_HTML

if __name__ == "__main__":
    run()