import json
import time
import re
from datetime import datetime
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
GETE_LOGIN_URL = "https://app.portal.get-e.com/rides"
GETE_EMAIL = "haytham97@live.com"
GETE_PASS  = "Stuntman1997!@"
RAW_FILE_NAME = "gete_raw_data.json"

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def clean_text(text):
    if not text: return ""
    text = str(text).replace("\n", " ").replace("\r", "")
    return re.sub(' +', ' ', text).strip()

def print_grouped_table(rides):
    if not rides:
        print("\n❌ DATA ERROR: No parsed rides to display.")
        return

    # Group by Status
    grouped = {}
    for r in rides:
        s = r.get('status', 'UNKNOWN')
        if s not in grouped: grouped[s] = []
        grouped[s].append(r)

    print(f"\n📊 SUMMARY: Found {len(rides)} total rides.")
    
    headers = ["ID", "Date & Time", "Passenger", "Phone", "Route", "Vehicle", "Flight"]
    widths = [12, 18, 20, 15, 55, 15, 10]
    header_row = "  ".join(h.ljust(w) for h, w in zip(headers, widths))

    for status, items in grouped.items():
        items.sort(key=lambda x: x['datetime'])
        print(f"\n🔹 STATUS: {status} ({len(items)})")
        print("-" * len(header_row))
        print(header_row)
        print("-" * len(header_row))

        for r in items:
            route = f"{r['pickup']} -> {r['dropoff']}"
            if len(route) > 53: route = route[:50] + "..."
            
            row = [
                clean_text(r['id']).ljust(widths[0]),
                clean_text(r['datetime']).ljust(widths[1]),
                clean_text(r['name'])[:19].ljust(widths[2]),
                clean_text(r['phone']).ljust(widths[3]),
                clean_text(route).ljust(widths[4]),
                clean_text(r['vehicle']).ljust(widths[5]),
                clean_text(r['flight']).ljust(widths[6])
            ]
            print("  ".join(row))
        print("\n")

# ==========================================
# 3. GET-E FETCH LOGIC
# ==========================================

def fetch_gete_data():
    print("\n🚀 STARTING GET-E DEBUG FETCHER...")
    
    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)

    try:
        # --- 1. LOGIN ---
        driver.get(GETE_LOGIN_URL)
        time.sleep(3)
        
        if "login" in driver.current_url or "signin" in driver.current_url:
            print("   -> 🔑 Logging in...")
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))).send_keys(GETE_EMAIL)
                driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(GETE_PASS)
                driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(Keys.RETURN)
                wait.until(EC.url_contains("rides"))
                time.sleep(5)
                print("   -> ✅ Login Successful.")
            except Exception as e:
                print(f"   ❌ Login Failed: {e}")
                return []

        # --- 2. INJECT API REQUEST (PROVEN URL) ---
        print("   -> ⚡ Fetching data (Using exact known-good URL)...")
        
        # REVERTED TO THE SNIFFER URL THAT WORKED (Removed limit=100)
        api_url = "https://portal.get-e.com/portal-api/trips?query=&statusFilters[]=TO_CONFIRM&statusFilters[]=CONFIRMED"
        
        js_script = """
        var callback = arguments[arguments.length - 1];
        fetch(arguments[0], {
            method: 'GET',
            credentials: 'include',
            headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' }
        })
        .then(response => response.json())
        .then(data => callback(data))
        .catch(err => callback({'error': err.toString()}));
        """
        
        response = driver.execute_async_script(js_script, api_url)

        # --- 3. SAVE RAW JSON ---
        with open(RAW_FILE_NAME, 'w', encoding='utf-8') as f:
            json.dump(response, f, indent=4)
        print(f"   -> 💾 Raw data saved to: {RAW_FILE_NAME}")

        # --- 4. DEBUG & PARSE ---
        raw_rides = []
        
        # DEBUG: Check what we actually got
        if isinstance(response, dict):
            if 'error' in response:
                print(f"   ❌ API Error: {response['error']}")
                return []
            
            # Check keys to debug empty results
            print(f"   ℹ️  Response Keys: {list(response.keys())}")
            
            raw_rides = response.get('content') or response.get('data') or response.get('trips') or []
            
            # Fallback: Maybe the dict IS the ride object?
            if not raw_rides and 'unid' in response:
                print("   ℹ️  Response looks like a single ride object.")
                raw_rides = [response]
                
        elif isinstance(response, list):
            print(f"   ℹ️  Response is a List of {len(response)} items.")
            raw_rides = response

        print(f"   -> Found {len(raw_rides)} raw items.")

        parsed_rides = []
        for r in raw_rides:
            try:
                r_id = r.get('unid') or r.get('prettifiedUnid', '').replace('-', '')
                
                raw_date = r.get('pickUp', {}).get('departAtLocal', '')
                clean_date = raw_date[:16].replace("T", " ") if len(raw_date) >= 16 else "Unknown"

                passengers = r.get('passengers', [])
                if passengers:
                    p = passengers[0]
                    name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
                    phone = p.get('phone', '')
                else:
                    name = "Unknown"
                    phone = ""

                pickup = r.get('pickUp', {}).get('location', {}).get('name', 'Unknown')
                dropoff = r.get('dropOff', {}).get('location', {}).get('name', 'Unknown')
                veh = r.get('vehicle', {}).get('name', 'Standard')
                
                flight = r.get('flightDetails', {}).get('number', '')
                if not flight: flight = "-"
                status = r.get('status', 'Unknown')

                parsed_rides.append({
                    "id": r_id,
                    "datetime": clean_date,
                    "name": name,
                    "phone": phone,
                    "pickup": pickup,
                    "dropoff": dropoff,
                    "vehicle": veh,
                    "flight": flight,
                    "status": status
                })

            except: continue
        
        return parsed_rides

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        return []
    finally:
        driver.quit()

if __name__ == "__main__":
    data = fetch_gete_data()
    print_grouped_table(data)
    input("\nPress Enter to exit...")