"""
alerts.py — Trade alerts and notifications system
Provides desktop notifications, sound alerts, and visual logging
"""

import os
import sys
import winsound
import urllib.parse
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

def send_whatsapp(message: str):
    """Send a WhatsApp alert based on the config.py settings."""
    try:
        from config import cfg
    except ImportError:
        return

    if not getattr(cfg, "whatsapp_enabled", False):
        return

    provider = getattr(cfg, "whatsapp_provider", "callmebot").lower()

    # CallMeBot Provider
    if provider == "callmebot":
        phone = getattr(cfg, "whatsapp_phone", "")
        apikey = getattr(cfg, "whatsapp_apikey", "")
        if not phone or not apikey:
            print("[ALERT ERROR] WhatsApp CallMeBot credentials missing in config.py")
            return
        
        try:
            formatted_phone = phone.replace("+", "").replace(" ", "").strip()
            encoded_msg = urllib.parse.quote(message)
            url = f"https://api.callmebot.com/whatsapp.php?phone={formatted_phone}&text={encoded_msg}&apikey={apikey}"
            
            # Send GET request asynchronously with a short timeout to not block thread
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                print(f"[ALERT] WhatsApp notification sent via CallMeBot.")
            else:
                print(f"[ALERT ERROR] CallMeBot returned status {r.status_code}: {r.text}")
        except Exception as e:
            print(f"[ALERT ERROR] Failed to send WhatsApp message via CallMeBot: {e}")

    # Twilio Provider
    elif provider == "twilio":
        sid = getattr(cfg, "whatsapp_twilio_sid", "")
        token = getattr(cfg, "whatsapp_twilio_auth_token", "")
        twilio_from = getattr(cfg, "whatsapp_twilio_from", "whatsapp:+14155238886")
        twilio_to = getattr(cfg, "whatsapp_twilio_to", "")
        
        if not sid or not token or not twilio_to:
            print("[ALERT ERROR] WhatsApp Twilio credentials missing in config.py")
            return
            
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
            formatted_to = twilio_to if twilio_to.startswith("whatsapp:") else f"whatsapp:{twilio_to}"
            data = {
                "From": twilio_from,
                "To": formatted_to,
                "Body": message
            }
            r = requests.post(
                url, 
                data=data, 
                auth=(sid, token),
                timeout=8
            )
            if r.status_code in (200, 201):
                print(f"[ALERT] WhatsApp notification sent via Twilio.")
            else:
                print(f"[ALERT ERROR] Twilio returned status {r.status_code}: {r.text}")
        except Exception as e:
            print(f"[ALERT ERROR] Failed to send WhatsApp message via Twilio: {e}")

    # Green-API Provider
    elif provider == "green-api":
        instance_id = getattr(cfg, "whatsapp_green_instance_id", "")
        token = getattr(cfg, "whatsapp_green_api_token", "")
        to_phone = getattr(cfg, "whatsapp_green_to_phone", "")
        
        if not instance_id or not token or not to_phone:
            print("[ALERT ERROR] WhatsApp Green-API credentials missing in config.py")
            return
            
        try:
            formatted_phone = to_phone.replace("+", "").replace(" ", "").strip()
            if not formatted_phone.endswith("@c.us"):
                chat_id = f"{formatted_phone}@c.us"
            else:
                chat_id = formatted_phone
                
            url = f"https://api.green-api.com/waInstance{instance_id}/sendMessage/{token}"
            payload = {
                "chatId": chat_id,
                "message": message
            }
            r = requests.post(url, json=payload, timeout=8)
            if r.status_code == 200:
                print(f"[ALERT] WhatsApp notification sent via Green-API.")
            else:
                print(f"[ALERT ERROR] Green-API returned status {r.status_code}: {r.text}")
        except Exception as e:
            print(f"[ALERT ERROR] Failed to send WhatsApp message via Green-API: {e}")

def send_ntfy(message: str, title: str = None, priority: int = 3, tags: str = None):
    """Send a mobile push alert via ntfy.sh (Zero API key / Free push gateway)."""
    try:
        from config import cfg
    except ImportError:
        return

    if not getattr(cfg, "ntfy_enabled", False):
        return

    topic = getattr(cfg, "ntfy_topic", "").strip()
    if not topic:
        print("[ALERT ERROR] ntfy.sh topic is missing in config.py")
        return

    try:
        url = f"https://ntfy.sh/{topic}"
        headers = {}
        if title:
            from email.header import Header
            try:
                headers["Title"] = Header(title, 'utf-8').encode()
            except Exception:
                headers["Title"] = title
        if priority:
            headers["Priority"] = str(priority)
        if tags:
            headers["Tags"] = tags

        r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=8)
        if r.status_code == 200:
            print(f"[ALERT] ntfy.sh push notification sent successfully to topic: '{topic}'.")
        else:
            print(f"[ALERT ERROR] ntfy.sh returned status {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[ALERT ERROR] Failed to send ntfy.sh notification: {e}")

