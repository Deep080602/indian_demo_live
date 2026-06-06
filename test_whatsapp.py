"""
test_whatsapp.py — Diagnostic script to test WhatsApp trade alerts.
Run this script using: python test_whatsapp.py
"""

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
log = logging.getLogger("test_whatsapp")

try:
    from config import cfg
    from alerts import send_whatsapp
except ImportError as e:
    log.error(f"Failed to import modules: {e}")
    sys.exit(1)

def run_test():
    print("=" * 60)
    print(" 📞 WhatsApp Trade Alerts Diagnostic Tester")
    print("=" * 60)
    print(f"Status in config.py:")
    print(f" - Enabled  : {cfg.whatsapp_enabled}")
    print(f" - Provider : {cfg.whatsapp_provider}")
    
    if cfg.whatsapp_provider == "callmebot":
        print(f" - Phone    : {cfg.whatsapp_phone or '[MISSING]'}")
        print(f" - API Key  : {'*' * len(cfg.whatsapp_apikey) if cfg.whatsapp_apikey else '[MISSING]'}")
    elif cfg.whatsapp_provider == "twilio":
        print(f" - Account SID : {cfg.whatsapp_twilio_sid or '[MISSING]'}")
        print(f" - Twilio From  : {cfg.whatsapp_twilio_from}")
        print(f" - Twilio To    : {cfg.whatsapp_twilio_to or '[MISSING]'}")
    elif cfg.whatsapp_provider == "green-api":
        print(f" - Instance ID  : {getattr(cfg, 'whatsapp_green_instance_id', '[MISSING]') or '[MISSING]'}")
        print(f" - API Token    : {'*' * len(getattr(cfg, 'whatsapp_green_api_token', '')) if getattr(cfg, 'whatsapp_green_api_token', '') else '[MISSING]'}")
        print(f" - To Phone     : {getattr(cfg, 'whatsapp_green_to_phone', '[MISSING]') or '[MISSING]'}")
        
    print("-" * 60)
    
    if not cfg.whatsapp_enabled:
        print("⚠️ WhatsApp alerts are currently DISABLED in config.py.")
        print("Please edit config.py and set 'whatsapp_enabled = True' to proceed with testing.")
        return

    test_message = (
        "🔔 *[ALGO TEST]* WhatsApp Trade Alerts are successfully connected!\n\n"
        "📈 *Status*: Active / Online\n"
        "⚡ ready to stream real-time trade signals and alerts."
    )
    
    print("Sending test message...")
    send_whatsapp(test_message)
    print("=" * 60)

if __name__ == "__main__":
    run_test()
