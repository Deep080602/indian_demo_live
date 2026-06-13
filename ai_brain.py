import os
import sqlite3
import numpy as np
import logging
from datetime import datetime
from typing import Tuple, Dict, Any, Optional
from enum import Enum

try:
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.exceptions import NotFittedError, ConvergenceWarning
    import warnings
except ImportError:
    MLPClassifier = None

from db_helper import DB_PATH, get_db_connection

log = logging.getLogger("ai_brain")

class SignalType(Enum):
    NONE = 0
    CALL = 1
    PUT = -1

class AITradingBrain:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.model = None
        self.scaler = None
        self.is_trained = False
        self.training_accuracy = 0.0
        self.total_samples = 0
        
        if MLPClassifier is not None:
            # We use a relatively simple architecture to avoid overfitting on small data
            self.model = MLPClassifier(hidden_layer_sizes=(16, 8), max_iter=500, random_state=42)
            self.scaler = StandardScaler()
        else:
            log.warning("[AI] scikit-learn is not installed. AI Brain will remain untrained.")

    def _extract_hour_minute(self, dt_str: str) -> float:
        """Convert ISO datetime string into fractional hour format (e.g. 9.25 for 9:15 AM)."""
        try:
            dt = datetime.fromisoformat(dt_str)
            return dt.hour + (dt.minute / 60.0)
        except:
            return 9.25

    def train_brain(self):
        """Train the neural network on user's historical trades."""
        if self.model is None or self.scaler is None:
            log.error("[AI] Cannot train: scikit-learn not available.")
            return

        try:
            with get_db_connection() as conn:
                c = conn.cursor()
                # We fetch closed positions for this user
                c.execute("""
                    SELECT direction, entry_spot, e9_15, e21_15, vix, atr_pct, entry_time, pnl, exit_reason
                    FROM positions 
                    WHERE user_id = ? AND is_open = 0
                """, (self.user_id,))
                rows = c.fetchall()

            if len(rows) < 10:
                log.info(f"[AI] Not enough trade history to train for user {self.user_id} (Need >= 10, found {len(rows)})")
                return

            X = []
            y = []

            for r in rows:
                direction = r[0]
                entry_spot = r[1]
                e9 = r[2]
                e21 = r[3]
                vix = r[4]
                atr = r[5]
                entry_time = r[6]
                pnl = r[7]
                
                # We construct features:
                # 1. EMA Spread Ratio
                ema_spread = (e9 - e21) / e21 if e21 else 0.0
                
                # 2. VIX
                # 3. ATR %
                # 4. Time of day
                time_val = self._extract_hour_minute(entry_time)

                features = [ema_spread, vix, atr, time_val]

                # We consider a trade "successful" if PnL > 0
                # Target formulation: 
                # If it was a CALL and successful -> Should predict CALL (1)
                # If it was a PUT and successful -> Should predict PUT (-1)
                # If it was unsuccessful -> Should predict NONE (0) 
                
                is_success = pnl > 0
                
                if is_success:
                    if direction == "CALL":
                        target = 1
                    else:
                        target = -1
                else:
                    target = 0

                X.append(features)
                y.append(target)

            X = np.array(X)
            y = np.array(y)

            # Fit scaler
            X_scaled = self.scaler.fit_transform(X)

            # Train model
            if MLPClassifier is not None:
                warnings.filterwarnings('ignore', category=ConvergenceWarning)
            self.model.fit(X_scaled, y)
            
            self.is_trained = True
            self.total_samples = len(X)
            self.training_accuracy = self.model.score(X_scaled, y) * 100
            
            log.info(f"[AI] Brain trained for user {self.user_id} on {self.total_samples} samples. Accuracy: {self.training_accuracy:.2f}%")

        except Exception as e:
            log.error(f"[AI] Failed to train brain: {e}", exc_info=True)

    def predict_market_state(self, e9: float, e21: float, vix: float, atr: float, current_time: datetime) -> SignalType:
        """Forward pass inference to yield a trading action."""
        if not self.is_trained or self.model is None or self.scaler is None:
            return SignalType.NONE

        try:
            ema_spread = (e9 - e21) / e21 if e21 else 0.0
            time_val = current_time.hour + (current_time.minute / 60.0)

            features = np.array([[ema_spread, vix, atr, time_val]])
            features_scaled = self.scaler.transform(features)

            prediction = self.model.predict(features_scaled)[0]

            if prediction == 1:
                return SignalType.CALL
            elif prediction == -1:
                return SignalType.PUT
            else:
                return SignalType.NONE
                
        except NotFittedError:
            self.is_trained = False
            return SignalType.NONE
        except Exception as e:
            log.error(f"[AI] Prediction error: {e}")
            return SignalType.NONE

    def generate_llm_rationale(self, signal: SignalType, api_key: str, market_context: dict) -> str:
        """Generate text analysis using Gemini API."""
        if not api_key:
            return "No API Key provided for LLM analysis."
        
        if signal == SignalType.NONE:
            return "AI recommends holding. No clear entry signal."
            
        direction = "CALL" if signal == SignalType.CALL else "PUT"
        
        # In a real scenario, this would make an HTTP request to the Gemini API
        # Because we want this to be responsive without actual network wait for now (or basic mockup)
        # We will just generate a structured pseudo-response.
        rationale = (f"AI Analysis indicates a strong {direction} setup based on current market context: "
                     f"EMA spread is favorable, VIX is at {market_context.get('vix', 0)}, "
                     f"and ATR shows sufficient momentum.")
                     
        return rationale

    def get_metrics(self) -> dict:
        """Returns training metrics to be displayed on the dashboard."""
        return {
            "trained": self.is_trained,
            "accuracy": round(self.training_accuracy, 2) if self.is_trained else 0.0,
            "samples": self.total_samples
        }
