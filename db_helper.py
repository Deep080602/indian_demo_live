import sqlite3
import hashlib
import os
import logging
from datetime import datetime
from contextlib import contextmanager

log = logging.getLogger("db_helper")

DB_DIR = "logs"
DB_PATH = os.path.join(DB_DIR, "trader_multi.db")

@contextmanager
def get_db_connection():
    """Context manager for SQLite connections with 30s timeout and automatic closing."""
    conn = None
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        yield conn
    finally:
        if conn:
            conn.close()

def init_db():
    """Create tables if they don't exist."""
    with get_db_connection() as conn:
        c = conn.cursor()
        
        # Users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_plain TEXT DEFAULT '',
                is_admin INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                last_login_at TEXT DEFAULT ''
            )
        ''')
        
        # Add columns to users table if they don't exist for legacy databases
        try:
            c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE users ADD COLUMN password_plain TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
            
        # User config table
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_config (
                user_id INTEGER PRIMARY KEY,
                dhan_client_id TEXT DEFAULT '',
                dhan_access_token TEXT DEFAULT '',
                groww_client_id TEXT DEFAULT '',
                groww_pin TEXT DEFAULT '',
                active_broker TEXT DEFAULT 'GROWW',
                live_trading INTEGER DEFAULT 0,
                trading_active INTEGER DEFAULT 1,
                capital REAL DEFAULT 200000.0,
                trading_indices TEXT DEFAULT 'NIFTY,SENSEX',
                smart_filter_enabled INTEGER DEFAULT 1,
                ai_brain_enabled INTEGER DEFAULT 1,
                trailing_sl_enabled INTEGER DEFAULT 1,
                risk_per_trade_pct REAL DEFAULT 0.05,
                target_per_trade_pct REAL DEFAULT 0.15,
                sl_on_premium_pct REAL DEFAULT 0.05,
                tp_on_premium_pct REAL DEFAULT 0.15,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        
        # Ensure default admin user exists
        c.execute("SELECT id FROM users WHERE username = 'admin'")
        if not c.fetchone():
            admin_pwd = "Madhab150972@#"
            pwd_hash = hashlib.sha256(admin_pwd.encode('utf-8')).hexdigest()
            created_at = datetime.now().isoformat()
            c.execute("INSERT INTO users (username, password_hash, password_plain, is_admin, created_at, last_login_at) VALUES (?, ?, ?, ?, ?, ?)",
                      ("admin", pwd_hash, admin_pwd, 1, created_at, created_at))
            admin_id = c.lastrowid
            c.execute("INSERT INTO user_config (user_id) VALUES (?)", (admin_id,))
        
        # Positions table
        c.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                tid TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                client_id TEXT DEFAULT '',
                index_name TEXT NOT NULL,
                direction TEXT NOT NULL,
                strike REAL NOT NULL,
                opt TEXT NOT NULL,
                expiry TEXT NOT NULL,
                lots INTEGER NOT NULL,
                contracts INTEGER NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                entry_time TEXT NOT NULL,
                entry_spot REAL NOT NULL,
                e9_15 REAL DEFAULT 0.0,
                e21_15 REAL DEFAULT 0.0,
                cur REAL NOT NULL,
                peak REAL NOT NULL,
                exit_px REAL DEFAULT 0.0,
                exit_time TEXT,
                exit_reason TEXT DEFAULT '',
                pnl REAL DEFAULT 0.0,
                groww_order_id TEXT DEFAULT '',
                groww_sec_id TEXT DEFAULT '',
                broker TEXT DEFAULT 'GROWW',
                entry_charges REAL DEFAULT 0.0,
                exit_charges REAL DEFAULT 0.0,
                charges REAL DEFAULT 0.0,
                brokerage REAL DEFAULT 0.0,
                gst REAL DEFAULT 0.0,
                stt REAL DEFAULT 0.0,
                stamp_duty REAL DEFAULT 0.0,
                exchange_charges REAL DEFAULT 0.0,
                sebi_fee REAL DEFAULT 0.0,
                vix REAL DEFAULT 15.0,
                atr_pct REAL DEFAULT 0.005,
                guard_status TEXT DEFAULT 'Disabled',
                predicted_win_prob REAL DEFAULT 100.0,
                is_super_order INTEGER DEFAULT 0,
                trailing_sl_enabled INTEGER DEFAULT 1,
                is_open INTEGER DEFAULT 1,
                is_live INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        
        # Ensure columns exist for legacy databases
        try:
            c.execute("ALTER TABLE positions ADD COLUMN is_live INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE positions ADD COLUMN client_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE positions ADD COLUMN groww_order_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE positions ADD COLUMN groww_sec_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
            
        # Copy data from legacy columns to new ones if they exist
        try:
            c.execute("UPDATE positions SET groww_order_id = dhan_order_id WHERE (groww_order_id = '' OR groww_order_id IS NULL) AND (dhan_order_id != '' AND dhan_order_id IS NOT NULL)")
            c.execute("UPDATE positions SET groww_sec_id = dhan_sec_id WHERE (groww_sec_id = '' OR groww_sec_id IS NULL) AND (dhan_sec_id != '' AND dhan_sec_id IS NOT NULL)")
        except sqlite3.OperationalError:
            pass
            
        conn.commit()
    log.info(f"[DB] Database initialized successfully at {DB_PATH}")

def _hash_password(password: str) -> str:
    """Helper to hash password securely with SHA256."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def register_user(username, password, is_admin=0) -> bool:
    """Register a new user and create their default config."""
    try:
        init_db()
        with get_db_connection() as conn:
            c = conn.cursor()
            pwd_hash = _hash_password(password)
            created_at = datetime.now().isoformat()
            
            c.execute("INSERT INTO users (username, password_hash, password_plain, is_admin, created_at, last_login_at) VALUES (?, ?, ?, ?, ?, ?)", 
                      (username.strip(), pwd_hash, password, is_admin, created_at, created_at))
            user_id = c.lastrowid
            
            # Insert default config for this user
            c.execute("INSERT INTO user_config (user_id) VALUES (?)", (user_id,))
            conn.commit()
            log.info(f"[DB] Registered user: {username} (ID: {user_id})")
            return True
    except sqlite3.IntegrityError:
        log.warning(f"[DB] Username already exists: {username}")
        return False
    except Exception as e:
        log.error(f"[DB] Error registering user {username}: {e}")
        return False

def verify_user(username, password) -> int:
    """Verify login and return user_id. Returns -1 if invalid. Supports case-insensitive and lenient fallback."""
    try:
        username_clean = username.strip()
        if not username_clean:
            return -1
        pwd_hash = _hash_password(password)
        with get_db_connection() as conn:
            c = conn.cursor()
            
            # 1. Try exact match
            c.execute("SELECT id FROM users WHERE username = ? AND password_hash = ?", (username_clean, pwd_hash))
            row = c.fetchone()
            user_id = row[0] if row else -1
            
            # 2. Try case-insensitive match
            if user_id == -1:
                c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?) AND password_hash = ?", (username_clean, pwd_hash))
                row = c.fetchone()
                if row:
                    user_id = row[0]
                    
            # 3. Try lenient match (remove spaces, dots, hyphens, underscores)
            if user_id == -1:
                import re
                def normalize(s):
                    return re.sub(r'[^a-zA-Z0-9]', '', s).lower()
                norm_input = normalize(username_clean)
                c.execute("SELECT id, username, password_hash FROM users")
                for r in c.fetchall():
                    if normalize(r[1]) == norm_input and r[2] == pwd_hash:
                        user_id = r[0]
                        break
                        
            if user_id != -1:
                # Update last login
                c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (datetime.now().isoformat(), user_id))
                conn.commit()
                return user_id
    except Exception as e:
        log.error(f"[DB] Error verifying user {username}: {e}")
    return -1

def get_user_config(user_id) -> dict:
    """Get config dictionary for a user."""
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM user_config WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            if not row:
                return {}
            res = dict(row)
            
            # Fetch username
            c.execute("SELECT username FROM users WHERE id = ?", (user_id,))
            user_row = c.fetchone()
            if user_row:
                res["username"] = user_row[0]
            return res
    except Exception as e:
        log.error(f"[DB] Error getting config for user {user_id}: {e}")
    return {}

def update_user_config(user_id, updates: dict) -> bool:
    """Update config values dynamically."""
    if not updates:
        return True
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            fields = []
            values = []
            for k, v in updates.items():
                fields.append(f"{k} = ?")
                values.append(v)
            values.append(user_id)
            
            query = f"UPDATE user_config SET {', '.join(fields)} WHERE user_id = ?"
            c.execute(query, tuple(values))
            conn.commit()
            log.info(f"[DB] Updated user config for user ID {user_id}")
            return True
    except Exception as e:
        log.error(f"[DB] Error updating config for user {user_id}: {e}")
        return False

def get_active_user_ids() -> list:
    """Get all user IDs who have trading active."""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT user_id FROM user_config WHERE trading_active = 1")
            rows = c.fetchall()
            return [row[0] for row in rows]
    except Exception as e:
        log.error(f"[DB] Error getting active user IDs: {e}")
        return []

def save_position(user_id, pos) -> bool:
    """Save/update a position record with active broker client_id."""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            
            # Resolve active client ID if not explicitly set
            client_id = getattr(pos, "client_id", "")
            is_live_pos = 1 if getattr(pos, "is_live", False) else 0
            if not client_id and is_live_pos:
                c.execute("SELECT groww_client_id FROM user_config WHERE user_id = ?", (user_id,))
                row = c.fetchone()
                if row:
                    client_id = row[0] or ""
            
            c.execute('''
                INSERT OR REPLACE INTO positions (
                    tid, user_id, client_id, index_name, direction, strike, opt, expiry, lots, contracts,
                    entry, sl, tp, entry_time, entry_spot, e9_15, e21_15, cur, peak,
                    exit_px, exit_time, exit_reason, pnl, groww_order_id, groww_sec_id, broker,
                    entry_charges, exit_charges, charges, brokerage, gst, stt, stamp_duty,
                    exchange_charges, sebi_fee, vix, atr_pct, guard_status, predicted_win_prob,
                    is_super_order, trailing_sl_enabled, is_open, is_live
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
            ''', (
                pos.tid, user_id, client_id, pos.index, pos.direction, pos.strike, pos.opt, pos.expiry, pos.lots, pos.contracts,
                pos.entry, pos.sl, pos.tp, pos.entry_time.isoformat() if isinstance(pos.entry_time, datetime) else pos.entry_time,
                pos.entry_spot, pos.e9_15, pos.e21_15, pos.cur, pos.peak,
                pos.exit_px, pos.exit_time.isoformat() if isinstance(pos.exit_time, datetime) else pos.exit_time,
                pos.exit_reason, pos.pnl, pos.groww_order_id, pos.groww_sec_id, pos.broker,
                pos.entry_charges, pos.exit_charges, pos.charges, pos.brokerage, pos.gst, pos.stt, pos.stamp_duty,
                pos.exchange_charges, pos.sebi_fee, pos.vix, pos.atr_pct, pos.guard_status, pos.predicted_win_prob,
                1 if pos.is_super_order else 0, 1 if pos.trailing_sl_enabled else 0, 1 if pos.is_open else 0,
                is_live_pos
            ))
            conn.commit()
            return True
    except Exception as e:
        log.error(f"[DB] Error saving position {pos.tid} for user {user_id}: {e}")
        return False

def load_user_open_positions(user_id) -> dict:
    """Load all open positions for a user as a dictionary of Pos object-compatible dicts, filtered by client ID in live mode."""
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # Fetch active user config
            c.execute("SELECT groww_client_id, live_trading FROM user_config WHERE user_id = ?", (user_id,))
            config_row = c.fetchone()
            active_client_id = ""
            live_trading = False
            if config_row:
                active_client_id = config_row["groww_client_id"] or ""
                live_trading = bool(config_row["live_trading"])
                    
            if live_trading:
                c.execute("""
                    SELECT * FROM positions 
                    WHERE user_id = ? AND is_open = 1 AND is_live = 1 AND client_id = ?
                """, (user_id, active_client_id))
            else:
                c.execute("""
                    SELECT * FROM positions 
                    WHERE user_id = ? AND is_open = 1 AND is_live = 0
                """, (user_id,))
                
            rows = c.fetchall()
            
            res = {}
            for r in rows:
                d = dict(r)
                if d["entry_time"]:
                    try: d["entry_time"] = datetime.fromisoformat(d["entry_time"])
                    except: pass
                if d["exit_time"]:
                    try: d["exit_time"] = datetime.fromisoformat(d["exit_time"])
                    except: pass
                else:
                    d["exit_time"] = None
                d["is_super_order"] = bool(d["is_super_order"])
                d["trailing_sl_enabled"] = bool(d["trailing_sl_enabled"])
                d["is_live"] = bool(d.get("is_live", 0))
                d["client_id"] = d.get("client_id", "")
                res[d["tid"]] = d
            return res
    except Exception as e:
        log.error(f"[DB] Error loading open positions for user {user_id}: {e}")
        return {}

def load_user_trade_history(user_id, is_live = None) -> list:
    """Load closed trades for a user, optionally filtered by live/demo mode and client ID."""
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # Fetch active user config
            c.execute("SELECT groww_client_id FROM user_config WHERE user_id = ?", (user_id,))
            config_row = c.fetchone()
            active_client_id = ""
            if config_row:
                active_client_id = config_row["groww_client_id"] or ""
                    
            if is_live is None:
                c.execute("SELECT * FROM positions WHERE user_id = ? AND is_open = 0 ORDER BY exit_time DESC", (user_id,))
            elif is_live:
                c.execute("""
                    SELECT * FROM positions 
                    WHERE user_id = ? AND is_open = 0 AND is_live = 1 AND client_id = ? 
                    ORDER BY exit_time DESC
                """, (user_id, active_client_id))
            else:
                c.execute("""
                    SELECT * FROM positions 
                    WHERE user_id = ? AND is_open = 0 AND is_live = 0 
                    ORDER BY exit_time DESC
                """, (user_id,))
                
            rows = c.fetchall()
            
            res = []
            for r in rows:
                d = dict(r)
                d["date"] = d["entry_time"][:10] if d["entry_time"] else ""
                d["is_live"] = bool(d.get("is_live", 0))
                d["client_id"] = d.get("client_id", "")
                d["exit"] = d.get("exit_px", 0.0)
                d["reason"] = d.get("exit_reason", "")
                res.append(d)
            return res
    except Exception as e:
        log.error(f"[DB] Error loading trade history for user {user_id}: {e}")
        return []

def delete_user_trade_history(user_id) -> bool:
    """Delete all closed positions/trades and open positions for a user (Reset)."""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM positions WHERE user_id = ?", (user_id,))
            conn.commit()
            return True
    except Exception as e:
        log.error(f"[DB] Error deleting trade history for user {user_id}: {e}")
        return False

def get_user_id_by_username(username: str) -> int:
    """Resolve user_id from username. Returns -1 if not found. Supports case-insensitive and lenient fallback."""
    try:
        username_clean = username.strip()
        if not username_clean:
            return -1
        with get_db_connection() as conn:
            c = conn.cursor()
            
            # 1. Try exact match
            c.execute("SELECT id FROM users WHERE username = ?", (username_clean,))
            row = c.fetchone()
            if row:
                return row[0]
                
            # 2. Try case-insensitive match
            c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username_clean,))
            row = c.fetchone()
            if row:
                return row[0]
                
            # 3. Try lenient match (remove spaces, dots, hyphens, underscores)
            import re
            def normalize(s):
                return re.sub(r'[^a-zA-Z0-9]', '', s).lower()
            norm_input = normalize(username_clean)
            c.execute("SELECT id, username FROM users")
            for r in c.fetchall():
                if normalize(r[1]) == norm_input:
                    return r[0]
    except Exception as e:
        log.error(f"[DB] Error getting user_id for {username}: {e}")
    return -1

def is_admin_user(user_id) -> bool:
    """Check if user is admin."""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
            row = c.fetchone()
            if row:
                return bool(row[0])
    except Exception as e:
        log.error(f"[DB] Error checking if admin user: {e}")
    return False

def get_all_users() -> list:
    """Get all registered users with credentials and config for admin dashboard view."""
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT u.id, u.username, u.password_plain, u.is_admin, u.created_at, u.last_login_at, 
                       uc.trading_active, uc.groww_client_id, uc.active_broker, uc.live_trading, uc.capital 
                FROM users u
                LEFT JOIN user_config uc ON u.id = uc.user_id
                ORDER BY CASE WHEN u.username = 'admin' THEN 0 ELSE 1 END, u.is_admin DESC, u.id ASC
            """)
            rows = c.fetchall()
            res = []
            for r in rows:
                d = dict(r)
                user_id = d["id"]
                live_trading = bool(d.get("live_trading", 0))
                
                # Fetch sum of P&L from closed positions for this user
                c2 = conn.cursor()
                c2.execute("SELECT sum(pnl) FROM positions WHERE user_id = ? AND is_open = 0 AND is_live = ?", (user_id, 1 if live_trading else 0))
                pnl_row = c2.fetchone()
                total_pnl = pnl_row[0] if (pnl_row and pnl_row[0] is not None) else 0.0
                
                d["capital"] = d.get("capital") if d.get("capital") is not None else 200000.0
                d["total_pnl"] = round(total_pnl, 2)
                
                if d.get("created_at"):
                    try:
                        # Format date to normal format YYYY-MM-DD HH:MM:SS
                        dt_part = d["created_at"].split(".")[0].replace("T", " ")
                        d["created_at"] = dt_part
                    except Exception:
                        pass
                
                last_login = d.get("last_login_at", "")
                active_today = False
                if last_login:
                    try:
                        # Format date to normal format YYYY-MM-DD HH:MM:SS
                        dt_part = last_login.split(".")[0].replace("T", " ")
                        d["last_login_at"] = dt_part
                        
                        # Compare date with today's date
                        last_login_date = last_login.split("T")[0]
                        today_date = datetime.now().isoformat().split("T")[0]
                        if last_login_date == today_date:
                            active_today = True
                    except Exception:
                        pass
                else:
                    d["last_login_at"] = "Never"
                
                d["active_today"] = active_today
                res.append(d)
            return res
    except Exception as e:
        log.error(f"[DB] Error getting all users: {e}")
        return []

def delete_user(user_id) -> bool:
    """Delete a user account and associated configurations/positions (due to cascade)."""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return True
    except Exception as e:
        log.error(f"[DB] Error deleting user {user_id}: {e}")
        return False

def modify_user(user_id, username=None, password=None, is_admin=None) -> bool:
    """Modify a user account credentials or role."""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            fields = []
            params = []
            if username:
                fields.append("username = ?")
                params.append(username)
            if password:
                pwd_hash = _hash_password(password)
                fields.append("password_hash = ?")
                params.append(pwd_hash)
                fields.append("password_plain = ?")
                params.append(password)
            if is_admin is not None:
                fields.append("is_admin = ?")
                params.append(1 if is_admin else 0)
            
            if not fields:
                return True
                
            params.append(user_id)
            query = f"UPDATE users SET {', '.join(fields)} WHERE id = ?"
            c.execute(query, tuple(params))
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        log.warning(f"[DB] Username already exists when modifying user {user_id}")
        return False
    except Exception as e:
        log.error(f"[DB] Error modifying user {user_id}: {e}")
        return False

def update_user_active(user_id) -> bool:
    """Update last_login_at timestamp for a user to mark them active."""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (datetime.now().isoformat(), user_id))
            conn.commit()
            return True
    except Exception as e:
        log.error(f"[DB] Error updating user active time for ID {user_id}: {e}")
        return False


