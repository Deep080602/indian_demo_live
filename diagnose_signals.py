"""
diagnose_signals.py — Real-time diagnostic report for Dhan Options Strategy.
Inspects current market trend, pullbacks, and volatility to explain why a trade was not taken.
Run this script using: python diagnose_signals.py
"""

import sys
import os
import logging
import pandas as pd
from datetime import datetime

# Configure clean console printing
print("=" * 65)
print(" 🔍 AUTOMATED STRATEGY DIAGNOSTICS & SIGNAL REPORTER")
print("=" * 65)

try:
    from config import cfg, INDEX_CONFIG
    from demo_trade import _fetch_ohlcv, _ema, _atr, _get_vix, _is_noisy, _now
except ImportError as e:
    print(f"❌ Dependency Import Error: {e}")
    print("Please make sure you are in the correct workspace directory.")
    sys.exit(1)

def diagnose_index(index: str):
    print(f"\n⚡ DIAGNOSING: {index}")
    print("-" * 65)
    
    # 1. Fetch OHLCV
    df5 = _fetch_ohlcv("5m", index)
    df15 = _fetch_ohlcv("15m", index)
    
    if df5 is None or len(df5) < 21:
        print("❌ 5m Chart Data: FAILED to fetch or has insufficient candles.")
        return
    if df15 is None or len(df15) < 21:
        print("❌ 15m Chart Data: FAILED to fetch or has insufficient candles.")
        return
        
    print(f"✅ 5m Chart Data : Loaded successfully ({len(df5)} candles)")
    print(f"✅ 15m Chart Data: Loaded successfully ({len(df15)} candles)")

    # 2. VIX Check
    vix = _get_vix()
    vix_passed = 10.0 <= vix <= 25.0
    vix_status = "✅ PASS" if vix_passed else "❌ FAIL (Out of 10.0 - 25.0 limits)"
    print(f"📈 Volatility VIX : {vix:.2f} | {vix_status}")

    # 3. Market Noise Check
    noise = _is_noisy()
    noise_status = "❌ FAIL (Inside opening/closing noisy period)" if noise else "✅ PASS (Calm session period)"
    print(f"⏳ Session Window : {noise_status}")

    # 4. Trend Stack Check (15m Primary)
    c15 = df15["close"]
    e9_15  = float(_ema(c15, 9).iloc[-1])
    e21_15 = float(_ema(c15, 21).iloc[-1])
    e50_15 = float(_ema(c15, 50).iloc[-1])
    atr15  = float(_atr(df15).iloc[-1])
    ltp15  = float(c15.iloc[-1])
    atr_pct = atr15 / ltp15 if ltp15 > 0 else 0
    
    print(f"\n📊 15m Higher Timeframe Trend Stack:")
    print(f" - Last Price (LTP) : {ltp15:.2f}")
    print(f" - EMA 9   (Fast)   : {e9_15:.2f}")
    print(f" - EMA 21  (Medium) : {e21_15:.2f}")
    print(f" - EMA 50  (Slow)   : {e50_15:.2f}")
    
    direction = None
    if e9_15 > e21_15 > e50_15:
        direction = "CALL"
        trend_status = "✅ PASS: Strong UPTREND Stack (EMA9 > EMA21 > EMA50)"
    elif e9_15 < e21_15 < e50_15:
        direction = "PUT"
        trend_status = "✅ PASS: Strong DOWNTREND Stack (EMA9 < EMA21 < EMA50)"
    else:
        trend_status = "❌ FAIL: EMAs are crossed / sideways (No clean stacked trend)"
        
    print(f" - Trend Status    : {trend_status}")

    # 5. Volatility Movement Check (ATR)
    atr_passed = atr_pct >= 0.003
    ema_sep = abs(e9_15 - e21_15)
    atr_status = "✅ PASS"
    if not atr_passed:
        if ema_sep < 10:
            atr_status = "❌ FAIL (Low volatility & weak trend separation)"
        else:
            atr_status = "✅ PASS (Low volatility but backed by a strong trend separation)"
    print(f"⚡ Volatility ATR% : {atr_pct*100:.3f}% (Min: 0.3%) | {atr_status}")

    # 6. Entry Logic Trigger (5m Chart)
    c5 = df5["close"]
    e9_5 = float(_ema(c5, 9).iloc[-1])
    e21_5 = float(_ema(c5, 21).iloc[-1])
    cur = df5.iloc[-1]
    prev = df5.iloc[-2]
    
    cur_close = float(cur["close"])
    prev_close = float(prev["close"])
    cur_high = float(cur["high"])
    cur_low = float(cur["low"])
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    
    print(f"\n📉 5m Lower Timeframe Entry Checks:")
    print(f" - Last Price (LTP) : {cur_close:.2f}")
    print(f" - EMA 9   (Fast)   : {e9_5:.2f}")
    print(f" - EMA 21  (Medium) : {e21_5:.2f}")
    
    if direction is None:
        print(" 🚫 Entry Logic Skipped (Failed trend stack check)")
    else:
        entry_signal = False
        reasons = []
        
        if direction == "CALL":
            cond1 = prev_close <= e21_5 and cur_close > e9_5
            cond2 = e9_5 < cur_close < e21_5
            cond3 = cur_close > e9_5 > e21_5 and (cur_high > prev_high or cur_close > prev_close)
            
            reasons.append(f"Classic Pullback to EMA21: {'✅ TRIGGERED' if cond1 else '❌ NO (Price did not pull back to EMA21 & cross EMA9)'}")
            reasons.append(f"Consolidation between EMA9-21: {'✅ TRIGGERED' if cond2 else '❌ NO (Price not consolidating inside EMA channel)'}")
            reasons.append(f"Strong Trend Continuation: {'✅ TRIGGERED' if cond3 else '❌ NO (Price not setting higher high/close above EMA9)'}")
            
            if cond1 or cond2 or cond3:
                entry_signal = True
        else: # PUT
            cond1 = prev_close >= e21_5 and cur_close < e9_5
            cond2 = e9_5 > cur_close > e21_5
            cond3 = cur_close < e9_5 < e21_5 and (cur_low < prev_low or cur_close < prev_close)
            
            reasons.append(f"Classic Pullback to EMA21: {'✅ TRIGGERED' if cond1 else '❌ NO (Price did not pull back to EMA21 & cross below EMA9)'}")
            reasons.append(f"Consolidation between EMA9-21: {'✅ TRIGGERED' if cond2 else '❌ NO (Price not consolidating inside EMA channel)'}")
            reasons.append(f"Strong Trend Continuation: {'✅ TRIGGERED' if cond3 else '❌ NO (Price not setting lower low/close below EMA9)'}")
            
            if cond1 or cond2 or cond3:
                entry_signal = True
                
        print(f" - Entry Targets for {direction} direction:")
        for r in reasons:
            print(f"   * {r}")
            
        entry_status = "🔥 BUY SIGNAL ACTIVATED!" if entry_signal else "😴 STANDBY (No pullback or breakout triggered)"
        print(f" - Entry Status    : {entry_status}")

def run_diagnostics():
    for index in INDEX_CONFIG.keys():
        try:
            diagnose_index(index)
        except Exception as e:
            print(f"❌ Error diagnosing {index}: {e}")
    print("\n" + "=" * 65)

if __name__ == "__main__":
    run_diagnostics()
