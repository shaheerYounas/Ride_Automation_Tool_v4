import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIG ---
GETE_LOGIN_URL = "https://app.portal.get-e.com/rides"
GETE_EMAIL = "haytham97@live.com"
GETE_PASS  = "Stuntman1997!@"

def sniff_gete_network():
    print("🕵️ STARTING NETWORK SNIFFER FOR GET-E...")
    
    # Enable Performance Logging
    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)

    try:
        # 1. Login
        driver.get(GETE_LOGIN_URL)
        print("   -> Logging in...")
        
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))).send_keys(GETE_EMAIL)
            driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(GETE_PASS)
            driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(Keys.RETURN)
        except:
            print("   ⚠️ Already logged in or login skipped.")

        # 2. Wait for Data to Load
        print("   -> Waiting for ride data to appear on screen...")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".MuiDataGrid-row")))
        time.sleep(5) # Wait extra time for background requests to finish

        # 3. CAPTURE LOGS
        print("\n🔍 ANALYZING NETWORK LOGS...")
        logs = driver.get_log("performance")
        
        candidates = []

        for entry in logs:
            try:
                message = json.loads(entry["message"])["message"]
                if "Network.requestWillBeSent" in message["method"]:
                    params = message["params"]
                    request = params["request"]
                    url = request["url"]
                    
                    # Filter for API-like URLs (JSON/GraphQL)
                    if "api" in url or "graphql" in url or "search" in url or "rides" in url:
                        # Ignore static assets
                        if any(x in url for x in [".js", ".css", ".png", ".svg", "fonts"]): continue
                        
                        method = request["method"]
                        headers = request.get("headers", {})
                        post_data = request.get("postData", "")
                        
                        # Store interesting requests
                        candidates.append({
                            "url": url,
                            "method": method,
                            "auth_header": headers.get("Authorization") or headers.get("authorization") or "NONE",
                            "content_type": headers.get("Content-Type") or "NONE",
                            "payload": post_data
                        })
            except: continue

        # 4. PRINT RESULTS
        print(f"   -> Found {len(candidates)} potential API calls.")
        print("="*60)
        
        for i, c in enumerate(candidates):
            print(f"[{i+1}] {c['method']} : {c['url']}")
            if c['auth_header'] != "NONE":
                print(f"    🔑 Auth Token: {c['auth_header'][:30]}...")
            if c['payload']:
                print(f"    📦 Payload: {c['payload'][:100]}...") # Print first 100 chars of payload
            print("-" * 60)

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        input("\nPress Enter to close browser...")
        driver.quit()

if __name__ == "__main__":
    sniff_gete_network()