"""
groww_live.py — Live execution and order placement logic for Groww.
"""

import logging
from typing import Optional
from groww_client import groww

log = logging.getLogger("trader")

def place_groww_order(sec_id: str, action: str, quantity: int, index: str, client = None) -> Optional[str]:
    """Place a live order (BUY/SELL) on Groww."""
    try:
        log.info(f"[GROWW] ⚡ PLACING LIVE {action} ORDER | SecID: {sec_id} | Qty: {quantity} | Index: {index}")
        
        groww_inst = client or groww
        # Check if we are using the official SDK wrapper or the simulated fallback
        if groww_inst.authenticated:
            # For the official SDK, security_id is the trading symbol (e.g. NIFTY2660221100CE)
            # If sec_id starts with 'GRW_SEC_', it means it's a fallback dummy. We should try to resolve the real trading symbol.
            trading_symbol = sec_id
            if sec_id.startswith("GRW_SEC_"):
                # Parse strike and opt from dummy
                try:
                    parts = sec_id.split("_")
                    strike = float(parts[2])
                    opt = parts[3]
                    from demo_trade import _get_groww_contract_symbol
                    resolved = _get_groww_contract_symbol(index, strike, opt)
                    if resolved:
                        trading_symbol = resolved
                        log.info(f"[GROWW] Resolved dummy {sec_id} to official symbol: {trading_symbol}")
                except Exception as ex:
                    log.error(f"[GROWW] Failed to parse dummy symbol: {ex}")
            
            order_resp = groww_inst._groww.place_order(
                validity="DAY",
                exchange="NSE" if index in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY") else "BSE",
                order_type="MARKET",
                product="MIS",
                quantity=quantity,
                segment="FNO",
                trading_symbol=trading_symbol,
                transaction_type=action,
                price=0.0
            )
        else:
            # Simulated fallback or custom mock wrapper
            order_resp = groww_inst._groww.place_order(
                security_id=sec_id,
                exchange_segment="NSE_FNO" if index == "NIFTY" else "BSE_FNO",
                transaction_type=action,
                quantity=quantity,
                order_type="MARKET",
                product_type="INTRADAY",
                price=0.0,
                validity="DAY"
            )
            
        log.info(f"[GROWW] Place order response: {order_resp}")
        if order_resp.get("status") == "success" or "orderId" in str(order_resp) or "groww_order_id" in order_resp:
            # Parse order ID correctly by checking root keys first
            order_id = str(order_resp.get("groww_order_id") or order_resp.get("orderId") or "")
            if not order_id:
                data = order_resp.get("data", {})
                if isinstance(data, dict):
                    order_id = str(data.get("orderId") or data.get("groww_order_id") or "")
            
            if order_id:
                log.info(f"[GROWW] ✅ Live order placed successfully! OrderID: {order_id}")
                return order_id
            else:
                log.error("[GROWW] ❌ Placed order but orderId / groww_order_id could not be parsed.")
        else:
            remarks = order_resp.get("remarks", order_resp.get("remark", "Unknown error"))
            log.error(f"[GROWW] ❌ Live order placement failed: {remarks}.")
    except Exception as e:
        err_msg = str(e)
        if "unregistered IP" in err_msg or "No registered IPs" in err_msg or "registered IP" in err_msg:
            try:
                import requests
                public_ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
            except Exception:
                public_ip = "Unknown"
            log.error(
                f"[GROWW] ❌ IP ADDRESS NOT REGISTERED: You must register your public IP address in the Groww Cloud developer console. "
                f"Go to https://groww.in/trade-api/api-keys and add your public IP address: {public_ip}"
            )
        else:
            log.error(f"[GROWW] ❌ Exception placing live order: {e}.")
    return None
