# DEPLOYMENT GUIDE - Access Dashboard From Anywhere

## Option 1: NGROK (Quick & Easy - Testing Only)
Fastest way to test - expose your local dashboard online instantly

```bash
# Install ngrok
pip install pyngrok

# Add to demo_trade.py after _start_dashboard():
from pyngrok import ngrok
ngrok.set_auth_token("YOUR_NGROK_TOKEN")  # Get free token from ngrok.com
public_url = ngrok.connect(5000)
log.info(f"[NGROK] Public URL: {public_url}")
```

**Pros:** Instant setup, no server needed
**Cons:** Free tier has limits, URL changes on restart, not for production

---

## Option 2: RENDER.COM (Recommended - Production Ready)
Modern, easy deployment with free tier

### Step 1: Prepare Your Project
```bash
# Create requirements.txt
pip freeze > requirements.txt

# Add to requirements.txt (if missing):
Flask==2.3.0
pandas==2.0.0
numpy==1.24.0
pytz==2024.1
```

### Step 2: Create Render Account
1. Go to https://render.com
2. Sign up with GitHub account (easier)
3. Create new "Web Service"

### Step 3: Deploy
```
Repository: Your GitHub repo with demo_trade.py
Build Command: pip install -r requirements.txt
Start Command: python -c "from demo_trade import _dashboard_app; _dashboard_app.run(host='0.0.0.0', port=5000)"
```

**Problem:** Trading data needs to sync from your local machine

---

## Option 3: BEST SOLUTION - Hybrid Approach
Keep trading local, sync dashboard data to cloud

### Architecture:
```
Your Local PC
├── demo_trade.py (runs 24/7, generates trades)
└── trades.csv + capital.json (updated constantly)
    ↓ (sync every 5 min)
Cloud Server (Render/Railway)
└── Flask dashboard (reads synced data)
    ↓
Website accessible from anywhere
```

### Step 1: Setup Cloud Sync
Create `cloud_sync.py`:

```python
import json
import os
import requests
from datetime import datetime

CLOUD_API_URL = "https://your-dashboard.onrender.com/api/sync"
LOG_DIR = "logs"

def sync_to_cloud():
    """Sync trade data to cloud every 5 minutes."""
    try:
        data = {}
        
        # Read capital.json
        if os.path.exists(f"{LOG_DIR}/capital.json"):
            with open(f"{LOG_DIR}/capital.json") as f:
                data['capital'] = json.load(f)
        
        # Read trades.csv
        if os.path.exists(f"{LOG_DIR}/trades.csv"):
            with open(f"{LOG_DIR}/trades.csv") as f:
                data['trades'] = f.read()
        
        # Read log
        if os.path.exists(f"{LOG_DIR}/nifty_trader.log"):
            with open(f"{LOG_DIR}/nifty_trader.log") as f:
                data['log'] = f.read()
        
        # Send to cloud
        response = requests.post(
            CLOUD_API_URL,
            json=data,
            headers={"Authorization": f"Bearer YOUR_SECRET_KEY"}
        )
        
        if response.status_code == 200:
            print(f"[SYNC] Data synced at {datetime.now().strftime('%H:%M:%S')}")
        else:
            print(f"[SYNC] Error: {response.status_code}")
    except Exception as e:
        print(f"[SYNC] Failed: {e}")
```

### Step 2: Add Sync to demo_trade.py
Add this to the main loop:

```python
# After _start_dashboard() in run() function:
import time as time_module

sync_interval = 300  # 5 minutes
last_sync = time_module.time()

# In main while loop:
if time_module.time() - last_sync > sync_interval:
    from cloud_sync import sync_to_cloud
    sync_to_cloud()
    last_sync = time_module.time()
```

---

## Option 4: RAILWAY.APP (Simplest Alternative)
Similar to Render but even easier

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Deploy
railway init
railway up
```

---

## Option 5: PYTHON ANYWHERE (Python-Specific)
https://www.pythonanywhere.com

Perfect for Python apps:
1. Sign up free
2. Upload your files
3. Create WSGI file
4. Enable web app

---

## COMPLETE SETUP (OPTION 2 - RENDER RECOMMENDED)

### Local Machine Setup:
1. Add to `demo_trade.py`:

```python
import requests

def sync_trades_to_cloud():
    """Upload trades to cloud."""
    cloud_key = "your-secret-key-here"
    try:
        files = {}
        if os.path.exists(f"{LOG_DIR}/trades.csv"):
            with open(f"{LOG_DIR}/trades.csv", 'rb') as f:
                files['trades'] = f.read()
        if os.path.exists(f"{LOG_DIR}/capital.json"):
            with open(f"{LOG_DIR}/capital.json", 'rb') as f:
                files['capital'] = f.read()
        
        requests.post(
            "https://your-dashboard.onrender.com/api/upload",
            files=files,
            headers={"X-API-Key": cloud_key}
        )
    except:
        pass  # Fail silently if cloud is down
```

2. Call `sync_trades_to_cloud()` every 5 minutes in the main loop

---

## ACCESS FROM ANYWHERE

Once deployed:
- **Desktop:** https://your-dashboard.onrender.com
- **Mobile:** Same URL (responsive design)
- **Tablet:** Same URL
- **Any device with browser:** ✅ Works

---

## MY RECOMMENDATION FOR YOU

**Use Render.com** because:
1. ✅ Free tier available
2. ✅ Easy GitHub integration
3. ✅ Automatic HTTPS
4. ✅ 24/7 uptime
5. ✅ Simple deployment
6. ✅ Great for Flask apps

**Quick Deploy in 10 minutes:**
1. Create GitHub repo with your files
2. Connect GitHub to Render
3. Add `requirements.txt`
4. Deploy
5. Get public URL

Want me to set this up for you? I can create the full deployment files!
