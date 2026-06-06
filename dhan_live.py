"""
dhan_live.py — Live execution, standard orders, and advanced Super Orders for Dhan.
"""

import logging
import requests
from typing import Optional
from dhan_client import dhan
from config import cfg

log = logging.getLogger("trader")

def place_dhan_order(sec_id: str, action: str, quantity: int, index: str, client = None) -> Optional[str]:
    """Place a standard live order (BUY/SELL) on Dhan API."""
    try:
        log.info(f"[DHAN] ⚡ PLACING LIVE {action} ORDER | SecID: {sec_id} | Qty: {quantity} | Index: {index}")
        dhan_inst = client or dhan
        order_resp = dhan_inst._dhan.place_order(
            security_id=sec_id,
            exchange_segment="NSE_FNO" if index != "SENSEX" and index != "BANKEX" else "BSE_FNO",
            transaction_type=action,
            quantity=quantity,
            order_type="MARKET",
            product_type="INTRADAY",
            price=0.0,
            validity="DAY"
        )
        log.info(f"[DHAN] Place order response: {order_resp}")
        if order_resp.get("status") == "success" or "orderId" in str(order_resp):
            data = order_resp.get("data", {})
            if isinstance(data, dict):
                order_id = str(data.get("orderId", ""))
            else:
                order_id = str(order_resp.get("orderId", ""))
            log.info(f"[DHAN] ✅ Live order placed successfully! OrderID: {order_id}")
            return order_id
        else:
            remarks = order_resp.get("remarks", "Unknown error")
            log.error(f"[DHAN] ❌ Live order placement failed: {remarks}.")
    except Exception as e:
        log.error(f"[DHAN] ❌ Exception placing live order: {e}.")
    return None

def place_dhan_super_order(sec_id: str, action: str, quantity: int, index: str, entry_px: float, sl_px: float, tp_px: float, client_id = None, access_token = None) -> Optional[str]:
    """
    Place a live Super Order (Bracket Order) on Dhan API.
    Bundles the entry leg, Stop Loss (SL) leg, and Profit Target (TP) leg into a single OCO request.
    """
    try:
        log.info(f"[DHAN-SUPER] ⚡ PLACING SUPER {action} ORDER | SecID: {sec_id} | Qty: {quantity} | Entry: {entry_px} | SL: {sl_px} | TP: {tp_px}")
        
        c_id = client_id or cfg.client_id
        tok = access_token or cfg.access_token
        
        headers = {
            'access-token': tok,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        payload = {
            "dhanClientId": c_id,
            "transactionType": action,
            "exchangeSegment": "NSE_FNO" if index != "SENSEX" and index != "BANKEX" else "BSE_FNO",
            "productType": "INTRADAY",
            "orderType": "LIMIT", # LIMIT order ensures exact premium entry trigger
            "validity": "DAY",
            "securityId": sec_id,
            "quantity": quantity,
            "price": round(entry_px, 1),
            "targetPrice": round(tp_px, 1),
            "stopLossPrice": round(sl_px, 1)
        }
        
        url = "https://api.dhan.co/v2/super/orders"
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        log.info(f"[DHAN-SUPER] Place order HTTP Status: {response.status_code}")
        
        if response.status_code == 200:
            order_resp = response.json()
            log.info(f"[DHAN-SUPER] Place order response: {order_resp}")
            if order_resp.get("status") == "success" or "orderId" in str(order_resp):
                data = order_resp.get("data", {})
                if isinstance(data, dict):
                    order_id = str(data.get("orderId", ""))
                else:
                    order_id = str(order_resp.get("orderId", ""))
                log.info(f"[DHAN-SUPER] ✅ Live Super Order placed successfully! OrderID: {order_id}")
                return order_id
            else:
                remarks = order_resp.get("remarks", "Unknown error")
                log.error(f"[DHAN-SUPER] ❌ Super Order placement failed: {remarks}.")
        else:
            log.error(f"[DHAN-SUPER] ❌ HTTP Error {response.status_code}: {response.text}")
    except Exception as e:
        log.error(f"[DHAN-SUPER] ❌ Exception placing live Super Order: {e}.")
    return None

def cancel_dhan_super_order(order_id: str, access_token = None) -> bool:
    """Cancel a pending Super Order on Dhan (e.g., for manual/EOD force exit)."""
    try:
        log.info(f"[DHAN-SUPER] 🛑 CANCELLING SUPER ORDER | OrderID: {order_id}")
        tok = access_token or cfg.access_token
        headers = {
            'access-token': tok,
            'Accept': 'application/json'
        }
        url = f"https://api.dhan.co/v2/super/orders/{order_id}"
        response = requests.delete(url, headers=headers, timeout=10)
        if response.status_code == 200:
            log.info(f"[DHAN-SUPER] ✅ Super Order {order_id} cancelled successfully.")
            return True
        else:
            log.error(f"[DHAN-SUPER] ❌ Failed to cancel Super Order: {response.text}")
    except Exception as e:
        log.error(f"[DHAN-SUPER] ❌ Exception cancelling Super Order: {e}.")
    return False
