import demo_trade
import time

def compare_ltp(index="NIFTY"):
    print(f"=== Comparing LTP for {index} ===")
    
    # 1. Fetch Option Chain
    chain = demo_trade._fetch_nse_chain(index)
    if not chain:
        print(f"Failed to fetch option chain for {index}")
        return
        
    spot = chain.get("spot", 0.0)
    records = chain.get("records", [])
    print(f"Spot Price: {spot}")
    print(f"Total Strikes: {len(records)}")
    
    if not records:
        print("No strikes found in the option chain.")
        return
        
    # Find ATM strike
    atm_record = min(records, key=lambda r: abs(r["strike"] - spot))
    atm_strike = atm_record["strike"]
    print(f"ATM Strike: {atm_strike}")
    
    # CE
    ce_ltp_chain = atm_record.get("ce_ltp", 0.0)
    ce_symbol = atm_record.get("ce_symbol")
    ce_ltp_direct = demo_trade._get_groww_contract_ltp(ce_symbol) if ce_symbol else 0.0
    
    print("\nCall Option (CE):")
    print(f"  Contract Symbol/ID: {ce_symbol}")
    print(f"  Chain LTP:          {ce_ltp_chain}")
    print(f"  Direct LTP:         {ce_ltp_direct}")
    ce_diff = abs(ce_ltp_chain - ce_ltp_direct)
    print(f"  Difference:         {ce_diff:.2f}")
    
    # PE
    pe_ltp_chain = atm_record.get("pe_ltp", 0.0)
    pe_symbol = atm_record.get("pe_symbol")
    pe_ltp_direct = demo_trade._get_groww_contract_ltp(pe_symbol) if pe_symbol else 0.0
    
    print("\nPut Option (PE):")
    print(f"  Contract Symbol/ID: {pe_symbol}")
    print(f"  Chain LTP:          {pe_ltp_chain}")
    print(f"  Direct LTP:         {pe_ltp_direct}")
    pe_diff = abs(pe_ltp_chain - pe_ltp_direct)
    print(f"  Difference:         {pe_diff:.2f}")
    
    # Check if they are matching
    ce_matches = ce_diff < 0.01
    pe_matches = pe_diff < 0.01
    
    print("\n=== Result ===")
    if ce_matches and pe_matches:
        print("✅ SUCCESS: LTPs from Option Chain and Direct Contract APIs are matching perfectly!")
    else:
        print("⚠️ WARNING: LTP mismatch detected!")
        if not ce_matches:
            print(f"  - CE mismatch by {ce_diff:.2f} (Chain: {ce_ltp_chain}, Direct: {ce_ltp_direct})")
        if not pe_matches:
            print(f"  - PE mismatch by {pe_diff:.2f} (Chain: {pe_ltp_chain}, Direct: {pe_ltp_direct})")

if __name__ == '__main__':
    compare_ltp("NIFTY")
    print()
    compare_ltp("SENSEX")
