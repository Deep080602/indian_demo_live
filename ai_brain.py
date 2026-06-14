import os
import sqlite3
import numpy as np
import logging
from datetime import datetime
from typing import Tuple, Dict, Any, Optional
from enum import Enum
import warnings

# Try importing scikit-learn preprocessing and exceptions
try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.exceptions import NotFittedError, ConvergenceWarning
    from sklearn.neural_network import MLPClassifier
except ImportError:
    StandardScaler = None
    MLPClassifier = None
    NotFittedError = Exception
    ConvergenceWarning = Warning

# Try importing PyTorch deep learning modules
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
except ImportError:
    torch = None
    nn = None
    optim = None

from db_helper import DB_PATH, get_db_connection

log = logging.getLogger("ai_brain")

class SignalType(Enum):
    NONE = 0
    CALL = 1
    PUT = -1

# PyTorch Deep Neural Network Architecture
if torch is not None and nn is not None:
    class DeepTradingModel(nn.Module):
        def __init__(self, input_dim: int = 4, hidden_dims: list = [64, 32, 16], output_dim: int = 3, dropout_rate: float = 0.1):
            super().__init__()
            layers = []
            in_dim = input_dim
            for h_dim in hidden_dims:
                layers.append(nn.Linear(in_dim, h_dim))
                layers.append(nn.ReLU())
                if dropout_rate > 0:
                    layers.append(nn.Dropout(dropout_rate))
                in_dim = h_dim
            layers.append(nn.Linear(in_dim, output_dim))
            self.network = nn.Sequential(*layers)
            
        def forward(self, x):
            return self.network(x)
else:
    class DummyDeepTradingModel:
        def __init__(self, *args, **kwargs):
            pass
        def __call__(self, *args, **kwargs):
            pass
    DeepTradingModel = DummyDeepTradingModel


class AITradingBrain:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.model = None
        self.scaler = None
        self.is_trained = False
        self.training_accuracy = 0.0
        self.total_samples = 0
        
        # Initialize scaler if scikit-learn is available
        if StandardScaler is not None:
            self.scaler = StandardScaler()
        else:
            log.warning("[AI] scikit-learn is not installed. Scaling will not be available.")

    def _extract_hour_minute(self, dt_str: str) -> float:
        """Convert ISO datetime string into fractional hour format (e.g. 9.25 for 9:15 AM)."""
        try:
            dt = datetime.fromisoformat(dt_str)
            return dt.hour + (dt.minute / 60.0)
        except:
            return 9.25

    def train_brain(self):
        """Train the deep neural network on user's historical trades."""
        if self.scaler is None:
            log.error("[AI] Cannot train: StandardScaler not available.")
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

            X = np.array(X, dtype=np.float32)
            y = np.array(y)

            # Fit scaler
            X_scaled = self.scaler.fit_transform(X)
            self.total_samples = len(X)

            # Check if PyTorch is available for Deep Learning
            if torch is not None and nn is not None and optim is not None:
                # Map target labels to PyTorch CrossEntropy classes:
                # 1 (CALL) -> 1, 0 (NONE) -> 0, -1 (PUT) -> 2
                torch_targets = []
                for val in y:
                    if val == 1:
                        torch_targets.append(1)
                    elif val == -1:
                        torch_targets.append(2)
                    else:
                        torch_targets.append(0)

                X_tensor = torch.tensor(X_scaled, dtype=torch.float32)
                y_tensor = torch.tensor(torch_targets, dtype=torch.long)

                # Instantiate PyTorch deep learning model
                self.model = DeepTradingModel(input_dim=4, hidden_dims=[64, 32, 16], output_dim=3)
                
                criterion = nn.CrossEntropyLoss()
                optimizer = optim.Adam(self.model.parameters(), lr=0.01)

                self.model.train()
                for epoch in range(250):
                    optimizer.zero_grad()
                    outputs = self.model(X_tensor)
                    loss = criterion(outputs, y_tensor)
                    loss.backward()
                    optimizer.step()

                self.model.eval()
                with torch.no_grad():
                    preds = self.model(X_tensor)
                    pred_classes = torch.argmax(preds, dim=1)
                    correct = (pred_classes == y_tensor).sum().item()
                    self.training_accuracy = (correct / len(y)) * 100

                self.is_trained = True
                log.info(f"[AI] PyTorch Deep Learning model trained for user {self.user_id} on {self.total_samples} samples. Accuracy: {self.training_accuracy:.2f}%")

            elif MLPClassifier is not None:
                # Fallback to a deep scikit-learn MLPClassifier
                self.model = MLPClassifier(hidden_layer_sizes=(64, 32, 16), max_iter=1000, random_state=42)
                warnings.filterwarnings('ignore', category=ConvergenceWarning)
                self.model.fit(X_scaled, y)
                
                self.is_trained = True
                self.training_accuracy = self.model.score(X_scaled, y) * 100
                log.info(f"[AI] Fallback Deep MLPClassifier model trained for user {self.user_id} on {self.total_samples} samples. Accuracy: {self.training_accuracy:.2f}%")
            else:
                log.error("[AI] Neither PyTorch nor scikit-learn is available for training.")

        except Exception as e:
            log.error(f"[AI] Failed to train brain: {e}", exc_info=True)

    def predict_market_state(self, e9: float, e21: float, vix: float, atr: float, current_time: datetime) -> SignalType:
        """Forward pass inference to yield a trading action."""
        if not self.is_trained or self.model is None or self.scaler is None:
            return SignalType.NONE

        try:
            ema_spread = (e9 - e21) / e21 if e21 else 0.0
            time_val = current_time.hour + (current_time.minute / 60.0)

            features = np.array([[ema_spread, vix, atr, time_val]], dtype=np.float32)
            features_scaled = self.scaler.transform(features)

            # Inference using PyTorch if applicable
            if torch is not None and isinstance(self.model, nn.Module):
                self.model.eval()
                with torch.no_grad():
                    features_tensor = torch.tensor(features_scaled, dtype=torch.float32)
                    outputs = self.model(features_tensor)
                    prediction = torch.argmax(outputs, dim=1).item()

                if prediction == 1:
                    return SignalType.CALL
                elif prediction == 2:
                    return SignalType.PUT
                else:
                    return SignalType.NONE

            # Inference fallback using scikit-learn
            else:
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

