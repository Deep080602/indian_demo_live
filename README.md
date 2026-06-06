# Dhan Options Paper Trader — Institutional Grade

A production-ready, confluence-based options paper trading system for Indian indices
(NIFTY / BANKNIFTY / FINNIFTY / SENSEX) using the **Dhan API**.

---

## Project Structure

```
dhan_algo/
├── config.py           ← ALL configuration lives here
├── dhan_client.py      ← Dhan API wrapper (retry, rate-limit, parsing)
├── market_data.py      ← Data feed manager (caching, multi-timeframe)
├── indicators.py       ← EMA, RSI, ATR, VWAP, Bollinger, Supertrend, MACD
├── smc_analysis.py     ← Smart Money Concepts (OB, BOS, CHOCH, FVG)
├── strategy_engine.py  ← Confluence signal generator (core logic)
├── risk_manager.py     ← Position sizing, drawdown, daily halt
├── paper_trader.py     ← Simulated execution, MTM, SL/TP, CSV logging
├── main.py             ← Main trading loop
├── backtest.py         ← Historical replay backtester
├── requirements.txt
└── logs/
    ├── algo.log        ← Runtime logs
    └── trades.csv      ← Trade ledger
```

---

## 1. Strategy Logic (Step-by-Step)

### Multi-Timeframe Confluence Framework

```
SCAN EVERY 60 SECONDS DURING MARKET HOURS
│
├── VOLATILITY CHECK
│   ├── VIX between 11–22 (configurable)
│   └── ATR% > 0.3% (enough intraday range)
│
├── HTF TREND (15m)
│   ├── EMA-9 > EMA-21 > EMA-50 (bullish) OR inverse (bearish)
│   ├── Close above/below EMA-50
│   └── Supertrend: majority of last 5 bars in direction
│
├── SMC ANALYSIS (5m)
│   ├── Market structure: HH/HL (bull) or LH/LL (bear)
│   ├── Break of Structure (BOS) confirms trend
│   ├── CHOCH = reversal signal (bonus score)
│   └── Price near Order Block or Fair Value Gap
│
├── MTF PRICE ACTION (5m)
│   ├── Price above VWAP (bull) / below VWAP (bear)
│   └── Supertrend alignment with HTF
│
├── RSI FILTER (1m)
│   ├── Bullish: RSI 40–65 (not overbought)
│   └── Bearish: RSI 35–60 (not oversold)
│
├── LTF ENTRY CANDLE (1m)
│   ├── Bullish engulfing or Hammer at support
│   └── Bearish engulfing or Shooting star at resistance
│
├── MINIMUM SCORE: 65/100 to generate signal
│
└── OPTION SELECTION
    ├── ATM / 1-strike ITM (configurable)
    ├── OI > 100,000, Volume > 1,000
    ├── Spread < 1.5% of mid
    └── Premium: ₹20 – ₹300
```

### Scoring Matrix

| Condition                    | Points |
|------------------------------|--------|
| VIX + ATR in range           | +10    |
| HTF EMA trend confirmed      | +20    |
| SMC trend aligns with HTF    | +15    |
| BOS confirmed                | +10    |
| CHOCH reversal signal        | +10    |
| Price above/below VWAP       | +10    |
| Price inside Order Block     | +15    |
| Price inside FVG             | +10    |
| RSI in buy/sell zone         | +5     |
| LTF entry candle             | +15    |
| Supertrend aligned HTF+MTF   | +10    |
| **MINIMUM TO TRADE**         | **65** |

---

## 2. Trade Conditions

### Entry Rules
- Signal score ≥ 65/100
- No existing open position
- Daily trades < max_trades_per_day (default: 3)
- Daily P&L > -3% of capital (drawdown halt)
- Time: 09:20 – 15:15
- VIX: 11–22

### Exit Rules (in priority order)
1. **Stop Loss**: Premium drops 50% from entry (e.g., enter ₹100 → SL at ₹50)
2. **Trailing Stop**: Once +50% gain, trail at 30% below peak
3. **Target**: Premium up 150% from entry (e.g., enter ₹100 → TP at ₹250)
4. **Force Exit**: Hard close all positions at 15:20

---

## 3. Risk Management

### Position Sizing Formula
```
risk_amount     = capital × 5%
max_loss_per_lot = entry_premium × 50% × lot_size
lots            = floor(risk_amount / max_loss_per_lot)
```

**Example (NIFTY, capital ₹5,00,000):**
```
risk_amount     = 5,00,000 × 5% = ₹25,000
premium         = ₹100
max_loss/lot    = 100 × 50% × 25 = ₹1,250
lots            = 25,000 / 1,250 = 20 lots  (capped at max_spend = 20% of capital)
```

### Daily Protection
- **Daily Drawdown Limit**: -3% of capital → halt all new trades
- **Max Trades/Day**: 3 (prevent overtrading in choppy conditions)
- **Max Open Positions**: 1 at a time (directional clarity)

