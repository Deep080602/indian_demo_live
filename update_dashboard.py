import os

# Read the file
with open('d:/Dhan/dhan_algo/demo_trade.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find DASHBOARD_HTML start
start_idx = content.find('DASHBOARD_HTML = r"""')
search_start = start_idx + 21
end_pattern = content.find('</html>', search_start)
end_idx = content.find('"""', end_pattern)

print(f"Replacing dashboard HTML from {start_idx} to {end_idx+3}")

new_html = '''DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NIFTY Pro Trader 3D</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Poppins:wght@300;400;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%}
:root{--bg:#0a0e27;--card:rgba(30,40,60,0.8);--border:rgba(100,150,255,0.2);--green:#00ff88;--red:#ff4466;--blue:#00ccff;--purple:#bb66ff;--text:#e8eaf6;--muted:#a0aec0}
body{background:linear-gradient(135deg, #0a0e27 0%, #1a1f3a 50%, #0f1629 100%);color:var(--text);font-family:'Poppins',sans-serif;overflow:hidden}
.main{display:grid;grid-template-columns:300px 1fr;height:100vh;gap:0}
.sidebar{background:rgba(10,14,39,0.9);backdrop-filter:blur(10px);border-right:1px solid var(--border);padding:30px 20px;overflow-y:auto}
.sidebar::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg, transparent, #00ffff, transparent)}
.logo{font-size:32px;margin-bottom:30px;text-align:center;background:linear-gradient(135deg,#00ff88,#00ccff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.metric-card{background:linear-gradient(135deg, rgba(0,255,136,0.1), rgba(0,204,255,0.1));border:1px solid rgba(0,255,136,0.3);border-radius:12px;padding:16px;margin-bottom:15px;transition:all 0.3s}
.metric-card:hover{border-color:#00ff88;box-shadow:0 0 20px rgba(0,255,136,0.3);transform:translateX(5px)}
.metric-card .label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;margin-bottom:8px}
.metric-card .value{font-size:22px;font-weight:700;font-family:'JetBrains Mono',monospace;color:#00ff88;margin-bottom:4px}
.metric-card .sub{font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace}
.content{display:grid;grid-template-rows:60px 1fr;padding:0;background:linear-gradient(135deg, rgba(26,31,58,0.5), rgba(15,22,41,0.5))}
.content::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg, transparent, #00ff88, transparent);z-index:10}
.header{background:rgba(10,14,39,0.95);backdrop-filter:blur(10px);border-bottom:1px solid var(--border);padding:15px 30px;display:flex;align-items:center;justify-content:space-between;z-index:100}
.header-title{font-size:20px;font-weight:700;background:linear-gradient(90deg, #00ff88, #00ccff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.live-indicator{display:flex;align-items:center;gap:8px;padding:8px 16px;background:rgba(0,255,136,0.1);border:1px solid #00ff88;border-radius:20px;font-size:12px;color:#00ff88}
.dot{width:8px;height:8px;border-radius:50%;background:#00ff88;box-shadow:0 0 10px #00ff88;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
.viewport{display:grid;grid-template-columns:1fr 1fr;gap:15px;padding:20px;height:calc(100vh - 120px);overflow-y:auto}
#canvas3d{width:100%;height:100%;border-radius:12px;background:radial-gradient(circle, rgba(0,204,255,0.05) 0%, rgba(0,0,0,0.3) 100%)}
.chart-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;height:100%;display:flex;flex-direction:column}
.chart-card .title{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;margin-bottom:15px;font-weight:600}
.chart-card .chart{position:relative;flex:1}
.metrics-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;grid-column:1/-1}
.stat{background:linear-gradient(135deg, rgba(0,255,136,0.1), rgba(0,204,255,0.1));border:1px solid rgba(0,255,136,0.3);border-radius:12px;padding:15px;text-align:center}
.stat .val{font-size:28px;font-weight:700;color:#00ff88;margin:10px 0;font-family:'JetBrains Mono',monospace}
.stat .lbl{font-size:10px;color:var(--muted);text-transform:uppercase}
.trades-table{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:15px;grid-column:1/-1;max-height:300px;overflow-y:auto}
.trades-table table{width:100%;font-size:10px;border-collapse:collapse}
.trades-table th{color:var(--muted);font-weight:600;text-align:left;padding:8px;border-bottom:1px solid var(--border)}
.trades-table td{padding:8px;border-bottom:1px solid rgba(100,150,255,0.1);font-family:'JetBrains Mono',monospace}
.trades-table tr:hover{background:rgba(0,255,136,0.1)}
.pos{color:#00ff88}.neg{color:#ff4466}
.live-log{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:15px;grid-column:1/-1;max-height:300px;overflow-y:auto;font-family:'JetBrains Mono',monospace;font-size:10px;line-height:1.6}
.log-line{padding:4px 0;margin:2px 0}
.log-line.win{color:#00ff88}.log-line.loss{color:#ff4466}.log-line.info{color:#00ccff}
.refresh{position:fixed;bottom:20px;right:20px;background:var(--card);border:1px solid var(--border);padding:10px 16px;border-radius:12px;font-size:11px;color:var(--muted);backdrop-filter:blur(10px)}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-thumb{background:rgba(0,255,136,0.3);border-radius:2px}
::-webkit-scrollbar-thumb:hover{background:rgba(0,255,136,0.6)}
</style>
</head>
<body>

<div class="main">
  <div class="sidebar">
    <div class="logo">📈</div>
    <div class="metric-card">
      <div class="label">Capital</div>
      <div class="value pos" id="cap">₹0</div>
      <div class="sub" id="capBase">Base: ₹0</div>
    </div>
    <div class="metric-card">
      <div class="label">Total P&L</div>
      <div class="value" id="pnl">₹0</div>
      <div class="sub" id="pnlPct">+0%</div>
    </div>
    <div class="metric-card">
      <div class="label">Win Rate</div>
      <div class="value pos" id="wr">0%</div>
      <div class="sub" id="wl">0 W / 0 L</div>
    </div>
    <div class="metric-card">
      <div class="label">Best Trade</div>
      <div class="value pos" id="best">₹0</div>
      <div class="sub" id="worst">Worst: ₹0</div>
    </div>
    <div class="metric-card">
      <div class="label">Avg Win/Loss</div>
      <div class="value pos" id="avgw">₹0</div>
      <div class="sub">R:R <span id="rr">0</span></div>
    </div>
    <div class="metric-card">
      <div class="label">Total Trades</div>
      <div class="value" id="trades">0</div>
      <div class="sub">Completed</div>
    </div>
  </div>

  <div class="content">
    <div class="header">
      <div class="header-title">NIFTY Pro Trader 3D Dashboard</div>
      <div class="live-indicator"><div class="dot"></div> LIVE</div>
    </div>

    <div class="viewport">
      <div class="chart-card" style="grid-row:1/3">
        <div class="title">3D Equity Curve</div>
        <div class="chart">
          <canvas id="canvas3d"></canvas>
        </div>
      </div>

      <div class="chart-card">
        <div class="title">Cumulative P&L</div>
        <div class="chart">
          <canvas id="eq-chart"></canvas>
        </div>
      </div>

      <div class="chart-card">
        <div class="title">Win/Loss Distribution</div>
        <div class="chart">
          <canvas id="wl-chart"></canvas>
        </div>
      </div>

      <div class="metrics-grid">
        <div class="stat">
          <div class="lbl">Avg Win</div>
          <div class="val pos" id="avw2">₹0</div>
        </div>
        <div class="stat">
          <div class="lbl">Avg Loss</div>
          <div class="val neg" id="avl2">₹0</div>
        </div>
        <div class="stat">
          <div class="lbl">Win Count</div>
          <div class="val pos" id="wc">0</div>
        </div>
        <div class="stat">
          <div class="lbl">Loss Count</div>
          <div class="val neg" id="lc">0</div>
        </div>
      </div>

      <div class="trades-table">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Time</th>
              <th>Type</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>P&L</th>
            </tr>
          </thead>
          <tbody id="trades-table">
            <tr><td colspan="6" style="text-align:center;color:var(--muted)">No trades</td></tr>
          </tbody>
        </table>
      </div>

      <div class="live-log" id="term"></div>
    </div>
  </div>
</div>

<div class="refresh" id="refresh">Updating...</div>

<script>
const fmt = n => new Intl.NumberFormat('en-IN').format(Math.round(+n||0));
let scene, camera, renderer, line;

function init3D(){
  const canvas = document.getElementById('canvas3d');
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0e1b);
  camera = new THREE.PerspectiveCamera(75, canvas.clientWidth/canvas.clientHeight, 0.1, 1000);
  camera.position.set(0, 40, 60);
  camera.lookAt(0, 0, 0);
  renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true});
  renderer.setSize(canvas.clientWidth, canvas.clientHeight);
  renderer.shadowMap.enabled = true;

  const light = new THREE.PointLight(0x00ff88, 2);
  light.position.set(50, 50, 50);
  scene.add(light);
  const ambient = new THREE.AmbientLight(0x4fc3f7, 0.5);
  scene.add(ambient);

  const grid = new THREE.GridHelper(100, 10, 0x00ff88, 0x004422);
  scene.add(grid);

  animate3D();
}

function animate3D(){
  requestAnimationFrame(animate3D);
  if(camera) camera.position.x = Math.sin(Date.now()*0.0001)*70;
  renderer.render(scene, camera);
}

function update3D(equity){
  if(line) scene.remove(line);
  const points = [];
  const max = Math.max(...equity, 1);
  equity.forEach((v,i)=>{
    points.push(new THREE.Vector3((i/equity.length)*100-50,(v/max)*50,Math.sin(i*0.05)*15));
  });
  const geom = new THREE.BufferGeometry().setFromPoints(points);
  const mat = new THREE.LineBasicMaterial({color:0x00ff88, linewidth:4});
  line = new THREE.Line(geom, mat);
  scene.add(line);
}

let eqC, wlC;

async function load(){
  try{
    const data = await fetch('/api/data').then(r=>r.json());
    const s = data.stats;
    const trades = data.trades;
    const equity = data.equity;
    const logs = data.log;

    document.getElementById('cap').textContent = '₹'+fmt(s.capital);
    document.getElementById('capBase').textContent = 'Base: ₹'+fmt(s.base_capital);
    const pnlEl = document.getElementById('pnl');
    pnlEl.textContent = (s.total_pnl>=0?'+':'')+'₹'+fmt(s.total_pnl);
    pnlEl.className = 'value '+(s.total_pnl>=0?'pos':'neg');
    const pct = s.base_capital>0?(s.total_pnl/s.base_capital*100):0;
    document.getElementById('pnlPct').textContent = (pct>=0?'+':'')+pct.toFixed(2)+'%';
    document.getElementById('wr').textContent = (s.win_rate||0).toFixed(1)+'%';
    document.getElementById('wl').textContent = s.wins+' W / '+s.losses+' L';
    document.getElementById('avw2').textContent = (s.avg_win>=0?'+':'')+'₹'+fmt(s.avg_win);
    document.getElementById('avl2').textContent = (s.avg_loss>=0?'+':'')+' ₹'+fmt(s.avg_loss);
    document.getElementById('best').textContent = (s.best>=0?'+':'')+'₹'+fmt(s.best);
    document.getElementById('worst').textContent = 'Worst: '+(s.worst>=0?'+':'')+' ₹'+fmt(s.worst);
    document.getElementById('avgw').textContent = (s.avg_win>=0?'+':'')+'₹'+fmt(s.avg_win);
    document.getElementById('rr').textContent = (s.rr||0).toFixed(2);
    document.getElementById('trades').textContent = s.total_trades;
    document.getElementById('wc').textContent = s.wins;
    document.getElementById('lc').textContent = s.losses;

    if(equity && equity.length>0) update3D(equity);

    const eqCtx = document.getElementById('eq-chart').getContext('2d');
    const wlCtx = document.getElementById('wl-chart').getContext('2d');
    const labels = (equity||[]).map((_,i)=>'#'+(i+1));
    const pnls = (trades||[]).map(t=>+t.pnl);

    if(eqC) eqC.destroy();
    eqC = new Chart(eqCtx, {
      type:'line',
      data:{
        labels,
        datasets:[{
          data:equity||[],
          borderColor:'#00ff88',
          backgroundColor:'rgba(0,255,136,0.1)',
          borderWidth:3,
          fill:true,
          tension:0.4,
          pointRadius:4,
          pointBackgroundColor:(equity||[]).map(v=>v>=0?'#00ff88':'#ff4466'),
          pointBorderColor:'#fff'
        }]
      },
      options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{display:false}, y:{grid:{color:'rgba(255,255,255,0.05)'}, ticks:{color:'#a0aec0'}}}}
    });

    if(wlC) wlC.destroy();
    wlC = new Chart(wlCtx, {
      type:'bar',
      data:{
        labels:pnls.map((_,i)=>'#'+(i+1)),
        datasets:[{data:pnls, backgroundColor:pnls.map(v=>v>=0?'rgba(0,255,136,0.7)':'rgba(255,68,102,0.7)'), borderColor:pnls.map(v=>v>=0?'#00ff88':'#ff4466'), borderWidth:2, borderRadius:8}]
      },
      options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{display:false}, y:{grid:{color:'rgba(255,255,255,0.05)'}}}},
    });

    const tb = document.getElementById('trades-table');
    if(!trades||!trades.length) tb.innerHTML = '<tr><td colspan="6" style="text-align:center">No trades</td></tr>';
    else tb.innerHTML = [...trades].reverse().map(t=>`<tr><td>${t.date||''}</td><td>${t.entry_time||''}</td><td>${t.direction||''}</td><td>₹${(+t.entry).toFixed(0)}</td><td>${t.exit?'₹'+(+t.exit).toFixed(0):'—'}</td><td class="${t.pnl>0?'pos':'neg'}">${(t.pnl>=0?'+':'')+'₹'+fmt(t.pnl)}</td></tr>`).join('');

    const term = document.getElementById('term');
    term.innerHTML = (logs||[]).map(l=>{let cls=''; if(/WIN|TARGET|ENTRY/.test(l))cls='win'; else if(/LOSS|STOP/.test(l))cls='loss'; else if(/STR|DATA/.test(l))cls='info'; return `<div class="log-line ${cls}">${l.replace(/</g,'&lt;')}</div>`;}).join('');
    term.scrollTop = term.scrollHeight;

    document.getElementById('refresh').textContent = '✓ '+new Date().toLocaleTimeString();
  }catch(e){console.error(e);}
}

init3D();
load();
setInterval(load, 5000);
window.addEventListener('resize', ()=>{
  if(renderer && camera){
    const w = document.getElementById('canvas3d').clientWidth;
    const h = document.getElementById('canvas3d').clientHeight;
    camera.aspect = w/h;
    camera.updateProjectionMatrix();
    renderer.setSize(w,h);
  }
});
</script>
</body>
</html>"""'''

# Replace
before = content[:start_idx]
after = content[end_idx+3:]
new_content = before + new_html + after

# Write back
with open('d:/Dhan/dhan_algo/demo_trade.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("✅ Dashboard updated with 3D version!")
print(f"New dashboard size: {len(new_html) // 1024} KB")
