import demo_trade
import time

def run():
    driver = demo_trade._get_driver()
    if not driver: return
    driver.get("https://groww.in/options/nifty")
    time.sleep(5)
    html = driver.page_source
    with open('groww_page.html', 'w', encoding='utf-8') as f:
        f.write(html)
    driver.quit()

if __name__ == '__main__':
    run()
