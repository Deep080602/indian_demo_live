"""
nse_data.py — Real NSE option prices
Table ID confirmed: optionChainTable-indices (155 rows)
"""
import json, time, re, logging, os
from datetime import datetime, date, timedelta
from typing import Optional, Dict
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_cache: Dict[str, dict] = {}
_cache_ts: Dict[str, datetime] = {}
CACHE_TTL = 60
_driver = None

# Dynamically set cross-platform Chromedriver path
_env_chromedriver = os.environ.get("CHROMEDRIVER_PATH")
if _env_chromedriver:
    CHROMEDRIVER = _env_chromedriver
else:
    if os.name == 'nt':
        CHROMEDRIVER = (
            r"C:\Users\DIPMA\.wdm\drivers\chromedriver\win64"
            r"\147.0.7727.117\chromedriver-win32\chromedriver.exe"
        )
    else:
        # Default Linux chromedriver path (typically in PATH on cloud services)
        CHROMEDRIVER = "chromedriver"


def _start_driver():
    global _driver
    if _driver:
        return _driver
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/147.0.7727.102 Safari/537.36"
        )
        _driver = webdriver.Chrome(service=Service(CHROMEDRIVER), options=opts)
        _driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        })
        log.info("[NSE] Chrome started")
        return _driver
    except Exception as e:
        log.error(f"[NSE] Driver: {e}")
        return None


