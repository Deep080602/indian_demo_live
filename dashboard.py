"""
dashboard.py — NIFTY Paper Trader Dashboard (Flask)
Displays real-time positions, P&L, market data, and trade history
"""

import csv
import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template_string
import pandas as pd

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")
LOG_DIR = "logs"

# ─── SHARED STATE ──────────────────────────────────────────────────────────
_state = {
    "nifty_spot": 0.0,
    "vix": 15.0,
    "open_positions": [],
    "closed_trades": [],
    "capital": 200_000,
    "day_pnl": 0.0,
    "cum_pnl": 0.0,
    "day_trades": 0,
    "market_status": "CLOSED",
    "last_update": "",
}

def _load_trades():
    """Load trade history from CSV."""
    path = os.path.join(LOG_DIR, "trades.csv")
    if not os.path.exists(path):
        return []
    try:
        df = pd.read_csv(path)
        return df.to_dict("records")
    except:
        return []

def _load_capital():
    """Load capital from JSON."""
    path = os.path.join(LOG_DIR, "capital.json")
    if not os.path.exists(path):
        return 200_000
    try:
        with open(path) as f:
            data = json.load(f)
            return int(float(data.get("capital", 200_000)))
    except:
        return 200_000

def _calculate_stats(trades):
    """Calculate trade statistics."""
    if not trades:
        return {}

    pnl_list = [t.get("PnL_Rs", 0) for t in trades if t.get("PnL_Rs")]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": f"{len(wins)/len(trades)*100:.1f}%" if trades else "0%",
        "total_pnl": f"Rs.{sum(pnl_list):+,.0f}",
        "avg_win": f"Rs.{sum(wins)/len(wins):+,.0f}" if wins else "Rs.0",
        "avg_loss": f"Rs.{sum(losses)/len(losses):+,.0f}" if losses else "Rs.0",
        "best_trade": f"Rs.{max(pnl_list):+,.0f}" if pnl_list else "Rs.0",
        "worst_trade": f"Rs.{min(pnl_list):+,.0f}" if pnl_list else "Rs.0",
    }

def _update_state():
    """Update dashboard state periodically."""
    while True:
        try:
            trades = _load_trades()
            _state["closed_trades"] = trades
            _state["capital"] = _load_capital()
            _state["last_update"] = datetime.now(IST).strftime("%H:%M:%S")

            # Calculate daily stats
            if trades:
                today = datetime.now(IST).strftime("%Y-%m-%d")
                today_trades = [t for t in trades if str(t.get("Date", "")).startswith(today)]
                day_pnl = sum([float(t.get("PnL_Rs", 0)) for t in today_trades])
                _state["day_pnl"] = day_pnl
                _state["day_trades"] = len(today_trades)

            # Cumulative PnL
            if trades:
                _state["cum_pnl"] = sum([float(t.get("PnL_Rs", 0)) for t in trades])

            time.sleep(5)
        except Exception as e:
            print(f"[DASH] State update error: {e}")
            time.sleep(5)

# Start background update thread
_update_thread = threading.Thread(target=_update_state, daemon=True)
_update_thread.start()

