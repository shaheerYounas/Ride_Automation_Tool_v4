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

def inspect_gete_final():
    print("🕵️ STARTING GET-E FINAL INSPECTION (CORS FIX)...")
    
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
            print("   ⚠️ Already logged in or skipped.")

        # 2. Wait for Dashboard
        print("   -> Waiting for dashboard to load...")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".MuiDataGrid-row")))
        time.sleep(5) 

        # 3. Inject API Call WITH CREDENTIALS
        print("   -> 💉 Injecting API Request with credentials: 'include'...")
        
        # Exact URL from your logs
        api_url = "https://portal.get-e.com/portal-api/trips?query=&statusFilters[]=TO_CONFIRM&statusFilters[]=CONFIRMED"
        
        js_script = """
        var callback = arguments[arguments.length - 1];
        
        fetch(arguments[0], {
            method: 'GET',
            credentials: 'include',  // <--- THIS IS THE FIX
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
        })
        .then(response => response.json())
        .then(data => callback(data))
        .catch(err => callback({'error': err.toString()}));
        """
        
        response = driver.execute_async_script(js_script, api_url)
        
        # 4. Analyze Results
        if 'error' in response:
            print(f"❌ API Error: {response['error']}")
        elif 'message' in response and len(response) == 1:
             print(f"❌ Server Message: {response['message']}")
        else:
            # Try to find the list of rides
            rides = []
            if isinstance(response, list):
                rides = response
            elif isinstance(response, dict):
                # Check common keys
                rides = response.get('content') or response.get('data') or response.get('trips') or []

            print(f"\n✅ SUCCESS! Found {len(rides)} rides.")
            
            if len(rides) > 0:
                print("\n--- 📄 RAW DATA SAMPLE (Copy this!) ---")
                sample = rides[0]
                print(json.dumps(sample, indent=4))
                print("---------------------------------------------")
            else:
                # If list is empty, print the keys to ensure we aren't missing a nested object
                print(f"   Response Keys: {response.keys() if isinstance(response, dict) else 'List'}")

    except Exception as e:
        print(f"❌ Script Error: {e}")
    finally:
        input("\nPress Enter to close...")
        driver.quit()

if __name__ == "__main__":
    inspect_gete_final()