---

## 4. Setup & Deployment

### Prerequisites
```bash
Python 3.10+
pip install -r requirements.txt
```

### Step 1: Configure Credentials
Edit `config.py` or set environment variables:
```bash
export DHAN_CLIENT_ID="your_client_id"
export DHAN_ACCESS_TOKEN="your_access_token"
```

### Step 2: Configure Trading Parameters
Open `config.py` and set:
```python
capital     = 500_000      # Your paper capital
index       = "NIFTY"      # Or BANKNIFTY / FINNIFTY / SENSEX
timeframe   = "intraday"
```

### Step 3: Run Paper Trader
```bash
# Local machine
python main.py

# Background (Linux/Mac)
nohup python main.py > /dev/null 2>&1 &

# With process manager (recommended for VPS)
pip install supervisor
# Configure supervisord.conf (see below)
```

### Step 4: Run Backtest
```bash
python backtest.py --from 2024-07-01 --to 2024-12-31 --capital 500000
```

### Step 5: Monitor Trades
```bash
# Live tail of logs
tail -f logs/algo.log

# View trade ledger
cat logs/trades.csv
```

---

## 5. VPS Deployment (Recommended for Production)

### Supervisor Configuration
```ini
; /etc/supervisor/conf.d/dhan_algo.conf
[program:dhan_algo]
command=python /home/ubuntu/dhan_algo/main.py
directory=/home/ubuntu/dhan_algo
autostart=true
autorestart=true
startretries=5
stderr_logfile=/var/log/dhan_algo.err.log
stdout_logfile=/var/log/dhan_algo.out.log
environment=DHAN_CLIENT_ID="YOUR_ID",DHAN_ACCESS_TOKEN="YOUR_TOKEN"
```

### Crontab (Alternative)
```bash
# Start at 9:00 AM on weekdays
0 9 * * 1-5 cd /home/ubuntu/dhan_algo && python main.py >> logs/algo.log 2>&1
```

### Recommended VPS Specs
- Ubuntu 22.04 LTS
- 2 vCPU, 2GB RAM (more than sufficient)
- Mumbai region (lowest latency to NSE/Dhan)
- Providers: DigitalOcean, AWS Mumbai, Hetzner

---

## 6. Risk Analysis

### Theoretical Risk Parameters

| Parameter                  | Value        |
|----------------------------|--------------|
| Risk per trade             | 5% of capital|
| Max loss on premium        | 50%          |
| Target on premium          | 150%         |
| R:R Ratio (raw)            | 3:1          |
| Max trades/day             | 3            |
| Daily drawdown halt        | 3%           |
| Worst case (3 SL/day)      | -15% capital |
| Daily halt triggers at     | -3%          |

### Expected Realistic Performance
Based on confluence strategies in Indian markets:

| Metric              | Conservative | Moderate | Optimistic |
|---------------------|-------------|----------|------------|
| Win Rate            | 35%         | 45%      | 55%        |
| Avg trades/month    | 15          | 20       | 25         |
| Monthly return      | -2% to +5%  | +3–8%    | +8–15%     |

**Expectancy formula:**
```
E = (Win% × Avg_Win) − (Loss% × Avg_Loss)
  = (0.45 × 150%) − (0.55 × 50%)  [on premium]
  = 67.5% − 27.5% = +40% on premium (if sizing correctly)
```

### Why This Will NOT Have 90% Win Rate
- Markets are random 60–70% of the time
- No strategy consistently beats 55% in options buying
- Options have theta decay working against buyers
- **The edge is in R:R (3:1), not in win rate**

---

## 7. Upgrading to Live Trading

To convert from paper to live trading, change only these lines in `paper_trader.py`:

```python
# In open_trade(), replace the comment block with:
# LIVE TRADING (REMOVE PAPER TRADING GUARD FIRST):
order = dhan._dhan.place_order(
    security_id=signal.option_security_id,
    exchange_segment="NSE_FNO",
    transaction_type="BUY",
    quantity=size.contracts,
    order_type="LIMIT",
    product_type="INTRADAY",
    price=fill_price,
    validity="DAY",
)
```

**Do NOT go live without:**
1. ≥ 3 months of consistent paper trading results
2. Minimum 50 paper trades logged
3. Positive expectancy demonstrated in backtest + paper mode
4. Understanding of options Greeks and margin requirements

---

## 8. Telegram Alerts (Optional)

Add to `config.py`:
```python
telegram_bot_token: str = "YOUR_BOT_TOKEN"
telegram_chat_id:   str = "YOUR_CHAT_ID"
```

Add to `paper_trader.py` after each open/close:
```python
import requests
def _send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    requests.post(url, data={"chat_id": cfg.telegram_chat_id, "text": msg})
```

---

## Disclaimer

This software is for **paper trading and educational purposes only**.
Options trading involves substantial risk of loss. Past performance
(including backtests) does not guarantee future results.
Always consult a SEBI-registered advisor before live trading.