# ─── HTML TEMPLATE ────────────────────────────────────────────────────────
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NIFTY Paper Trader Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: #fff;
            padding: 20px;
            min-height: 100vh;
        }

        .header {
            text-align: center;
            margin-bottom: 30px;
            border-bottom: 2px solid rgba(255,255,255,0.1);
            padding-bottom: 20px;
        }

        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }

        .header .status {
            font-size: 0.9em;
            opacity: 0.8;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .card {
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 10px;
            padding: 20px;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px rgba(0,0,0,0.1);
        }

        .card.highlight {
            border: 2px solid #4CAF50;
            background: rgba(76, 175, 80, 0.1);
        }

        .card.warning {
            border: 2px solid #ff9800;
            background: rgba(255, 152, 0, 0.1);
        }

        .card.danger {
            border: 2px solid #f44336;
            background: rgba(244, 67, 54, 0.1);
        }

        .card-title {
            font-size: 0.9em;
            opacity: 0.8;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .card-value {
            font-size: 2em;
            font-weight: bold;
            margin-bottom: 5px;
        }

        .card-subtitle {
            font-size: 0.85em;
            opacity: 0.7;
        }

        .positive {
            color: #4CAF50;
        }

        .negative {
            color: #f44336;
        }

        .neutral {
            color: #2196F3;
        }

        .table-section {
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 30px;
            backdrop-filter: blur(10px);
        }

        .table-section h2 {
            margin-bottom: 20px;
            font-size: 1.5em;
            border-bottom: 2px solid rgba(255,255,255,0.2);
            padding-bottom: 10px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th {
            background: rgba(255,255,255,0.1);
            padding: 15px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid rgba(255,255,255,0.2);
            text-transform: uppercase;
            font-size: 0.85em;
            letter-spacing: 0.5px;
        }

        td {
            padding: 12px 15px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }

        tr:hover {
            background: rgba(255,255,255,0.05);
        }

        .chart-container {
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 30px;
            backdrop-filter: blur(10px);
        }

        .chart-title {
            margin-bottom: 20px;
            font-size: 1.5em;
            border-bottom: 2px solid rgba(255,255,255,0.2);
            padding-bottom: 10px;
        }

        .empty-state {
            text-align: center;
            padding: 40px;
            opacity: 0.7;
            font-style: italic;
        }

        .refresh-info {
            text-align: center;
            font-size: 0.85em;
            opacity: 0.6;
            margin-top: 20px;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }

        .updating {
            animation: pulse 1s infinite;
        }

        /* Premium Chart Selector Tabs */
        .chart-tabs {
            display: flex;
            gap: 8px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 20px;
            padding: 3px;
        }
        .chart-tab {
            background: transparent;
            border: none;
            border-radius: 16px;
            padding: 5px 14px;
            color: rgba(255, 255, 255, 0.6);
            font-size: 11px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.25s ease;
            outline: none;
        }
        .chart-tab.active {
            background: rgba(33, 150, 243, 0.25);
            color: #2196F3;
            text-shadow: 0 0 8px rgba(33, 150, 243, 0.5);
            box-shadow: 0 0 10px rgba(33, 150, 243, 0.1);
        }
        .chart-tab:hover:not(.active) {
            color: #fff;
            background: rgba(255, 255, 255, 0.05);
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📈 NIFTY Paper Trader Dashboard</h1>
        <div class="status">
            <span id="market-status">LOADING...</span> |
            Last Update: <span id="last-update">--:--:--</span>
        </div>
    </div>

    <div class="container">
        <!-- Key Metrics Grid -->
        <div class="grid">
            <div class="card">
                <div class="card-title">Current Capital</div>
                <div class="card-value" id="capital">₹--</div>
                <div class="card-subtitle">Account Balance</div>
            </div>

            <div class="card" id="day-pnl-card">
                <div class="card-title">Day P&L</div>
                <div class="card-value" id="day-pnl">₹0</div>
                <div class="card-subtitle" id="day-pnl-pct">0 trades today</div>
            </div>

            <div class="card" id="cum-pnl-card">
                <div class="card-title">Cumulative P&L</div>
                <div class="card-value" id="cum-pnl">₹0</div>
                <div class="card-subtitle" id="cum-pnl-pct">Total Session</div>
            </div>

            <div class="card">
                <div class="card-title">NIFTY Spot</div>
                <div class="card-value neutral" id="nifty-spot">--</div>
                <div class="card-subtitle">Current Index</div>
            </div>

            <div class="card">
                <div class="card-title">VIX Level</div>
                <div class="card-value neutral" id="vix">--</div>
                <div class="card-subtitle">Volatility Index</div>
            </div>

            <div class="card">
                <div class="card-title">Open Positions</div>
                <div class="card-value" id="open-positions">0</div>
                <div class="card-subtitle">Active Trades</div>
            </div>
        </div>

        <!-- Statistics Grid -->
        <div class="grid">
            <div class="card">
                <div class="card-title">Total Trades</div>
                <div class="card-value" id="stat-trades">0</div>
            </div>

            <div class="card">
                <div class="card-title">Win Rate</div>
                <div class="card-value" id="stat-win-rate">--%</div>
                <div class="card-subtitle">
                    <span id="stat-wins">0</span>W /
                    <span id="stat-losses">0</span>L
                </div>
            </div>

            <div class="card">
                <div class="card-title">Avg Win</div>
                <div class="card-value positive" id="stat-avg-win">₹0</div>
            </div>

            <div class="card">
                <div class="card-title">Avg Loss</div>
                <div class="card-value negative" id="stat-avg-loss">₹0</div>
            </div>

            <div class="card">
                <div class="card-title">Best Trade</div>
                <div class="card-value positive" id="stat-best">₹0</div>
            </div>

            <div class="card">
                <div class="card-title">Worst Trade</div>
                <div class="card-value negative" id="stat-worst">₹0</div>
            </div>
        </div>

        <!-- Trade History -->
        <div class="table-section">
            <h2>📋 Trade History</h2>
            <div id="trade-table-container">
                <div class="empty-state">No trades recorded yet</div>
            </div>
        </div>

        <!-- P&L Chart -->
        <div class="chart-container">
            <div class="chart-title" style="display:flex;justify-content:space-between;align-items:center;">
                <span>💹 Cumulative P&L & Drawdown</span>
                <div class="chart-tabs">
                    <button id="btnEquityWeb" class="chart-tab active" onclick="switchWebChart('equity')">📈 Equity Curve</button>
                    <button id="btnDrawdownWeb" class="chart-tab" onclick="switchWebChart('drawdown')">📉 Drawdown</button>
                </div>
            </div>
            <div style="position:relative;" id="equityWebChartContainer">
                <canvas id="pnl-chart"></canvas>
            </div>
            <div style="position:relative;display:none;" id="drawdownWebChartContainer">
                <canvas id="ddChart"></canvas>
            </div>
        </div>

        <!-- Win/Loss Distribution -->
        <div class="chart-container">
            <div class="chart-title">📊 Trade Statistics</div>
            <canvas id="stats-chart" style="max-height: 300px;"></canvas>
        </div>

        <div class="refresh-info">
            Dashboard updates every 5 seconds • All times in IST
        </div>
    </div>

    <script>
        let pnlChart = null;
        let statsChart = null;
        let ddChart = null;

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

        async function updateDashboard() {
            try {
                const response = await axios.get('/api/data');
                const data = response.data;

                // Update key metrics
                document.getElementById('capital').textContent =
                    `₹${data.capital.toLocaleString('en-IN', {maximumFractionDigits: 0})}`;
                document.getElementById('nifty-spot').textContent =
                    data.nifty_spot.toFixed(0) || '--';
                document.getElementById('vix').textContent =
                    data.vix.toFixed(1) || '--';
                document.getElementById('last-update').textContent = data.last_update;

                // Day P&L
                const dayPnl = data.day_pnl;
                const dayPnlCard = document.getElementById('day-pnl-card');
                dayPnlCard.className = 'card';
                if (dayPnl > 0) {
                    dayPnlCard.classList.add('highlight');
                    document.getElementById('day-pnl').className = 'card-value positive';
                } else if (dayPnl < 0) {
                    dayPnlCard.classList.add('danger');
                    document.getElementById('day-pnl').className = 'card-value negative';
                }
                document.getElementById('day-pnl').textContent =
                    `₹${dayPnl.toLocaleString('en-IN', {maximumFractionDigits: 0})}`;
                document.getElementById('day-pnl-pct').textContent =
                    `${data.day_trades} trades today`;

                // Cumulative P&L
                const cumPnl = data.cum_pnl;
                const cumPnlCard = document.getElementById('cum-pnl-card');
                cumPnlCard.className = 'card';
                if (cumPnl > 0) {
                    cumPnlCard.classList.add('highlight');
                    document.getElementById('cum-pnl').className = 'card-value positive';
                } else if (cumPnl < 0) {
                    cumPnlCard.classList.add('danger');
                    document.getElementById('cum-pnl').className = 'card-value negative';
                }
                document.getElementById('cum-pnl').textContent =
                    `₹${cumPnl.toLocaleString('en-IN', {maximumFractionDigits: 0})}`;

                // Statistics
                const stats = data.stats;
                document.getElementById('stat-trades').textContent = stats.total_trades;
                document.getElementById('stat-win-rate').textContent = stats.win_rate;
                document.getElementById('stat-wins').textContent = stats.wins;
                document.getElementById('stat-losses').textContent = stats.losses;
                document.getElementById('stat-avg-win').textContent = stats.avg_win;
                document.getElementById('stat-avg-loss').textContent = stats.avg_loss;
                document.getElementById('stat-best').textContent = stats.best_trade;
                document.getElementById('stat-worst').textContent = stats.worst_trade;

                // Trade table
                updateTradeTable(data.closed_trades);

                // Charts
                updateCharts(data.closed_trades, data.day_pnl, data.cum_pnl);

            } catch (error) {
                console.error('Dashboard update error:', error);
            }
        }

        function updateTradeTable(trades) {
            const container = document.getElementById('trade-table-container');

            if (!trades || trades.length === 0) {
                container.innerHTML = '<div class="empty-state">No trades recorded yet</div>';
                return;
            }

            let html = `
                <table>
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Strike</th>
                            <th>Type</th>
                            <th>Entry</th>
                            <th>Exit</th>
                            <th>P&L</th>
                            <th>%</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            trades.slice().reverse().forEach(trade => {
                const pnl = parseFloat(trade.PnL_Rs || 0);
                const pnlClass = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral';
                const reason = trade.ExitReason || '-';

                html += `
                    <tr>
                        <td>${trade.EntryTime || '-'}</td>
                        <td>${Math.round(trade.Strike)}</td>
                        <td>${trade.OptType || '-'}</td>
                        <td>₹${parseFloat(trade.EntryPrice).toFixed(2)}</td>
                        <td>₹${parseFloat(trade.ExitPrice).toFixed(2)}</td>
                        <td class="${pnlClass}">₹${pnl.toFixed(0)}</td>
                        <td class="${pnlClass}">${parseFloat(trade.PnL_Pct || 0).toFixed(2)}%</td>
                        <td>${reason}</td>
                    </tr>
                `;
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        }

        function updateCharts(trades, dayPnl, cumPnl) {
            // P&L Trend Chart
            if (!trades || trades.length === 0) {
                if (pnlChart) pnlChart.destroy();
                pnlChart = null;
                if (ddChart) ddChart.destroy();
                ddChart = null;
                return;
            }

            const pnlData = [];
            let cumulative = 0;
            trades.forEach(trade => {
                cumulative += parseFloat(trade.PnL_Rs || 0);
                pnlData.push(cumulative);
            });

            const labels = trades.map((t, i) => `Trade ${i + 1}`);

            const pnlCtx = document.getElementById('pnl-chart');
            if (!pnlCtx) return;

            if (pnlChart) pnlChart.destroy();
            
            const ctxEq = pnlCtx.getContext('2d');
            const positiveGrad = ctxEq.createLinearGradient(0,0,0,190);
            positiveGrad.addColorStop(0, 'rgba(76, 175, 80, 0.35)');
            positiveGrad.addColorStop(1, 'rgba(76, 175, 80, 0.01)');
            
            const negativeGrad = ctxEq.createLinearGradient(0,0,0,190);
            negativeGrad.addColorStop(0, 'rgba(244, 67, 54, 0.01)');
            negativeGrad.addColorStop(1, 'rgba(244, 67, 54, 0.35)');

            pnlChart = new Chart(pnlCtx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Cumulative P&L (₹)',
                        data: pnlData,
                        borderColor: '#4CAF50',
                        segment: {
                            borderColor: ctx => {
                                const p0Val = ctx.p0.parsed.y;
                                const p1Val = ctx.p1.parsed.y;
                                return (p0Val < 0 || p1Val < 0) ? '#f44336' : '#4CAF50';
                            }
                        },
                        backgroundColor: positiveGrad,
                        fill: {
                            target: 'origin',
                            above: positiveGrad,
                            below: negativeGrad
                        },
                        borderWidth: 2.5,
                        tension: 0.4,
                        pointRadius: pnlData.map((_,i)=>i===pnlData.length-1?6:3),
                        pointBackgroundColor: pnlData.map(v=>v>=0?'#4CAF50':'#f44336'),
                        pointBorderColor: pnlData.map(v=>v>=0?'rgba(76, 175, 80, 0.5)':'rgba(244, 67, 54, 0.5)'),
                        pointBorderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: 'rgba(10,13,20,0.95)',
                            borderColor: 'rgba(76, 175, 80, 0.4)',
                            borderWidth: 1,
                            titleColor: '#4CAF50',
                            bodyColor: '#fff',
                            callbacks: { label: ctx => ' Rs.' + Math.round(ctx.raw).toLocaleString('en-IN') }
                        }
                    },
                    scales: {
                        y: {
                            grid: {
                                color: ctx => ctx.tick.value === 0 ? 'rgba(244, 67, 54, 0.5)' : 'rgba(255,255,255,0.05)',
                                lineWidth: ctx => ctx.tick.value === 0 ? 2 : 0.5
                            },
                            ticks: { color: '#fff' }
                        },
                        x: {
                            grid: { color: 'rgba(255,255,255,0.05)' },
                            ticks: { color: '#fff' }
                        }
                    }
                }
            });

            // ─── DRAWDOWN TRAJECTORY CHART ─────────────────────────────
            const ddCtx = document.getElementById('ddChart');
            if (ddCtx) {
                if (ddChart) ddChart.destroy();
                
                let peakVal = 0;
                let ddData = [];
                for (let val of pnlData) {
                    if (val > peakVal) peakVal = val;
                    ddData.push(val - peakVal);
                }
                if (ddData.length === 0) ddData = [0];

                const ctxDd = ddCtx.getContext('2d');
                const ddGrad = ctxDd.createLinearGradient(0,0,0,190);
                ddGrad.addColorStop(0, 'rgba(244, 67, 54, 0.01)');
                ddGrad.addColorStop(1, 'rgba(244, 67, 54, 0.35)');

                ddChart = new Chart(ddCtx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Drawdown (₹)',
                            data: ddData,
                            borderColor: '#f44336',
                            backgroundColor: ddGrad,
                            borderWidth: 2.5,
                            fill: true,
                            tension: 0.4,
                            pointRadius: ddData.map((_,i)=>i===ddData.length-1?6:3),
                            pointBackgroundColor: '#f44336',
                            pointBorderColor: 'rgba(244, 67, 54, 0.5)',
                            pointBorderWidth: 2
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                backgroundColor: 'rgba(10,13,20,0.95)',
                                borderColor: 'rgba(244, 67, 54, 0.4)',
                                borderWidth: 1,
                                titleColor: '#f44336',
                                bodyColor: '#fff',
                                callbacks: { label: ctx => ' Rs.' + Math.round(ctx.raw).toLocaleString('en-IN') }
                            }
                        },
                        scales: {
                            y: {
                                grid: {
                                    color: ctx => ctx.tick.value === 0 ? 'rgba(244, 67, 54, 0.55)' : 'rgba(255,255,255,0.05)',
                                    lineWidth: ctx => ctx.tick.value === 0 ? 2 : 0.5
                                },
                                ticks: { color: '#fff' }
                            },
                            x: {
                                grid: { color: 'rgba(255,255,255,0.05)' },
                                ticks: { color: '#fff' }
                            }
                        }
                    }
                });
            }

            // Statistics Chart
            const statsCtx = document.getElementById('stats-chart');
            if (!statsCtx) return;

            const pnls = trades.map(t => parseFloat(t.PnL_Rs || 0));
            const wins = pnls.filter(p => p > 0).length;
            const losses = pnls.filter(p => p <= 0).length;

            if (statsChart) statsChart.destroy();
            statsChart = new Chart(statsCtx, {
                type: 'doughnut',
                data: {
                    labels: [`Wins (${wins})`, `Losses (${losses})`],
                    datasets: [{
                        data: [wins, losses],
                        backgroundColor: ['#4CAF50', '#f44336'],
                        borderColor: '#fff',
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: { labels: { color: '#fff' } }
                    }
                }
            });
        }

        // Update every 5 seconds
        updateDashboard();
        setInterval(updateDashboard, 5000);
    </script>
</body>
</html>
"""

# ─── ROUTES ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/data")
def api_data():
    trades = _load_trades()
    stats = _calculate_stats(trades)

    return jsonify({
        "nifty_spot": _state["nifty_spot"],
        "vix": _state["vix"],
        "capital": _state["capital"],
        "day_pnl": _state["day_pnl"],
        "cum_pnl": _state["cum_pnl"],
        "day_trades": _state["day_trades"],
        "closed_trades": trades,
        "stats": stats,
        "last_update": _state["last_update"],
        "market_status": _state["market_status"],
    })

if __name__ == "__main__":
    print("[DASHBOARD] Starting on http://localhost:8000")
    app.run(host="127.0.0.1", port=8000, debug=False, use_reloader=False)