def _load_bse_sensex(driver) -> Optional[dict]:
    """Fetch SENSEX option chain from BSE website."""
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver.get("https://www.bseindia.com")
        time.sleep(2)
        driver.get("https://www.bseindia.com/markets/Derivatives/DerivativesHome.aspx?expandable=3")
        time.sleep(5)

        # Get spot from page
        spot = 0.0
        try:
            src = driver.page_source
            m = re.search(r'SENSEX[^0-9]*([\d,]+\.?\d*)', src)
            if m: spot = float(m.group(1).replace(",",""))
        except: pass

        # Extract table rows via JS
        rows_js = driver.execute_script("""
            var tables = document.querySelectorAll('table');
            var result = [];
            tables.forEach(function(tbl) {
                var rows = tbl.querySelectorAll('tr');
                rows.forEach(function(row) {
                    var cells = row.querySelectorAll('td');
                    if (cells.length >= 10) {
                        result.push(Array.from(cells).map(c => c.textContent.trim()));
                    }
                });
            });
            return JSON.stringify(result);
        """)

        rows = json.loads(rows_js) if rows_js else []
        log.info(f"[BSE] SENSEX: spot={spot:.0f}, rows={len(rows)}")

        records = []
        for row in rows:
            try:
                # Find strike (large round number)
                for i, cell in enumerate(row):
                    val = cell.replace(",","").strip()
                    if val.isdigit() and 60000 <= int(float(val)) <= 100000:
                        strike = float(val)
                        # CE LTP before strike, PE LTP after
                        ce_ltp = 0.0; pe_ltp = 0.0
                        for j in range(max(0,i-6), i):
                            try:
                                v = float(row[j].replace(",",""))
                                if 1 < v < 5000: ce_ltp = v
                            except: pass
                        for j in range(i+1, min(len(row), i+7)):
                            try:
                                v = float(row[j].replace(",",""))
                                if 1 < v < 5000: pe_ltp = v
                            except: pass
                        if ce_ltp > 0 or pe_ltp > 0:
                            records.append({
                                "strikePrice": strike,
                                "CE": {"lastPrice": ce_ltp},
                                "PE": {"lastPrice": pe_ltp},
                            })
                        break
            except: continue

        log.info(f"[BSE] SENSEX: {len(records)} records")
        if not spot and records:
            mid = records[len(records)//2]
            spot = mid["strikePrice"]
        return {"records": {"underlyingValue": spot, "data": records}}
    except Exception as e:
        log.warning(f"[BSE] SENSEX error: {e}")
        return None

def _load_chain(symbol: str) -> Optional[dict]:
    driver = _start_driver()
    if not driver:
        return None
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait, Select
        from selenium.webdriver.support import expected_conditions as EC

        # Load page
        driver.get("https://www.nseindia.com")
        time.sleep(3)
        driver.get("https://www.nseindia.com/option-chain")

        # Wait for confirmed table ID
        wait = WebDriverWait(driver, 25)
        wait.until(EC.presence_of_element_located(
            (By.ID, "optionChainTable-indices")
        ))
        time.sleep(2)

        # SENSEX — navigate to BSE option chain via Selenium
        if symbol == "SENSEX":
            return _load_bse_sensex(driver)

        # Get spot price from page
        spot = 0.0
        try:
            src = driver.page_source
            # Pattern: "Underlying Index : NIFTY 23,897.95"
            m = re.search(r'Underlying Index\s*[:\-]\s*[A-Z0-9 ]+\s*([\d,]+\.?\d*)', src)
            if m:
                spot = float(m.group(1).replace(",", ""))
            if not spot:
                # Try JS variable
                spot_js = driver.execute_script("""
                    try {
                        var el = document.querySelector('.niftyWrap .num, .underlying-value, [class*=underlying]');
                        if (el) return el.textContent.replace(/[^0-9.]/g,'');
                    } catch(e){}
                    return '0';
                """)
                spot = float(spot_js) if spot_js else 0.0
        except:
            pass

        # Extract all rows from the confirmed table
        rows_js = driver.execute_script("""
            var tbl = document.getElementById('optionChainTable-indices');
            if (!tbl) return '[]';
            var rows = tbl.querySelectorAll('tbody tr');
            var result = [];
            rows.forEach(function(row) {
                var cells = row.querySelectorAll('td');
                var vals = [];
                cells.forEach(function(td) {
                    vals.push(td.textContent.trim());
                });
                if (vals.length > 0) result.push(vals);
            });
            return JSON.stringify(result);
        """)

        rows = json.loads(rows_js) if rows_js else []
        log.info(f"[NSE] {symbol}: spot={spot:.0f}, table rows={len(rows)}")

        # Get spot from first numeric-looking cell if still 0
        if spot == 0 and rows:
            try:
                # Spot is often in the page header — try different approach
                spot_el = driver.find_element(
                    By.XPATH,
                    "//*[contains(text(),'Underlying')]/following-sibling::*[1]"
                )
                spot = float(re.sub(r"[^\d.]", "", spot_el.text))
            except:
                pass

        # Parse rows into option records
        # NSE table columns (21 cols per row):
        # 0:OI 1:CHG_OI 2:VOL 3:IV 4:LTP 5:CHNG 6:BIDQTY 7:BID 8:ASK 9:ASKQTY 10:STRIKE
        # 11:BIDQTY 12:BID 13:ASK 14:ASKQTY 15:CHNG 16:LTP 17:IV 18:VOL 19:CHG_OI 20:OI
        records = []
        for row in rows:
            try:
                n = len(row)
                if n < 11:
                    continue
                mid = n // 2
                strike_str = row[mid].replace(",", "").strip()
                if not strike_str or not strike_str[0].isdigit():
                    continue
                strike = float(strike_str)
                if strike < 100:
                    continue

                ce_ltp = 0.0
                pe_ltp = 0.0

                # NSE table: icon col + OI,CHG_OI,VOL,IV,LTP,CHNG,BIDQTY,BID,ASK,ASKQTY,STRIKE,...
                # CE LTP = col 5, STRIKE = col 11, PE LTP = col 17 (icon col shifts by 1)
                # Also try col 4 (no icon) as fallback
                def _f(s):
                    s=s.replace(",","").replace("-","").strip()
                    return float(s) if s and s not in ("","0") else 0.0
                if n >= 22:
                    ce_ltp = _f(row[5]) or _f(row[4])
                    pe_ltp = _f(row[17]) or _f(row[16])
                    strike = float(row[11].replace(",","")) if n>11 else strike
                elif n >= 21:
                    ce_ltp = _f(row[4])
                    pe_ltp = _f(row[16])
                elif n >= 13:
                    ce_ltp = _f(row[4])
                    pe_ltp = _f(row[n-5])

                if ce_ltp > 0 or pe_ltp > 0:
                    records.append({
                        "strikePrice": strike,
                        "expiryDate":  "",
                        "CE": {"lastPrice": ce_ltp},
                        "PE": {"lastPrice": pe_ltp},
                    })
            except:
                continue

        log.info(f"[NSE] {symbol}: {len(records)} option records parsed")
        if records and spot == 0:
            # Estimate spot from ATM (strike with highest CE+PE combined LTP change)
            atm_row = min(records, key=lambda r: abs(
                r["CE"]["lastPrice"] - r["PE"]["lastPrice"]
            ))
            spot = atm_row["strikePrice"]
            log.info(f"[NSE] Estimated spot from ATM: {spot:.0f}")

        return {
            "records": {
                "underlyingValue": spot,
                "data": records,
            }
        }

    except Exception as e:
        log.warning(f"[NSE] load error: {e}")
        return None

def get_option_chain(symbol: str) -> Optional[dict]:
    now = datetime.now()
    if (symbol in _cache and
            (now - _cache_ts.get(symbol, datetime.min)).total_seconds() < CACHE_TTL):
        return _cache[symbol]
    data = _load_chain(symbol)
    if data:
        _cache[symbol] = data
        _cache_ts[symbol] = now
    return data

def get_spot(symbol: str) -> float:
    data = get_option_chain(symbol)
    if not data:
        return 0.0
    return float(data.get("records", {}).get("underlyingValue", 0))

def get_option_price(symbol: str, strike: float, opt: str, expiry_iso: str) -> float:
    data = get_option_chain(symbol)
    if not data:
        return 0.0
    try:
        for row in data.get("records", {}).get("data", []):
            if abs(float(row.get("strikePrice", 0)) - strike) < 1:
                ltp = float(row.get(opt, {}).get("lastPrice", 0))
                if ltp > 0:
                    log.info(f"[NSE] {symbol} {strike:.0f}{opt}=Rs.{ltp}")
                    return ltp
    except Exception as e:
        log.debug(f"parse: {e}")
    return 0.0

def get_atm_prices(symbol: str, spot: float, expiry_iso: str, gap: int = 50) -> dict:
    atm = round(spot / gap) * gap
    return {
        "CE":     get_option_price(symbol, atm, "CE", expiry_iso),
        "PE":     get_option_price(symbol, atm, "PE", expiry_iso),
        "strike": atm,
    }

def cleanup():
    global _driver
    if _driver:
        try: _driver.quit()
        except: pass
        _driver = None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print("\n=== NSE Real Price Test ===\n")
    for sym, gap, wd in [("NIFTY", 50, 1), ("SENSEX", 100, 3)]:
        print(f"\n--- {sym} ---")
        spot = get_spot(sym)
        if spot > 0:
            today  = date.today()
            days   = (wd - today.weekday()) % 7
            expiry = (today + timedelta(days=days if days > 0 else 7)).strftime("%Y-%m-%d")
            px     = get_atm_prices(sym, spot, expiry, gap)
            print(f"  Spot   : {spot:.0f}")
            print(f"  Expiry : {expiry}")
            print(f"  ATM    : {px['strike']:.0f}")
            print(f"  CE LTP : Rs.{px['CE']}")
            print(f"  PE LTP : Rs.{px['PE']}")
        else:
            print(f"  No data")
    cleanup()
    print("\nDone.")