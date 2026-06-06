import demo_trade
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

def run():
    driver = demo_trade._get_driver()
    if not driver:
        return
    print("Got driver, loading URL...")
    driver.get("https://groww.in/options/nifty")
    print("URL loaded, waiting a bit...")
    time.sleep(5)
    print("Taking screenshot...")
    driver.save_screenshot("groww_screenshot.png")
    print("Page title:", driver.title)
    print("Saved screenshot.")
    driver.quit()

if __name__ == '__main__':
    run()
