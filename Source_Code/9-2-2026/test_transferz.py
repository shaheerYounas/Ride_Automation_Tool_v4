import json
import time
import re
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
TZ_LOGIN_URL = "https://rides.transferz.com/login"
TZ_JOURNEYS_URL = "https://rides.transferz.com/journeys"
TZ_EMAIL = "haytham97@live.com"
TZ_PASS  = "ZEuoHFzP78cp"
TZ_COMPANY_ID = 3843

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def get_tz_api_token(driver):
    """Sniffs the Bearer token from network logs"""
    print("   -> 🕵️ Sniffing Transferz API Token...")
    try:
        logs = driver.get_log("performance")
        for entry in logs:
            try:
                message = json.loads(entry["message"])["message"]
                if "Network.requestWillBeSent" in message["method"]:
                    params = message["params"]
                    request_url = params["request"]["url"]
                    
                    # We look for calls to the graphql api
                    if "graphql" in request_url or "api" in request_url:
                        headers = params["request"]["headers"]
                        token = headers.get("Authorization") or headers.get("authorization")
                        if token and "Bearer" in token:
                            return token
            except: continue
    except Exception as e:
        print(f"   ⚠️ Error sniffing token: {e}")
    return None

def clean_text(text):
    if not text: return ""
    return str(text).replace("\n", " ").strip()

# ==========================================
# 3. MAIN TEST LOGIC
# ==========================================

def test_transferz():
    print("\n🚀 STARTING TRANSFERZ DATA TEST...")
    
    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)

    try:
        # --- 1. LOGIN ---
        driver.get(TZ_LOGIN_URL)
        time.sleep(5)

        if "login" in driver.current_url:
            print("   -> 🔑 Logging in...")
            try:
                email_field = wait.until(EC.visibility_of_element_located((By.ID, "email")))
                email_field.clear()
                email_field.send_keys(TZ_EMAIL)
                
                pass_field = driver.find_element(By.NAME, "password")
                pass_field.send_keys(TZ_PASS)
                pass_field.send_keys(Keys.RETURN)
                
                # Wait for redirect
                time.sleep(8)
                print("   -> ✅ Login successful.")
            except Exception as e:
                print(f"   ❌ Login Failed: {e}")
                return

        # --- 2. GET TOKEN ---
        # We must be on the journeys page to trigger the API calls we want to sniff
        if "journeys" not in driver.current_url:
            driver.get(TZ_JOURNEYS_URL)
            time.sleep(6)
        
        access_token = get_tz_api_token(driver)
        
        if not access_token:
            print("   ❌ Failed to retrieve API Token. Check network logs.")
            return

        print("   -> ✅ API Token Retrieved.")

        # --- 3. INJECT GRAPHQL QUERY ---
        print("   -> ⚡ Fetching Rides (Next 90 Days)...")
        
        now = datetime.now()
        future = now + timedelta(days=90)
        date_start = now.strftime("%Y-%m-%dT00:00:00")
        date_end = future.strftime("%Y-%m-%dT23:59:59")

        # The exact query from your main script
        js_script = """
        const query = `query Journeys($params: JourneysRequestParams!, $skip: Boolean! = false) {
          journeys(params: $params) @skip(if: $skip) {
            results {
              id journeyCode inbound
              travellerInfo { firstName lastName phone flightNumber passengerCount driverComments luggageCount }
              journeyExecutionInfo { pickupDate vehicleCategory }
              originLocation { address { formattedAddress originalAddress } }
              destinationLocation { address { formattedAddress originalAddress } }
              driver { name }
              status
            }
          }
        }`;
        const variables = {
            "params": {
                "assignedTransferCompanyId": [%s],
                "excludedStatuses": ["NOT_PAID"],
                "includedStatuses": ["PLANNED", "CONFIRMED", "DRIVER_ARRIVED", "DRIVER_UNDERWAY", "JOURNEY_IN_PROGRESS", "COMPLETED"],
                "page": 0, "size": 50, "sort": ["pickup;asc"],
                "pickupDateAfter": "%s", "pickupDateBefore": "%s"
            }, "skip": false
        };
        
        var callback = arguments[arguments.length - 1];
        
        fetch('https://rides.transferz.com/api/graphql', {
            method: 'POST', 
            headers: { 
                'Content-Type': 'application/json', 
                'Authorization': '%s' 
            },
            body: JSON.stringify({ query: query, variables: variables })
        })
        .then(r => r.json())
        .then(d => callback(d))
        .catch(e => callback({"error": e.toString()}));
        """ % (TZ_COMPANY_ID, date_start, date_end, access_token)

        response = driver.execute_async_script(js_script)

        # --- 4. PARSE & PRINT RESULTS ---
        if 'error' in response:
            print(f"   ❌ API Error: {response['error']}")
        else:
            results = response.get('data', {}).get('journeys', {}).get('results', [])
            print(f"\n✅ SUCCESS: Found {len(results)} rides.")
            
            if results:
                # Table Header
                print("\n" + "="*145)
                print(f"{'ID':<12} | {'Date & Time':<18} | {'Passenger':<20} | {'Flight':<8} | {'Pickup':<35} | {'Dropoff':<35} | {'Driver'}")
                print("="*145)

                for r in results:
                    try:
                        r_id = r.get('journeyCode', 'Unknown')
                        
                        t_info = r.get('travellerInfo') or {}
                        j_info = r.get('journeyExecutionInfo') or {}
                        origin = r.get('originLocation') or {}
                        dest = r.get('destinationLocation') or {}
                        driver_info = r.get('driver') or {}

                        # Parse Date
                        raw_date = j_info.get('pickupDate', '')
                        if "T" in raw_date:
                            clean_date = raw_date.split(".")[0].replace("T", " ")
                        else:
                            clean_date = raw_date

                        # Parse Name
                        name = f"{t_info.get('firstName','')} {t_info.get('lastName','')}".strip()
                        
                        # Parse Route
                        pickup = (origin.get('address') or {}).get('originalAddress') or (origin.get('address') or {}).get('formattedAddress', 'Unknown')
                        dropoff = (dest.get('address') or {}).get('originalAddress') or (dest.get('address') or {}).get('formattedAddress', 'Unknown')
                        
                        # Parse Extras
                        flight = t_info.get('flightNumber', '-') or '-'
                        driver_name = driver_info.get('name') or "Unassigned"

                        # Truncate for table
                        print(f"{r_id:<12} | {clean_date:<18} | {name[:19]:<20} | {flight[:8]:<8} | {clean_text(pickup)[:34]:<35} | {clean_text(dropoff)[:34]:<35} | {driver_name}")
                        
                    except Exception as e:
                        print(f"   ⚠️ Error parsing row: {e}")

                print("="*145 + "\n")

    except Exception as e:
        print(f"❌ Critical Error: {e}")
    finally:
        input("Press Enter to close browser...")
        driver.quit()

if __name__ == "__main__":
    test_transferz()