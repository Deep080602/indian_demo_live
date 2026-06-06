"""
test_ntfy.py — Diagnostic script to test ntfy.sh mobile push alerts.
Run this script using: python test_ntfy.py
"""

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
log = logging.getLogger("test_ntfy")

try:
    from config import cfg
    from alerts import send_ntfy
except ImportError as e:
    log.error(f"Failed to import modules: {e}")
    sys.exit(1)

def run_test():
    print("=" * 60)
    print(" 🔔 ntfy.sh Mobile Push Alerts Diagnostic Tester")
    print("=" * 60)
    print(f"Status in config.py:")
    print(f" - Enabled : {cfg.ntfy_enabled}")
    print(f" - Topic   : '{cfg.ntfy_topic or '[MISSING]'}'")
    print("-" * 60)
    print("💡 HOW TO VIEW YOUR ALERTS:")
    print(" 1. Mobile App: Download the free 'ntfy' app (Android Play Store / iOS App Store).")
    print(f"    Open the app, tap '+', and subscribe to topic: '{cfg.ntfy_topic}'")
    print(" 2. Web Browser: Visit this URL on any device to view live:")
    print(f"    https://ntfy.sh/{cfg.ntfy_topic}")
    print("-" * 60)

    if not cfg.ntfy_enabled:
        print("⚠️ ntfy alerts are currently DISABLED in config.py.")
        print("Please edit config.py and set 'ntfy_enabled = True' to proceed with testing.")
        return

    test_message = (
        "🔔 *[ALGO TEST]* ntfy.sh Mobile Push Alerts are active!\n\n"
        "📈 Status: Active / Online\n"
        "⚡ Ready to stream real-time trade signals, exits, and alerts."
    )
    
    print("Sending test high-priority push notification...")
    send_ntfy(
        test_message,
        title="🔔 SMART ALGO ONLINE",
        priority=5,
        tags="heavy_check_mark,rocket"
    )
    print("=" * 60)

if __name__ == "__main__":
    run_test()
