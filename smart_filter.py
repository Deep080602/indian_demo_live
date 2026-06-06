"""
smart_filter.py — Smart Trade Guard using RandomForestClassifier.
Trains on logs/trades.csv and blocks low-probability trades.
"""

import os
import csv
import logging
import pickle
import pandas as pd
import numpy as np
from datetime import datetime
from config import cfg

log = logging.getLogger("smart_filter")

MODEL_FILE = f"{cfg.log_dir}/smart_guard_model.pkl"

class SmartTradeFilter:
    def __init__(self):
        self.model = None
        self.trained = False
        self.features = [
            "Index", "Direction", "Strike", "EntryPrice", "Lots", 
            "VIX", "ATR_Pct", "EMA9_15m", "EMA21_15m", "EMA_Spread_15m", 
            "Hour", "Minute"
        ]
        self.accuracy = 0.0
        self.samples_count = 0
        self.wins_count = 0
        self.losses_count = 0
        self.filtered_count = 0
        
        # Load/Train model on initialization
        self.train_model()

    def train_model(self):
        """Train or retrain the RandomForestClassifier on logs/trades.csv data."""
        csv_path = f"{cfg.log_dir}/trades.csv"
        if not os.path.exists(csv_path):
            log.info("[Guard] No historical trades found (no trades.csv). Smart filter bypassed.")
            self.samples_count = 0
            self.wins_count = 0
            self.losses_count = 0
            self.accuracy = 0.0
            self.trained = False
            self.model = None
            return

        try:
            # Read CSV using pandas
            df = pd.read_csv(csv_path)
            if df.empty or len(df) < cfg.smart_min_trades:
                log.info(f"[Guard] Insufficient data. Found {len(df)} trades, need at least {cfg.smart_min_trades} to train. Running in learning mode.")
                self.samples_count = len(df)
                self.wins_count = 0
                self.losses_count = 0
                self.accuracy = 0.0
                self.trained = False
                self.model = None
                
                # Extract actual win/loss counts from the few existing trades to display on dashboard
                if not df.empty and "PnL_Rs" in df.columns:
                    try:
                        pnl_vals = df["PnL_Rs"].dropna().astype(float)
                        self.wins_count = sum(pnl_vals > 0)
                        self.losses_count = sum(pnl_vals <= 0)
                    except Exception as ex:
                        log.debug(f"[Guard] Error parsing wins/losses for Learning Mode: {ex}")
                return

            # Feature extraction and encoding
            # Map Index: NIFTY=0, SENSEX=1
            df["Index_Code"] = df["Index"].map({"NIFTY": 0.0, "SENSEX": 1.0}).fillna(0.0)
            
            # Map Direction: CALL=1, PUT=0
            df["Direction_Code"] = df["Direction"].map({"CALL": 1.0, "PUT": 0.0}).fillna(1.0)
            
            # Strike and EntryPrice
            df["Strike_Val"] = df["Strike"].astype(float)
            df["EntryPrice_Val"] = df["EntryPrice"].astype(float)
            df["Lots_Val"] = df["Lots"].astype(float)
            
            # Technical columns
            df["VIX_Val"] = df.get("VIX", pd.Series([15.0] * len(df))).fillna(15.0).astype(float)
            df["ATR_Pct_Val"] = df.get("ATR_Pct", pd.Series([0.005] * len(df))).fillna(0.005).astype(float)
            df["EMA9_15m_Val"] = df.get("EMA9_15m", df.get("EMA9", pd.Series([0.0] * len(df)))).fillna(0.0).astype(float)
            df["EMA21_15m_Val"] = df.get("EMA21_15m", df.get("EMA21", pd.Series([0.0] * len(df)))).fillna(0.0).astype(float)
            df["EMA_Spread_15m_Val"] = abs(df["EMA9_15m_Val"] - df["EMA21_15m_Val"]).astype(float)
            
            # Time components from EntryTime (format: H:M:S)
            df["Hour"] = df["EntryTime"].apply(lambda t: int(str(t).split(":")[0]) if ":" in str(t) else 9)
            df["Minute"] = df["EntryTime"].apply(lambda t: int(str(t).split(":")[1]) if ":" in str(t) else 15)

            # Build feature matrix X
            X = pd.DataFrame({
                "Index": df["Index_Code"],
                "Direction": df["Direction_Code"],
                "Strike": df["Strike_Val"],
                "EntryPrice": df["EntryPrice_Val"],
                "Lots": df["Lots_Val"],
                "VIX": df["VIX_Val"],
                "ATR_Pct": df["ATR_Pct_Val"],
                "EMA9_15m": df["EMA9_15m_Val"],
                "EMA21_15m": df["EMA21_15m_Val"],
                "EMA_Spread_15m": df["EMA_Spread_15m_Val"],
                "Hour": df["Hour"],
                "Minute": df["Minute"]
            })
            
            # Build target y: 1 if PnL_Rs > 0 else 0
            y = (df["PnL_Rs"].astype(float) > 0).astype(int)

            self.samples_count = len(df)
            self.wins_count = sum(y)
            self.losses_count = len(y) - self.wins_count

            # Check class balance
            if len(y.unique()) < 2:
                log.info(f"[Guard] Data is highly single-sided ({self.wins_count} W / {self.losses_count} L). Skipping training until balanced.")
                return

            # Train Random Forest
            from sklearn.ensemble import RandomForestClassifier
            # Keep hyperparameters constrained to avoid overfitting on small datasets
            self.model = RandomForestClassifier(n_estimators=30, max_depth=4, random_state=42)
            self.model.fit(X, y)
            self.trained = True

            # Calculate training accuracy as a proxy
            self.accuracy = float(self.model.score(X, y))

            # Save the trained model
            os.makedirs(cfg.log_dir, exist_ok=True)
            with open(MODEL_FILE, "wb") as f:
                pickle.dump(self.model, f)

            log.info(f"[Guard] 🛡️ Smart filter successfully trained! Accuracy={self.accuracy*100:.1f}% | Samples={self.samples_count} ({self.wins_count} W / {self.losses_count} L)")
        except Exception as e:
            log.error(f"[Guard] Error training model: {e}")

    def evaluate_signal(self, index: str, direction: str, strike: float, entry_price: float, lots: float, 
                         vix: float, atr_pct: float, ema9_15m: float, ema21_15m: float) -> tuple:
        """
        Evaluate a trade signal using the trained classifier model.
        Returns: (should_block, win_probability_pct)
        """
        if not self.trained or self.model is None:
            # Bypass if not trained
            return False, 100.0

        try:
            # Prepare feature vector
            idx_code = 0.0 if index == "NIFTY" else 1.0
            dir_code = 1.0 if direction == "CALL" else 0.0
            ema_spread = abs(ema9_15m - ema21_15m)
            
            now_ist = datetime.now()
            hour = now_ist.hour
            minute = now_ist.minute

            feature_dict = {
                "Index": idx_code,
                "Direction": dir_code,
                "Strike": strike,
                "EntryPrice": entry_price,
                "Lots": lots,
                "VIX": vix,
                "ATR_Pct": atr_pct,
                "EMA9_15m": ema9_15m,
                "EMA21_15m": ema21_15m,
                "EMA_Spread_15m": ema_spread,
                "Hour": hour,
                "Minute": minute
            }

            # Convert to DataFrame to match features names exactly
            X_inst = pd.DataFrame([feature_dict])
            
            # Predict probabilities
            probs = self.model.predict_proba(X_inst)[0] # class 0 (loss), class 1 (win)
            win_prob = float(probs[1])
            win_prob_pct = round(win_prob * 100, 1)

            # Block if below threshold
            should_block = win_prob < cfg.smart_win_threshold
            
            if should_block:
                self.filtered_count += 1
                log.info(f"[SMART GUARD] 🛡️ Signal for {index} {direction} {strike} REJECTED | Predicted Win Prob = {win_prob_pct}% (Threshold: {cfg.smart_win_threshold*100}%)")
            else:
                log.info(f"[SMART GUARD] ✅ Signal for {index} {direction} {strike} APPROVED | Predicted Win Prob = {win_prob_pct}%")

            return should_block, win_prob_pct

        except Exception as e:
            log.error(f"[Guard] Inference error: {e}")
            return False, 100.0

    def get_status(self) -> dict:
        """Get status dictionary for the Dashboard API."""
        return {
            "enabled": cfg.smart_filter_enabled,
            "status": "🛡️ ACTIVE / PROTECTED" if self.trained else "✏️ LEARNING MODE",
            "accuracy": f"{self.accuracy * 100:.1f}%" if self.trained else "—",
            "total_samples": self.samples_count,
            "wins": self.wins_count,
            "losses": self.losses_count,
            "filtered_count": self.filtered_count,
            "threshold": f"{cfg.smart_win_threshold * 100:.0f}%"
        }

# Singleton instance
smart_filter = SmartTradeFilter()
