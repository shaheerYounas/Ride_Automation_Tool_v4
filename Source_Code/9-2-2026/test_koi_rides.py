import json
import time
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ==========================================
# 1. CONFIGURATION
# ==========================================
KOI_LOGIN_URL = "https://taxiportal.koiride.com/auth/sign-in"
KOI_USER = "Haytham Montana"
KOI_PASS = "montana123"
RAW_FILE_NAME = "koi_raw_data.json"

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def get_koi_api_token(driver):
    """Scans performance logs for the KOI API token."""
    print("   -> 🕵️ Sniffing API Token...")
    try:
        time.sleep(2)
        logs = driver.get_log("performance")
        
        for entry in logs:
            try:
                message = json.loads(entry["message"])["message"]
                if "Network.requestWillBeSent" in message["method"]:
                    params = message["params"]
                    request_url = params["request"]["url"]
                    
                    if "api.koiride.com" in request_url:
                        headers = params["request"]["headers"]
                        token = headers.get("Authorization") or headers.get("authorization")
                        
                        if token and len(token) > 20:
                            return token
            except: continue
    except Exception as e:
        print(f"   ⚠️ Error finding Koi Token: {e}")
        
    return None

# ==========================================
# 3. MAIN LOGIC
# ==========================================

def fetch_koi_filtered():
    print("\n🚀 STARTING KOI RIDES FETCHER (TODAY/TOMORROW/DAY AFTER)...")
    
    # --- CALCULATE DATES ---
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after_str = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    
    target_dates = [today_str, tomorrow_str, day_after_str]
    print(f"   -> 📅 Target Dates: {target_dates}")

    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)

    try:
        # --- 1. LOGIN ---
        driver.get(KOI_LOGIN_URL)
        time.sleep(5)
        
        if "auth" in driver.current_url:
            print("   -> 🔑 Logging in...")
            try:
                user_in = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']")))
                user_in.clear()
                user_in.send_keys(KOI_USER)
                pass_in = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
                pass_in.clear()
                pass_in.send_keys(KOI_PASS)
                pass_in.send_keys(Keys.RETURN)
                time.sleep(8)
            except Exception as e:
                print(f"   ❌ Login Failed: {e}")
                return

        # --- 2. GET TOKEN ---
        print("   -> 🔄 Refreshing to capture token...")
        driver.refresh()
        time.sleep(5)
        
        access_token = get_koi_api_token(driver)
        if not access_token:
            print("   ❌ Failed to retrieve API Token.")
            return

        print("   -> ✅ API Token Retrieved.")

        # --- 3. FETCH & FILTER ---
        print("   -> ⚡ Scanning pages for matches...")
        
        js_script = """
        var callback = arguments[arguments.length - 1];
        fetch(arguments[0], {
            method: 'GET',
            headers: {
                'Accept': 'application/json, text/plain, */*',
                'Authorization': arguments[1] 
            }
        })
        .then(response => response.json())
        .then(data => callback(data))
        .catch(err => callback({'error': err.toString()}));
        """

        filtered_rides = []
        
        # Scan up to 20 pages (2000 rides) to be safe
        for page in range(1, 21):
            # Fetch 100 per page
            api_url = f"https://api.koiride.com/api/v3/reservation/all?sortBy=pickupDate&desc=true&pageNumber={page}&pageSize=100&driverAssigned=true"
            
            try:
                response = driver.execute_async_script(js_script, api_url, access_token)
                results = response.get('rows', [])
                
                if not results:
                    print(f"      Page {page}: Empty. Stopping.")
                    break
                
                # Filter this page
                matches_in_page = 0
                for r in results:
                    r_date = r.get('pickupDate', '')
                    if r_date in target_dates:
                        filtered_rides.append(r)
                        matches_in_page += 1
                
                print(f"      Page {page}: Scanned 100 rides -> {matches_in_page} matched target dates.")
                
                # OPTIONAL: Optimization check
                # If we encounter dates strictly older than 'Today' and sorting is Descending, we could potentially break.
                # However, sticking to a page limit is safer if sorting isn't perfect.
                
            except Exception as e:
                print(f"      ⚠️ Error on page {page}: {e}")
                break

        # --- 4. SAVE JSON ---
        if filtered_rides:
            with open(RAW_FILE_NAME, 'w', encoding='utf-8') as f:
                json.dump(filtered_rides, f, indent=4)
            print(f"\n   -> 💾 Complete raw data saved to: {RAW_FILE_NAME}")
        else:
            print("\n   -> ⚠️ No rides found for the specified dates.")

        # --- 5. PRINT SUMMARY ---
        print(f"\n✅ SUCCESS: Found {len(filtered_rides)} relevant rides.")
        if filtered_rides:
            print("\n--- RIDE LIST (Today, Tomorrow, After Tomorrow) ---")
            print(f"{'ID':<10} | {'Date':<12} | {'Time':<6} | {'Passenger':<20} | {'Vehicle'}")
            print("-" * 80)
            
            # Sort by Date for display
            filtered_rides.sort(key=lambda x: x.get('pickupDate', ''))

            for r in filtered_rides:
                try:
                    rid = str(r.get('reservationId', 'Unknown'))
                    date = r.get('pickupDate', 'N/A')
                    time_str = r.get('pickupTime', 'N/A')
                    fname = r.get('customerFirstName', '')
                    lname = r.get('customerLastName', '')
                    name = f"{fname} {lname}".strip()
                    car = r.get('carType', {}).get('carTypeName', 'Unknown')
                    
                    print(f"{rid:<10} | {date:<12} | {time_str:<6} | {name[:19]:<20} | {car}")
                except: continue
            print("-" * 80)

    except Exception as e:
        print(f"❌ Critical Error: {e}")
    finally:
        input("\nPress Enter to close browser...")
        driver.quit()

if __name__ == "__main__":
    fetch_koi_filtered()