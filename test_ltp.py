import demo_trade
import time

def test():
    print("Testing NIFTY LTP extraction from Groww...")
    chain_nifty = demo_trade._fetch_nse_chain("NIFTY")
    if chain_nifty:
        print(f"NIFTY Spot: {chain_nifty.get('spot')}")
        records = chain_nifty.get('records', [])
        print(f"NIFTY Total strikes found: {len(records)}")
        if records:
            strike = records[0]['strike']
            ltp = demo_trade._get_nse_ltp(strike, "CE", "NIFTY")
            print(f"NIFTY CE LTP for strike {strike}: {ltp}")
    else:
        print("Failed to fetch NIFTY chain.")



if __name__ == '__main__':
    test()
