"""
price_monitor.py — Live Option Chain via Groww API (Pure JSON, No Selenium)
Run: python price_monitor.py
Open: http://localhost:8001
"""
import logging, threading, time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, request, render_template_string

IST = ZoneInfo("Asia/Kolkata")
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("monitor")

_lock = threading.Lock()
_cache: dict = {}   # index → payload

GAPS = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}

# Maps standard index name to (Spot API path, Option Chain API path)
INDEX_MAP = {
    "NIFTY":     ("exchange/NSE/segment/CASH/NIFTY/latest",  "nifty"),
    "BANKNIFTY": ("exchange/NSE/segment/CASH/BANKNIFTY/latest", "nifty-bank"),
    "SENSEX":    ("exchange/BSE/segment/CASH/SENSEX/latest", "sp-bse-sensex"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

def _fetch_chain(index: str) -> dict:
    if index not in INDEX_MAP:
        return {"error": f"Unsupported index: {index}", "records": [], "spot": 0}

    spot_path, chain_path = INDEX_MAP[index]

    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        
        # 1. Fetch Spot Price
        spot_url = f"https://groww.in/v1/api/stocks_data/v1/tr_live_indices/{spot_path}"
        r_spot = s.get(spot_url, timeout=10)
        r_spot.raise_for_status()
        spot = float(r_spot.json().get("value", 0))

        # 2. Fetch Option Chain
        chain_url = f"https://groww.in/v1/api/option_chain_service/v1/option_chain/{chain_path}"
        r_chain = s.get(chain_url, timeout=15)
        r_chain.raise_for_status()
        cdata = r_chain.json().get("optionChain", {})
        
        expiries = cdata.get("expiryDetailsDto", {}).get("expiryDates", [])
        sel_exp = expiries[0] if expiries else ""
        rows = cdata.get("optionChains", [])

        records = []
        for row in rows:
            strike = row.get("strikePrice", 0) / 100.0  # Groww returns strike * 100
            ce = row.get("callOption", {})
            pe = row.get("putOption", {})
            records.append({
                "strike":  strike,
                "ce_ltp":  float(ce.get("ltp", 0)),
                "ce_iv":   0.0,  # Groww doesn't provide IV directly
                "ce_oi":   int(ce.get("openInterest", 0)),
                "ce_chng": float(ce.get("dayChange", 0)),
                "pe_ltp":  float(pe.get("ltp", 0)),
                "pe_iv":   0.0,
                "pe_oi":   int(pe.get("openInterest", 0)),
                "pe_chng": float(pe.get("dayChange", 0)),
            })

        records.sort(key=lambda x: x["strike"])
        log.info(f"{index}: spot={spot:.2f} | expiry={sel_exp} | {len(records)} strikes")

        return {
            "spot": spot,
            "expiries": expiries,
            "selected_expiry": sel_exp,
            "records": records,
            "fetched_at": datetime.now(IST).strftime("%H:%M:%S"),
            "source": "Groww API",
            "error": None,
        }

    except Exception as e:
        log.error(f"{index} fetch error: {e}")
        return {"error": str(e), "records": [], "spot": 0}


# ── API routes ────────────────────────────────────────────────────────────────
@app.route("/api/fetch")
def api_fetch():
    index = request.args.get("index", "NIFTY").upper()
    with _lock:
        result = _fetch_chain(index)
        _cache[index] = result
    return jsonify(result)

@app.route("/api/ltp")
def api_ltp():
    index  = request.args.get("index", "NIFTY").upper()
    strike = float(request.args.get("strike", 0))
    opt    = request.args.get("opt", "CE").upper()
    gap    = GAPS.get(index, 50)
    for r in _cache.get(index, {}).get("records", []):
        if abs(r["strike"] - strike) < gap * 0.6:
            ltp = r["ce_ltp"] if opt == "CE" else r["pe_ltp"]
            return jsonify({"strike": strike, "opt": opt, "ltp": ltp, "found": ltp > 0})
    return jsonify({"strike": strike, "opt": opt, "ltp": 0, "found": False})

@app.route("/")
def index():
    return render_template_string(HTML)


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live Price Monitor (Groww API)</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#060b18;color:#e2e8f0;min-height:100vh}
.topbar{background:linear-gradient(135deg,#0f1629,#1a2340);border-bottom:1px solid #1e2d50;padding:14px 24px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.logo{font-size:1.3em;font-weight:700;background:linear-gradient(90deg,#10b981,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.badge{background:#10b981;color:#fff;font-size:.72em;font-weight:700;padding:3px 10px;border-radius:5px}
.tabs{display:flex;gap:6px;margin-left:auto}
.tab{padding:7px 16px;border-radius:7px;border:1px solid #1e2d50;background:transparent;color:#64748b;cursor:pointer;font-size:.85em;font-weight:600;transition:all .2s}
.tab.active,.tab:hover{background:#10b981;border-color:#10b981;color:#fff}
.sdot{width:9px;height:9px;border-radius:50%;background:#ef4444;display:inline-block;margin-right:5px;animation:blink 1.4s infinite}
.sdot.ok{background:#10b981;animation:none}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
.main{padding:18px 22px;max-width:1600px;margin:0 auto}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin-bottom:14px}
.mc{background:#0d1526;border:1px solid #1e2d50;border-radius:10px;padding:14px}
.mc label{font-size:.72em;color:#475569;text-transform:uppercase;letter-spacing:.5px}
.mc .v{font-size:1.6em;font-weight:700;margin-top:3px;color:#10b981}
.mc .s{font-size:.75em;color:#334155;margin-top:2px}
.controls{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.btn{padding:9px 18px;border-radius:7px;border:none;font-size:.88em;font-weight:600;cursor:pointer;transition:all .2s}
.bp{background:#10b981;color:#fff}.bp:hover{background:#059669}
.bs{padding:6px 12px;font-size:.8em}
select,input{background:#0d1526;border:1px solid #1e2d50;color:#e2e8f0;padding:7px 12px;border-radius:7px;font-size:.88em;outline:none}
select:focus,input:focus{border-color:#10b981}
.lbox{background:#0d1526;border:1px solid #1e2d50;border-radius:10px;padding:14px;margin-bottom:14px}
.lbox h3{font-size:.88em;color:#64748b;margin-bottom:10px}
.lres{font-size:1.3em;font-weight:700;padding:10px 14px;background:#071020;border-radius:7px;min-height:46px;display:flex;align-items:center;gap:10px;color:#10b981}
.lres.err{color:#ef4444;font-size:.95em}
.tw{background:#0d1526;border:1px solid #1e2d50;border-radius:10px;overflow:hidden;margin-bottom:14px}
.th3{padding:12px 18px;font-size:.9em;color:#64748b;border-bottom:1px solid #1e2d50;display:flex;justify-content:space-between;align-items:center}
table{width:100%;border-collapse:collapse;font-size:.83em}
thead th{background:#060e1e;padding:9px 10px;text-align:center;font-size:.75em;font-weight:600;color:#475569;text-transform:uppercase}
td{padding:8px 10px;text-align:center;border-bottom:1px solid #0f1a30}
tr:last-child td{border-bottom:none}
tr:hover td{background:#0f1a2e}
tr.atm td{background:#0a1c17!important}
.stk{color:#fbbf24;font-weight:700}
.ce{color:#10b981}.pe{color:#f87171}.oi{color:#475569}
.cp{color:#10b981}.cn{color:#ef4444}
.abadge{background:#064e3b;color:#34d399;font-size:.7em;font-weight:700;padding:2px 7px;border-radius:4px}
.fr{display:flex;align-items:center;padding:10px 18px;border-top:1px solid #1e2d50;font-size:.78em;color:#334155}
.spin{display:none;width:14px;height:14px;border:2px solid #1e2d50;border-top-color:#10b981;border-radius:50%;animation:sp .6s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.ebar{color:#fca5a5;background:#1f0a0a;border:1px solid #7f1d1d;padding:10px 16px;border-radius:8px;margin-bottom:12px;display:none;font-size:.88em}
.note{font-size:.78em;color:#475569;margin-left:8px}
</style>
</head>
<body>
<div class="topbar">
  <span class="logo">📡 Price Monitor</span>
  <span class="badge">Groww API</span>
  <div style="display:flex;align-items:center;gap:5px;font-size:.82em">
    <span class="sdot" id="dot"></span><span id="stxt">Not fetched</span>
  </div>
  <div class="tabs">
    <button class="tab active" onclick="sw('NIFTY',this)">NIFTY</button>
    <button class="tab" onclick="sw('BANKNIFTY',this)">BANKNIFTY</button>
    <button class="tab" onclick="sw('SENSEX',this)">SENSEX</button>
  </div>
</div>

<div class="main">
  <div class="metrics">
    <div class="mc"><label>Index</label><div class="v" id="mi">NIFTY</div></div>
    <div class="mc"><label>Spot Price</label><div class="v" id="ms">—</div><div class="s">Groww Live</div></div>
    <div class="mc"><label>ATM Strike</label><div class="v" id="ma">—</div></div>
    <div class="mc"><label>Expiry</label><div class="v" style="font-size:1em;margin-top:8px" id="me">—</div></div>
    <div class="mc"><label>Strikes Loaded</label><div class="v" id="mc2">—</div></div>
    <div class="mc"><label>Last Fetch</label><div class="v" style="font-size:1.1em;margin-top:6px" id="mt">—</div></div>
  </div>

  <div id="ebar" class="ebar"></div>

  <div class="controls">
    <button class="btn bp" id="fetchbtn" onclick="doFetch()">⚡ Fetch Fast API</button>
    <select id="expsel" style="display:none"></select>
    <label style="font-size:.82em;color:#475569">Auto-refresh:</label>
    <select id="arint"><option value="0">Off</option><option value="10">10s</option><option value="30">30s</option><option value="60" selected>60s</option></select>
    <div class="spin" id="spin"></div>
    <label style="font-size:.82em;color:#475569;margin-left:8px"><input type="checkbox" id="atmchk" onchange="render()"> ±10 ATM only</label>
    <span class="note">Pure API (No Selenium/Chrome needed)</span>
  </div>

  <div class="lbox">
    <h3>🔍 Test a Specific Strike</h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <input type="number" id="ls" placeholder="Strike e.g. 24000" style="width:190px">
      <select id="lo"><option>CE</option><option>PE</option></select>
      <button class="btn bp bs" onclick="lookup()">Get LTP</button>
    </div>
    <div class="lres" id="lr">Enter a strike and click Get LTP</div>
  </div>

  <div class="tw">
    <div class="th3">
      <span id="ctitle">NIFTY Option Chain</span>
      <span id="rcnt" style="font-size:.8em;color:#334155">—</span>
    </div>
    <div id="tarea" style="padding:36px;text-align:center;color:#334155">Click "Fetch Fast API" to load</div>
    <div class="fr">Source: Groww Open API &nbsp;|&nbsp; Expiry: <span id="fexp">—</span><span style="margin-left:auto"><span id="ftime">—</span></span></div>
  </div>
</div>

<script>
let idx='NIFTY', data=null, timer=null;
const GAPS={NIFTY:50,BANKNIFTY:100,SENSEX:100};

function sw(i,el){
  idx=i; data=null;
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('mi').textContent=i;
  document.getElementById('ctitle').textContent=i+' Option Chain';
  document.getElementById('tarea').innerHTML='<div style="padding:36px;text-align:center;color:#334155">Click Fetch Fast API to load '+i+'</div>';
  ['ms','ma','me','mc2','mt'].forEach(id=>document.getElementById(id).textContent='—');
  document.getElementById('expsel').style.display='none';
}

async function doFetch(){
  const spin=document.getElementById('spin'),dot=document.getElementById('dot'),st=document.getElementById('stxt');
  const btn=document.getElementById('fetchbtn');
  spin.style.display='inline-block'; dot.className='sdot'; st.textContent='Fetching JSON...'; btn.disabled=true;
  document.getElementById('ebar').style.display='none';
  try{
    const res=await fetch('/api/fetch?index='+idx);
    data=await res.json();
    if(data.error){ showErr(data.error); dot.className='sdot'; st.textContent='Error'; }
    else{
      dot.className='sdot ok'; st.textContent='Live — '+data.fetched_at;
      updateMeta(); buildExpSel(); render();
    }
  }catch(e){ showErr('Network error: '+e.message); dot.className='sdot'; st.textContent='Error'; }
  spin.style.display='none'; btn.disabled=false;
}

function showErr(m){ const e=document.getElementById('ebar'); e.textContent='⚠ '+m; e.style.display='block'; }

function updateMeta(){
  const spot=data.spot||0, gap=GAPS[idx]||50, atm=Math.round(spot/gap)*gap;
  document.getElementById('ms').textContent=spot>0?'₹'+spot.toLocaleString('en-IN',{maximumFractionDigits:2}):'—';
  document.getElementById('ma').textContent=atm>0?atm.toLocaleString('en-IN'):'—';
  document.getElementById('me').textContent=data.selected_expiry||'—';
  document.getElementById('mc2').textContent=(data.records||[]).length;
  document.getElementById('mt').textContent=data.fetched_at||'—';
  document.getElementById('fexp').textContent=data.selected_expiry||'—';
  document.getElementById('ftime').textContent=data.fetched_at||'—';
  document.getElementById('ctitle').textContent=idx+' Option Chain';
  if(atm>0) document.getElementById('ls').value=atm;
}

function buildExpSel(){
  const sel=document.getElementById('expsel'); sel.innerHTML='';
  (data.expiries||[]).forEach(e=>{ const o=document.createElement('option'); o.value=o.textContent=e; sel.appendChild(o); });
  if(data.expiries&&data.expiries.length>1){ sel.value=data.selected_expiry; sel.style.display='inline-block'; }
}

function render(){
  if(!data||!data.records) return;
  const spot=data.spot||0, gap=GAPS[idx]||50, atm=Math.round(spot/gap)*gap;
  const atmOnly=document.getElementById('atmchk').checked;
  let recs=data.records;
  if(atmOnly&&atm>0) recs=recs.filter(r=>Math.abs(r.strike-atm)<=gap*10);
  if(!recs.length){
    document.getElementById('tarea').innerHTML='<div style="padding:36px;text-align:center;color:#ef4444">No data</div>';
    return;
  }
  let h=`<table><thead><tr>
    <th class="oi">CE OI</th>
    <th class="ce">CE LTP</th><th>CE Chng</th>
    <th>STRIKE</th>
    <th>PE Chng</th><th class="pe">PE LTP</th>
    <th class="oi">PE OI</th>
  </tr></thead><tbody>`;
  recs.forEach(r=>{
    const isAtm=Math.abs(r.strike-atm)<gap*0.5;
    const badge=isAtm?'<span class="abadge">ATM</span>':'';
    const cc=r.ce_chng>0?'cp':r.ce_chng<0?'cn':'', pc=r.pe_chng>0?'cp':r.pe_chng<0?'cn':'';
    h+=`<tr class="${isAtm?'atm':''}">
      <td class="oi">${r.ce_oi>0?(r.ce_oi/100000).toFixed(1)+'L':'—'}</td>
      <td class="ce" style="font-weight:600">${r.ce_ltp>0?'₹'+r.ce_ltp.toFixed(2):'—'}</td>
      <td class="${cc}">${r.ce_chng!==0?(r.ce_chng>0?'+':'')+r.ce_chng.toFixed(2):'—'}</td>
      <td class="stk">${r.strike.toLocaleString('en-IN')} ${badge}</td>
      <td class="${pc}">${r.pe_chng!==0?(r.pe_chng>0?'+':'')+r.pe_chng.toFixed(2):'—'}</td>
      <td class="pe" style="font-weight:600">${r.pe_ltp>0?'₹'+r.pe_ltp.toFixed(2):'—'}</td>
      <td class="oi">${r.pe_oi>0?(r.pe_oi/100000).toFixed(1)+'L':'—'}</td>
    </tr>`;
  });
  h+='</tbody></table>';
  document.getElementById('tarea').innerHTML=h;
  document.getElementById('rcnt').textContent=recs.length+' strikes'+(atmOnly?' (±10 ATM)':'');
}

async function lookup(){
  const strike=document.getElementById('ls').value, opt=document.getElementById('lo').value;
  const el=document.getElementById('lr');
  if(!strike){ el.textContent='⚠ Enter a strike'; el.className='lres err'; return; }
  if(!data||!data.records||!data.records.length){
    el.textContent='⏳ Auto-fetching chain...'; el.className='lres';
    await doFetch();
    if(!data||!data.records||!data.records.length){ el.textContent='❌ Fetch failed'; el.className='lres err'; return; }
  }
  const gap=GAPS[idx]||50;
  for(const r of data.records){
    if(Math.abs(r.strike-parseFloat(strike))<gap*0.6){
      const ltp=opt==='CE'?r.ce_ltp:r.pe_ltp, oi=opt==='CE'?r.ce_oi:r.pe_oi;
      el.className='lres';
      if(ltp>0){
        el.innerHTML=`<span style="color:#fbbf24;font-size:.8em">${idx} ${parseInt(strike)} ${opt}</span>
          <span style="color:#10b981;font-size:1.4em">₹${ltp.toFixed(2)}</span>
          <span style="font-size:.75em;color:#475569">OI: ${oi>0?(oi/100000).toFixed(1)+'L':'—'}</span>
          <span style="font-size:.7em;color:#334155;margin-left:auto">✓ Live API</span>`;
      } else {
        el.innerHTML=`<span style="color:#f59e0b">LTP = 0 (market closed or no quotes)</span>`;
      }
      return;
    }
  }
  el.innerHTML=`<span style="color:#ef4444">Strike ${strike} not found (${data.records.length} strikes loaded)</span>`;
  el.className='lres err';
}

document.getElementById('arint').addEventListener('change',function(){
  if(timer){clearInterval(timer);timer=null;}
  const s=parseInt(this.value);
  if(s>0) timer=setInterval(doFetch,s*1000);
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("=" * 54)
    print("  Fast Groww API Monitor — http://localhost:8001")
    print("  (Zero Selenium, Zero Chrome)")
    print("=" * 54)
    app.run(host="127.0.0.1", port=8001, debug=False, use_reloader=False)