if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr is not None:
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

IST = ZoneInfo("Asia/Kolkata")

def _now_str() -> str:
    """Current time in IST."""
    return datetime.now(IST).strftime("%H:%M:%S")

def desktop_notify(title: str, message: str, duration: int = 5):
    """Show Windows desktop notification."""
    try:
        # PowerShell command to show toast notification
        ps_cmd = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications.ToastNotification] > $null
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Python").Show(
    [Windows.UI.Notifications.ToastNotification]::new(
        [xml]@"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{title}</text>
            <text id="2">{message}</text>
        </binding>
    </visual>
</toast>
"@
    )
)
"""
        os.system(f'powershell -NoProfile -Command "{ps_cmd}"')
    except Exception as e:
        pass  # Silently fail if notification doesn't work

def sound_alert(alert_type: str = "info"):
    """Play system sound alert."""
    try:
        if alert_type == "entry":
            # Double beep for entry
            winsound.Beep(800, 200)
            winsound.Beep(800, 200)
        elif alert_type == "win":
            # Success sound (ascending beeps)
            winsound.Beep(800, 150)
            winsound.Beep(1000, 150)
            winsound.Beep(1200, 150)
        elif alert_type == "loss":
            # Loss sound (descending beeps)
            winsound.Beep(1200, 150)
            winsound.Beep(1000, 150)
            winsound.Beep(800, 150)
        else:
            # Info sound
            winsound.Beep(1000, 100)
    except Exception:
        pass

def alert_entry(direction: str, strike: float, entry: float, sl: float, tp: float, index: str = "NIFTY", lots: int = 5, contracts: int = 325, guard_status: str = "Disabled", predicted_win_prob: float = 100.0):
    """Alert on trade entry."""
    opt = "CALL" if direction == "CALL" else "PUT"
    display_index = "NIFTY_ALGO_SHARK" if index == "NIFTY" else index
    title = f"[ENTRY] {direction} | {lots} Lots Taken"
    message = f"{display_index} {int(strike)}{opt}\nEntry: Rs.{entry} | Lots: {lots}"

    sound_alert("entry")
    desktop_notify(title, message)

    # Format Guard Status string
    if guard_status == "Active":
        guard_text = f"🛡️ Active (Win Prob: {predicted_win_prob:.1f}%)"
    elif guard_status == "Learning":
        guard_text = "✏️ Learning Mode (Auto-Approved)"
    else:
        guard_text = "⚠️ Disabled (Pure Signal)"

    # Send WhatsApp Alert
    wa_msg = (
        f"🛡️ *[SMART ALGO] TRADE ENTRY*\n\n"
        f"📍 *Index*: {display_index}\n"
        f"📈 *Direction*: {direction}\n"
        f"🎯 *Strike*: {int(strike)} ({opt})\n"
        f"💰 *Entry Price*: Rs.{entry}\n"
        f"🛡️ *SL*: Rs.{sl} | *TP*: Rs.{tp}\n"
        f"📦 *Lots*: {lots} ({contracts} contracts)\n"
        f"🛡️ *Guard Status*: {guard_text}"
    )
    send_whatsapp(wa_msg)
    send_ntfy(wa_msg, title=title, priority=4, tags="chart_with_upwards_trend,bell")
    print(f"\n{'='*60}")
    print(f"  [ENTRY] TRADE ENTRY ALERT")
    print(f"  Time: {_now_str()}")
    print(f"  Index: {display_index}")
    print(f"  Direction: {direction}")
    print(f"  Strike: {int(strike)} ({opt})")
    print(f"  Lots Taken: {lots} ({contracts} contracts)")
    print(f"  Entry: Rs.{entry} | SL: Rs.{sl} | TP: Rs.{tp}")
    print(f"  Smart Guard: {guard_text}")
    print(f"{'='*60}\n")

def alert_win(tid: str, pnl: float, pnl_pct: float, exit_price: float, lots: int = 5, contracts: int = 325):
    """Alert on winning trade."""
    title = f"[WIN] +Rs.{pnl:,.0f} | {lots} Lots Exited"
    message = f"P&L: +Rs.{pnl:,.0f} ({pnl_pct:+.1f}%)\nLots Exited: {lots}"

    sound_alert("win")
    desktop_notify(title, message)

    # Send WhatsApp Alert
    wa_msg = (
        f"✅ *[SMART ALGO] WINNING EXIT*\n\n"
        f"🆔 *Trade ID*: {tid}\n"
        f"💵 *Net P&L*: +Rs.{pnl:,.2f} ({pnl_pct:+.2f}%)\n"
        f"🎯 *Exit Price*: Rs.{exit_price}\n"
        f"📦 *Lots*: {lots} ({contracts} contracts)"
    )
    send_whatsapp(wa_msg)
    send_ntfy(wa_msg, title=title, priority=4, tags="heavy_check_mark,moneybag")
    print(f"\n{'='*60}")
    print(f"  [WIN] WINNING TRADE")
    print(f"  Time: {_now_str()}")
    print(f"  Trade ID: {tid}")
    print(f"  Lots Exited: {lots} ({contracts} contracts)")
    print(f"  P&L: +Rs.{pnl:,.0f} ({pnl_pct:+.1f}%)")
    print(f"  Exit: Rs.{exit_price}")
    print(f"{'='*60}\n")

def alert_loss(tid: str, pnl: float, pnl_pct: float, exit_price: float, reason: str, lots: int = 5, contracts: int = 325):
    """Alert on losing trade."""
    title = f"[LOSS] -Rs.{abs(pnl):,.0f} | {lots} Lots Exited"
    message = f"P&L: -Rs.{abs(pnl):,.0f} ({pnl_pct:.1f}%)\nLots Exited: {lots}"

    sound_alert("loss")
    desktop_notify(title, message)

    # Send WhatsApp Alert
    wa_msg = (
        f"❌ *[SMART ALGO] LOSING EXIT*\n\n"
        f"🆔 *Trade ID*: {tid}\n"
        f"💵 *Net P&L*: -Rs.{abs(pnl):,.2f} ({pnl_pct:.2f}%)\n"
        f"⚠️ *Reason*: {reason}\n"
        f"🎯 *Exit Price*: Rs.{exit_price}\n"
        f"📦 *Lots*: {lots} ({contracts} contracts)"
    )
    send_whatsapp(wa_msg)
    send_ntfy(wa_msg, title=title, priority=4, tags="x,warning")
    print(f"\n{'='*60}")
    print(f"  [LOSS] LOSING TRADE")
    print(f"  Time: {_now_str()}")
    print(f"  Trade ID: {tid}")
    print(f"  Lots Exited: {lots} ({contracts} contracts)")
    print(f"  P&L: -Rs.{abs(pnl):,.0f} ({pnl_pct:.1f}%)")
    print(f"  Reason: {reason}")
    print(f"  Exit: Rs.{exit_price}")
    print(f"{'='*60}\n")

def alert_target_hit(capital: float, cum_pnl: float):
    """Alert when profit target is hit."""
    title = "[TARGET HIT] PROFIT TARGET HIT!"
    message = f"Cumulative P&L: Rs.{cum_pnl:,.0f}\nCapital: Rs.{capital:,.0f}"

    # Triple beep celebration
    for _ in range(3):
        winsound.Beep(1000, 150)

    desktop_notify(title, message)

    # Send WhatsApp Alert
    wa_msg = (
        f"🚀 *[SMART ALGO] GOAL TARGET HIT!*\n\n"
        f"💰 *Cumulative P&L*: +Rs.{cum_pnl:,.2f}\n"
        f"💳 *Current Capital*: Rs.{capital:,.2f}\n\n"
        f"Automated execution has successfully hit its profit targets and locked in gains!"
    )
    send_whatsapp(wa_msg)
    send_ntfy(wa_msg, title=title, priority=5, tags="rocket,trophy")
    print(f"\n{'#'*60}")
    print(f"  [TARGET HIT] PROFIT TARGET HIT!")
    print(f"  Time: {_now_str()}")
    print(f"  Cumulative P&L: Rs.{cum_pnl:,.0f}")
    print(f"  Capital: Rs.{capital:,.0f}")
    print(f"{'#'*60}\n")

def alert_daily_halt(day_pnl: float):
    """Alert when daily loss limit reached."""
    title = "[HALT] DAILY HALT"
    message = f"Daily P&L: Rs.{day_pnl:,.0f}\nLoss limit reached"

    winsound.Beep(500, 200)
    winsound.Beep(500, 200)

    desktop_notify(title, message)

    # Send WhatsApp Alert
    wa_msg = (
        f"⚠️ *[SMART ALGO] DAILY LOSS HALT*\n\n"
        f"💵 *Daily Net P&L*: -Rs.{abs(day_pnl):,.2f}\n"
        f"Daily drawdown limit reached. Automated scanning has been suspended to guard capital."
    )
    send_whatsapp(wa_msg)
    send_ntfy(wa_msg, title=title, priority=5, tags="octagonal_sign,warning")
    print(f"\n{'!'*60}")
    print(f"  [HALT] DAILY LOSS LIMIT REACHED")
    print(f"  Time: {_now_str()}")
    print(f"  Day P&L: Rs.{day_pnl:,.0f}")
    print(f"{'!'*60}\n")
