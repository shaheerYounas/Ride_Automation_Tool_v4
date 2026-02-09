import json
import math
import os
import re
import time
from datetime import datetime, timedelta

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ==========================================
# 1. CONFIGURATION
# ==========================================

# --- SOURCE TOGGLES (ON/OFF SWITCHES) ---
# ENABLE_TRANSFERZ = True   # Set to False to disable Transferz
ENABLE_KOI = True         # Set to False to disable KOI Ride
# ENABLE_GETE = True        # Set to False to disable Get-e

ENABLE_TRANSFERZ = False   # Set to False to disable Transferz
# ENABLE_KOI = False         # Set to False to disable KOI Ride
ENABLE_GETE = False        # Set to False to disable Get-e


# --- TRANSFERZ (SOURCE A) ---
TZ_LOGIN_URL = "https://rides.transferz.com/login"
TZ_JOURNEYS_URL = "https://rides.transferz.com/journeys"
TZ_EMAIL = "haytham97@live.com"
TZ_PASS  = "ZEuoHFzP78cp"
TZ_COMPANY_ID = 3843

# --- KOI RIDE (SOURCE B) ---
KOI_LOGIN_URL = "https://taxiportal.koiride.com/auth/sign-in"
KOI_ASSIGNED_URL = "https://taxiportal.koiride.com/ridesAssigned"
KOI_USER = "Haytham Montana"
KOI_PASS = "montana123"

# --- GET-E (SOURCE C) ---
GETE_LOGIN_URL = "https://app.portal.get-e.com/rides"
GETE_EMAIL = "haytham97@live.com"
GETE_PASS  = "Stuntman1997!@"

# --- ACCOMMTRA (DESTINATION) ---
DEST_URL_LOGIN = "https://driver.accommtra.com/login"
DEST_EMAIL = "haytham97@live.com"
DEST_PASS  = "tham97"

# --- CONTRACTOR MAPPING ---
CONTRACTOR_ID_TZ   = "227"     # Transferz
CONTRACTOR_ID_KOI  = "269"     # Koi Ride
CONTRACTOR_TEXT_KOI = "Koi Ride" 
CONTRACTOR_TEXT_GETE = "GE CZ" # Get-e (Select by Text)

DEST_FORM_URL_BASE = "https://driver.accommtra.com/order-filter/item/date/"

# SETTINGS
DEFAULT_PRICE = "800" 
MEMORY_FILE = "processed_rides.txt"

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def load_processed_ids():
    """Load processed IDs from file. Extracts just the ID from format: DATE | SOURCE | ID"""
    if not os.path.exists(MEMORY_FILE):
        return []
    with open(MEMORY_FILE, "r") as f:
        lines = f.read().splitlines()
    
    ids = []
    for line in lines:
        if "|" in line:
            # New format: DATE | SOURCE | ID
            parts = line.split("|")
            if len(parts) >= 3:
                ids.append(parts[2].strip())
        else:
            # Old format: just ID (backward compatibility)
            ids.append(line.strip())
    return ids

def save_processed_id(journey_code, source="UNKNOWN"):
    """Save processed ride with date, source and ID. Format: DATE | SOURCE | ID"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(MEMORY_FILE, "a") as f:
        f.write(f"{timestamp} | {source} | {journey_code}\n")

def clean_memory():
    """Keep only last 5000 entries and remove entries older than 90 days"""
    try:
        if not os.path.exists(MEMORY_FILE): return
        with open(MEMORY_FILE, "r") as f:
            lines = f.read().splitlines()
        
        # Filter out entries older than 90 days
        cutoff_date = datetime.now() - timedelta(days=90)
        valid_lines = []
        
        for line in lines:
            if "|" in line:
                try:
                    date_str = line.split("|")[0].strip()
                    line_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    if line_date >= cutoff_date:
                        valid_lines.append(line)
                except:
                    valid_lines.append(line)  # Keep if can't parse
            else:
                valid_lines.append(line)  # Keep old format lines
        
        # Also limit to last 5000 entries
        if len(valid_lines) > 5000:
            valid_lines = valid_lines[-5000:]
        
        with open(MEMORY_FILE, "w") as f:
            f.write("\n".join(valid_lines) + "\n")
            
        print(f"   🧹 Memory cleaned: {len(lines)} → {len(valid_lines)} entries")
    except Exception as e:
        print(f"   ⚠️ Memory cleanup error: {e}")

def round_time_to_nearest_5(dt_obj):
    total_minutes = dt_obj.hour * 60 + dt_obj.minute
    rounded_minutes = 5 * round(total_minutes / 5)
    new_hour = (rounded_minutes // 60) % 24
    new_min = rounded_minutes % 60
    return dt_obj.replace(hour=new_hour, minute=new_min, second=0)

def check_logout_screen(driver):
    try:
        buttons = driver.find_elements(By.XPATH, "//span[contains(@class, 'button-title') and contains(text(), 'Click here')]")
        if len(buttons) > 0 and buttons[0].is_displayed():
            print("   ⚠️ Found 'Logged Out' screen. Clicking 'Click here'...")
            buttons[0].click()
            time.sleep(5)
            return True
    except: pass
    return False

def get_koi_api_token(driver):
    """
    Scans performance logs to find the Authorization token for api.koiride.com.
    Updated to accept raw tokens (without 'Bearer' prefix) since that is what the browser sends.
    """
    print("   -> 🕵️ Sniffing API Token...")
    try:
        # Force a small wait to ensure logs are captured
        time.sleep(2)
        logs = driver.get_log("performance")
        
        for entry in logs:
            try:
                message = json.loads(entry["message"])["message"]
                if "Network.requestWillBeSent" in message["method"]:
                    params = message["params"]
                    request_url = params["request"]["url"]
                    
                    # We only care about requests to the Koi API
                    if "api.koiride.com" in request_url:
                        headers = params["request"]["headers"]
                        
                        # Case-insensitive lookup for Authorization header
                        token = headers.get("Authorization") or headers.get("authorization")
                        
                        # Logic: If we find a long string (JWT), accept it, even if it lacks "Bearer"
                        if token and len(token) > 20:
                            # print(f"      ✅ Token found! (Length: {len(token)})") # Debug print
                            return token
            except: continue
    except Exception as e:
        print(f"   ⚠️ Error finding Koi Token: {e}")
        
    return None


def get_tz_api_token(driver):
    logs = driver.get_log("performance")
    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]
            if "Network.requestWillBeSent" in message["method"]:
                params = message["params"]
                request_url = params["request"]["url"]
                if "graphql" in request_url or "api" in request_url:
                    headers = params["request"]["headers"]
                    token = headers.get("Authorization") or headers.get("authorization")
                    if token and "Bearer" in token:
                        return token
        except: continue
    return None

def force_fill(driver, element_name, value):
    try:
        element = driver.find_element(By.NAME, element_name)
        driver.execute_script("arguments[0].value = arguments[1];", element, value)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", element)
        return True
    except: return False

# ==========================================
# 3. SOURCE A: TRANSFERZ (Max 50)
# ==========================================

def fetch_transferz_rides(driver, processed_list):
    print("\n--- STARTING SOURCE A: TRANSFERZ ---")
    driver.get(TZ_LOGIN_URL)
    time.sleep(5)
    check_logout_screen(driver)

    if "login" in driver.current_url:
        try:
            email_field = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "email")))
            email_field.clear()
            email_field.send_keys(TZ_EMAIL)
            driver.find_element(By.NAME, "password").send_keys(TZ_PASS)
            driver.find_element(By.NAME, "password").send_keys(Keys.RETURN)
            time.sleep(8)
        except Exception as e:
            print(f"   ⚠️ Transferz Login skipped or failed: {e}")

    driver.get(TZ_JOURNEYS_URL)
    time.sleep(6)
    access_token = get_tz_api_token(driver)
    
    if not access_token:
        print("   ❌ No API Token found for Transferz. Skipping.")
        return []

    rides_found = []
    now = datetime.now()
    future = now + timedelta(days=90)
    date_start = now.strftime("%Y-%m-%dT00:00:00")
    date_end = future.strftime("%Y-%m-%dT23:59:59")
    
    for page in range(0, 1): 
        print(f"   -> Scanning Transferz (Top 50 upcoming)...")
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
            }
          }
        }`;
        const variables = {
            "params": {
                "assignedTransferCompanyId": [%s],
                "excludedStatuses": ["NOT_PAID"],
                "includedStatuses": ["PLANNED", "CONFIRMED", "DRIVER_ARRIVED", "DRIVER_UNDERWAY", "JOURNEY_IN_PROGRESS", "COMPLETED"],
                "page": %d, "size": 50, "sort": ["pickup;asc"],
                "pickupDateAfter": "%s", "pickupDateBefore": "%s"
            }, "skip": false
        };
        var callback = arguments[arguments.length - 1];
        fetch('https://rides.transferz.com/api/graphql', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': '%s' },
            body: JSON.stringify({ query: query, variables: variables })
        }).then(r => r.json()).then(d => callback(d)).catch(e => callback({"error": e.toString()}));
        """ % (TZ_COMPANY_ID, page, date_start, date_end, access_token)

        response = driver.execute_async_script(js_script)
        if not response or 'data' not in response: break
        
        journeys_data = response.get('data', {}).get('journeys', {})
        if not journeys_data: break
        results = journeys_data.get('results', [])

        for r in results:
            r_id = r['journeyCode']
            
            # --- FIX: CHECK BOTH PROCESSED LIST AND CURRENT BATCH FOR DUPLICATES ---
            if r_id not in processed_list and not any(x['id'] == r_id for x in rides_found):
                t_info = r.get('travellerInfo') or {}
                j_info = r.get('journeyExecutionInfo') or {}
                origin = r.get('originLocation') or {}
                dest = r.get('destinationLocation') or {}
                driver_info = r.get('driver') or {}
                
                pickup_addr = (origin.get('address') or {}).get('originalAddress') or (origin.get('address') or {}).get('formattedAddress', 'Unknown')
                dropoff_addr = (dest.get('address') or {}).get('originalAddress') or (dest.get('address') or {}).get('formattedAddress', 'Unknown')

                raw_name = f"{t_info.get('firstName','')} {t_info.get('lastName','')}"
                clean_name = raw_name.strip().title()

                normalized_ride = {
                    "source": "TRANSFERZ",
                    "id": r_id,
                    "pickup_dt_raw": j_info.get('pickupDate'),
                    "name": clean_name,
                    "phone": t_info.get('phone', ''),
                    "pax": t_info.get('passengerCount', 1),
                    "luggage": t_info.get('luggageCount', 0),
                    "flight": t_info.get('flightNumber', ''),
                    "pickup_addr": pickup_addr,
                    "dropoff_addr": dropoff_addr,
                    "vehicle_raw": j_info.get('vehicleCategory', 'Standard'),
                    "driver_note": f"{r_id} | Driver: {driver_info.get('name','No Driver')} | {t_info.get('driverComments','')}",
                    "inbound_hint": r.get('inbound', True)
                }
                rides_found.append(normalized_ride)
                
    print(f"   -> Found {len(rides_found)} new rides from Transferz.")
    return rides_found

# ==========================================
# 4. SOURCE B: KOI RIDE (Date Loop)
# ==========================================

def fetch_koi_rides(driver, wait, processed_list):
    print("\n--- STARTING SOURCE B: KOI RIDE (API MODE) ---")
    driver.get(KOI_LOGIN_URL)
    time.sleep(5)

    # --- 1. LOGIN CHECK ---
    if "auth" in driver.current_url:
        try:
            print("   -> 🔑 Logging in...")
            user_in = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']")))
            user_in.clear()
            user_in.send_keys(KOI_USER)
            pass_in = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            pass_in.clear()
            pass_in.send_keys(KOI_PASS)
            pass_in.send_keys(Keys.RETURN)
            time.sleep(8)
        except Exception as e:
            print(f"   ⚠️ Koi Login failed: {e}")

    # --- 2. CAPTURE TOKEN ---
    driver.refresh()
    time.sleep(5)
    
    access_token = get_koi_api_token(driver)
    if not access_token:
        print("   ❌ No API Token found for Koi Ride. Skipping.")
        return []

    # --- 3. CALCULATE TARGET DATES ---
    today = datetime.now().date()
    target_date_1 = today + timedelta(days=1)  # Tomorrow
    target_date_2 = today + timedelta(days=2)  # Day After
    
    print(f"   -> 📅 Target dates: {target_date_1} and {target_date_2}")

    # --- 4. FETCH DATA WITH PAGINATION (API ignores date filters) ---
    # The KOI API doesn't support date filtering, so we paginate through
    # all rides sorted by ID (newest first) and filter locally
    all_rides = []
    target_dates_str = [str(target_date_1), str(target_date_2)]
    
    print(f"   -> 🔍 Scanning pages for rides on {target_dates_str}...")
    
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
    
    # Paginate through up to 20 pages (2000 rides) looking for target dates
    # Try sorting by pickupDate descending to get future rides first
    for page in range(1, 21):
        # Try different sort combinations
        api_url = f"https://api.koiride.com/api/v3/reservation/all?sortBy=pickupDate&desc=true&pageNumber={page}&pageSize=100&driverAssigned=true"
        
        try:
            response = driver.execute_async_script(js_script, api_url, access_token)
            results = response.get('rows', [])
            
            if not results:
                print(f"      Page {page}: Empty - stopping pagination")
                break
            
            # Check dates in this page
            page_matches = 0
            dates_in_page = set()
            
            for r in results:
                ride_date = r.get('pickupDate', '')
                dates_in_page.add(ride_date)
                
                if ride_date in target_dates_str:
                    all_rides.append(r)
                    page_matches += 1
            
            # Debug: Show page summary with IDs
            min_date = min(dates_in_page) if dates_in_page else "?"
            max_date = max(dates_in_page) if dates_in_page else "?"
            first_id = results[0].get('reservationId', '?') if results else '?'
            last_id = results[-1].get('reservationId', '?') if results else '?'
            print(f"      Page {page}: IDs {first_id}-{last_id}, dates {min_date} to {max_date}, matches: {page_matches}")
            
            # If we found matches, keep going to get all of them
            # If dates are way older than targets, stop
            if max_date < "2025-12-01" and page > 3:
                print(f"      -> All dates are from before December. Stopping.")
                break
                
        except Exception as e:
            print(f"      Page {page} ERROR: {e}")
            break
    
    print(f"   -> Total rides matching target dates: {len(all_rides)}")

    # --- 5. PARSE RIDES ---
    rides_found = []
    
    for r in all_rides:
        try:
            r_id = str(r.get('reservationId'))
            if r_id in processed_list: 
                continue
            
            # Skip duplicates in current batch
            if any(ride['id'] == r_id for ride in rides_found):
                continue

            date_str = r.get('pickupDate')
            time_str = r.get('pickupTime')
            full_dt_str = f"{date_str}T{time_str}:00"

            # -- DETAILS --
            fname = r.get('customerFirstName', '')
            lname = r.get('customerLastName', '')
            name = f"{fname} {lname}".replace(".", "").strip()

            prefix = r.get('passengerPhonePrefix', '')
            num = r.get('customerPhone', '')
            phone = f"+{prefix}{num}" if prefix and num else num

            car_info = r.get('carType', {})
            car_name = car_info.get('carTypeName', 'Standard')
            model = r.get('model', '').lower()
            
            vehicle = "Standard"
            if "People carrier" in car_name or "Van" in car_name or "vito" in model:
                vehicle = "Minivan"
            elif "Business" in car_name or "Executive" in car_name:
                vehicle = "Business"

            flight = r.get('flightNumber', '')
            pickup = r.get('pickupAddress', '')
            dropoff = r.get('dropoffAddress', '')
            comment = r.get('comment', '')
            internal = r.get('internalComment', '')
            full_note = f"KOI-{r_id} | {comment} {internal}".strip()

            normalized_ride = {
                "source": "KOI",
                "id": r_id,
                "pickup_dt_raw": full_dt_str,
                "name": name,
                "phone": phone,
                "pax": r.get('numberOfPassengers', 1),
                "luggage": car_info.get('maxLuggage', 0),
                "flight": flight,
                "pickup_addr": pickup,
                "dropoff_addr": dropoff,
                "vehicle_raw": vehicle,
                "driver_note": full_note,
                "inbound_hint": True 
            }
            rides_found.append(normalized_ride)
            print(f"      + MATCH: {r_id} | {date_str} {time_str} | {name}")

        except Exception as e:
            print(f"      ⚠️ Error parsing ride: {e}")
            continue

    print(f"   -> Found {len(rides_found)} valid rides for target dates.")
    return rides_found

# ==========================================
# 5. SOURCE C: GET-E (SMART TABS & SCRAPE)
# ==========================================

def ensure_only_tab_active(driver, target_tab_text):
    """
    Enforces that ONLY the target_tab is active by checking RGB background colors.
    If a tab is active (dark blue) but shouldn't be, it clicks it to toggle OFF.
    If the target tab is inactive (white/light), it clicks it to toggle ON.
    """
    all_tabs = ["To confirm", "Confirmed", "To close", "Completed", "Cancelled"]
    print(f"   -> ⚙️ Enforcing Single Tab: '{target_tab_text}'")
    
    for tab_name in all_tabs:
        try:
            # Find the button containing this text
            btn = driver.find_element(By.XPATH, f"//button[contains(., '{tab_name}')]")
            
            # Check color state: Active = Dark Blue (~ rgb(7, 54, 144))
            bg_color = driver.execute_script("return window.getComputedStyle(arguments[0]).backgroundColor;", btn)
            
            is_active = False
            if "rgb" in bg_color:
                rgb = [int(x) for x in re.findall(r'\d+', bg_color)]
                # Logic: If Red component < 100 and Blue > 100, it's the dark blue active color
                if len(rgb) >= 3 and rgb[0] < 100 and rgb[2] > 100:
                    is_active = True
            
            if tab_name == target_tab_text:
                if not is_active: # Turn ON
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
            else:
                if is_active: # Turn OFF
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
                    
        except Exception as e:
            pass # Tab might not be visible, skip
    
    time.sleep(3) # Wait for grid reload


    """Generic function to scrape whatever rides are currently visible in the grid."""
    rides_found = []
    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, ".MuiDataGrid-row")
        row_indices = []
        
        # 1. Identify new rows
        for row in rows:
            try:
                # Quick check text to avoid entering known rides
                if any(x in processed_list for x in row.text.split()): continue
                row_indices.append(row.get_attribute("data-rowindex"))
            except: continue
        
        if not row_indices:
            print("      -> No new rides found on this page.")
        else:
            print(f"      -> Processing {len(row_indices)} rides...")

        # 2. Process rows
        for r_idx in row_indices:
            try:
                target_row = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, f".MuiDataGrid-row[data-rowindex='{r_idx}']"))
                )
                driver.execute_script("arguments[0].click();", target_row)
                time.sleep(3) 

                page_text = driver.find_element(By.TAG_NAME, "body").text
                
                # --- ID Parsing ---
                r_id = "Unknown"
                if "Ride number" in page_text:
                    try:
                        lines = page_text.splitlines()
                        for i, line in enumerate(lines):
                            if "Ride number" in line:
                                candidate = lines[i] + " " + (lines[i+1] if i+1 < len(lines) else "")
                                match = re.search(r'(\d{3}[-\s]?\d{3}[-\s]?\d{4})', candidate)
                                if match:
                                    r_id = match.group(1).replace("-","").replace(" ","")
                                    break
                    except: pass
                
                if r_id == "Unknown" or len(r_id) < 5:
                    url_part = driver.current_url.split("/")[-1]
                    if url_part.replace("-","").isdigit():
                        r_id = url_part.replace("-","")

                if r_id in processed_list:
                    driver.back()
                    time.sleep(2)
                    continue

                # --- Detail Parsing ---
                full_dt_str = datetime.now().strftime("%Y-%m-%dT12:00:00")
                date_matches = re.findall(r'(\w{3}, \d{1,2} \w{3} \d{4} at \d{1,2}:\d{2} [AP]M)', page_text)
                if date_matches:
                    try:
                        clean_dt = date_matches[0].replace("at ", "")
                        dt_obj = datetime.strptime(clean_dt, "%a, %d %b %Y %H:%M %p")
                        full_dt_str = dt_obj.strftime("%Y-%m-%dT%H:%M:00")
                    except: pass

                # --- NAME PARSING (FIXED TYPO & LOGIC) ---
                name = "Unknown"
                try:
                    # 1. Try finding the bold label "Name:" and getting its parent text
                    # Example HTML: <p><strong>Name: </strong> John Doe</p>
                    name_el = driver.find_element(By.XPATH, "//*[contains(text(), 'Name:')]")
                    # Get the full text of the parent element (e.g. "Name: John Doe")
                    full_text = name_el.find_element(By.XPATH, "./..").text
                    name = full_text.replace("Name:", "").strip()
                except:
                    # 2. Fallback: Try regex again if XPath fails
                    name_match = re.search(r'Name:\s*(.+)', page_text)
                    if name_match: name = name_match.group(1).strip()

                if len(name) < 2: name = "Unknown"
                
                pickup = "See Notes"
                dropoff = "See Notes"
                try:
                    addr_block = re.search(r'From:\s*(.+?)\nTo:\s*(.+)', page_text)
                    if addr_block:
                        pickup = addr_block.group(1).strip()
                        dropoff = addr_block.group(2).strip()
                except: pass

                vehicle = "Standard"
                if "Business" in page_text: vehicle = "Business"
                elif "Van" in page_text: vehicle = "Minivan"

                normalized_ride = {
                    "source": "GETE",
                    "id": r_id,
                    "pickup_dt_raw": full_dt_str,
                    "name": name,
                    "phone": "",
                    "pax": "1",
                    "pickup_addr": pickup,
                    "dropoff_addr": dropoff,
                    "vehicle_raw": vehicle,
                    "driver_note": f"GETE-{r_id}",
                    "inbound_hint": True,
                    "flight": ""
                }
                rides_found.append(normalized_ride)
                print(f"         + Scraped: {r_id} ({name})")
                
                driver.back()
                time.sleep(3)

            except Exception as e:
                print(f"         ⚠️ Error scraping row {r_idx}: {e}")
                driver.get(GETE_LOGIN_URL) 
                time.sleep(3)
                return rides_found # Stop scraping this tab if critical error

        # 3. Pagination
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, "button[title='Go to next page']")
            if not next_btn.is_enabled() or "disabled" in next_btn.get_attribute("class"):
                break
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(5)
        except: 
            break
            
    return rides_found


    """Generic function to scrape whatever rides are currently visible in the grid."""
    rides_found = []
    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, ".MuiDataGrid-row")
        row_indices = []
        
        # 1. Identify new rows
        for row in rows:
            try:
                if any(x in processed_list for x in row.text.split()): continue
                row_indices.append(row.get_attribute("data-rowindex"))
            except: continue
        
        if not row_indices:
            print("      -> No new rides found on this page.")
        else:
            print(f"      -> Processing {len(row_indices)} rides...")

        # 2. Process rows
        for r_idx in row_indices:
            try:
                target_row = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, f".MuiDataGrid-row[data-rowindex='{r_idx}']"))
                )
                driver.execute_script("arguments[0].click();", target_row)
                time.sleep(3) 

                page_text = driver.find_element(By.TAG_NAME, "body").text
                
                # --- ID Parsing ---
                r_id = "Unknown"
                # Strategy A: From URL (Most reliable)
                url_part = driver.current_url.split("/")[-1]
                if url_part.replace("-","").isdigit() and len(url_part) > 5:
                    r_id = url_part.replace("-","")
                else:
                    # Strategy B: From Text
                    match = re.search(r'Ride number[:\s]*([\d-]+)', page_text)
                    if match: r_id = match.group(1).replace("-", "")

                if r_id in processed_list:
                    driver.back()
                    time.sleep(2)
                    continue

                # --- Detail Parsing ---
                full_dt_str = datetime.now().strftime("%Y-%m-%dT12:00:00")
                # Look for "Fri, 12 Dec 2025 at 09:00 AM" pattern [cite: 24]
                date_matches = re.findall(r'(\w{3}, \d{1,2} \w{3} \d{4} at \d{1,2}:\d{2} [AP]M)', page_text)
                if date_matches:
                    try:
                        clean_dt = date_matches[0].replace("at ", "")
                        dt_obj = datetime.strptime(clean_dt, "%a, %d %b %Y %H:%M %p")
                        full_dt_str = dt_obj.strftime("%Y-%m-%dT%H:%M:00")
                    except: pass

                # --- NAME PARSING (ICON STRATEGY) ---
                name = "Unknown"
                try:
                    # 1. Find the Hail Icon (Person Waving) [cite: 30]
                    # The name is ALWAYS in the span immediately following this icon
                    name_ele = driver.find_element(By.XPATH, "//*[local-name()='svg' and @data-testid='HailOutlinedIcon']/following-sibling::span")
                    raw_text = name_ele.text # Returns "Name: John Doe"
                    name = raw_text.replace("Name:", "").replace("Passenger:", "").strip()
                except:
                    # 2. Fallback: Find the strong tag "Name:" and get parent text [cite: 31]
                    try:
                        name_ele = driver.find_element(By.XPATH, "//strong[contains(text(), 'Name:')]")
                        parent_text = name_ele.find_element(By.XPATH, "./..").text
                        name = parent_text.replace("Name:", "").strip()
                    except:
                        pass

                if len(name) < 2: name = "Unknown"

                # --- ADDRESS PARSING (ICON STRATEGY) ---
                pickup = "See Notes"
                dropoff = "See Notes"
                try:
                    # Addresses are usually below the route/map section or listed with "From:" / "To:"
                    # Strategy: Look for "From:" bold text
                    from_ele = driver.find_element(By.XPATH, "//strong[contains(text(), 'From:')]")
                    pickup = from_ele.find_element(By.XPATH, "./..").text.replace("From:", "").strip()
                    
                    to_ele = driver.find_element(By.XPATH, "//strong[contains(text(), 'To:')]")
                    dropoff = to_ele.find_element(By.XPATH, "./..").text.replace("To:", "").strip()
                except: pass

                vehicle = "Standard"
                if "Business" in page_text: vehicle = "Business"
                elif "Van" in page_text: vehicle = "Minivan"

                normalized_ride = {
                    "source": "GETE",
                    "id": r_id,
                    "pickup_dt_raw": full_dt_str,
                    "name": name,
                    "phone": "",
                    "pax": "1",
                    "pickup_addr": pickup,
                    "dropoff_addr": dropoff,
                    "vehicle_raw": vehicle,
                    "driver_note": f"GETE-{r_id}",
                    "inbound_hint": True,
                    "flight": ""
                }
                rides_found.append(normalized_ride)
                print(f"         + Scraped: {r_id} ({name})")
                
                driver.back()
                time.sleep(3)

            except Exception as e:
                print(f"         ⚠️ Error scraping row {r_idx}: {e}")
                driver.get(GETE_LOGIN_URL) 
                time.sleep(3)
                return rides_found 

        # 3. Pagination
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, "button[title='Go to next page']")
            if not next_btn.is_enabled() or "disabled" in next_btn.get_attribute("class"):
                break
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(5)
        except: 
            break
            
    return rides_found

def scrape_current_view(driver, processed_list):
    """Generic function to scrape whatever rides are currently visible in the grid."""
    rides_found = []
    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, ".MuiDataGrid-row")
        row_indices = []
        
        # 1. Identify new rows
        for row in rows:
            try:
                if any(x in processed_list for x in row.text.split()): continue
                row_indices.append(row.get_attribute("data-rowindex"))
            except: continue
        
        if not row_indices:
            print("      -> No new rides found on this page.")
        else:
            print(f"      -> Processing {len(row_indices)} rides...")

        # 2. Process rows
        for r_idx in row_indices:
            try:
                target_row = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, f".MuiDataGrid-row[data-rowindex='{r_idx}']"))
                )
                driver.execute_script("arguments[0].click();", target_row)
                time.sleep(3) 

                page_text = driver.find_element(By.TAG_NAME, "body").text
                
                # --- ID Parsing ---
                r_id = "Unknown"
                # Strategy A: From URL (Most reliable)
                url_part = driver.current_url.split("/")[-1]
                if url_part.replace("-","").isdigit() and len(url_part) > 5:
                    r_id = url_part.replace("-","")
                else:
                    # Strategy B: From Text
                    match = re.search(r'Ride number[:\s]*([\d-]+)', page_text)
                    if match: r_id = match.group(1).replace("-", "")

                if r_id in processed_list:
                    driver.back()
                    time.sleep(2)
                    continue

                # --- Detail Parsing ---
                full_dt_str = datetime.now().strftime("%Y-%m-%dT12:00:00")
                date_matches = re.findall(r'(\w{3}, \d{1,2} \w{3} \d{4} at \d{1,2}:\d{2} [AP]M)', page_text)
                if date_matches:
                    try:
                        clean_dt = date_matches[0].replace("at ", "")
                        dt_obj = datetime.strptime(clean_dt, "%a, %d %b %Y %H:%M %p")
                        full_dt_str = dt_obj.strftime("%Y-%m-%dT%H:%M:00")
                    except: pass

                # --- NAME PARSING (ICON STRATEGY - UPDATED) ---
                name = "Unknown"
                try:
                    # Look for ANY icon with 'Hail' in the ID (HailIcon or HailOutlinedIcon)
                    # The name is always in the span immediately following it.
                    icon_ele = driver.find_element(By.XPATH, "//*[contains(@data-testid, 'Hail')]/following-sibling::span")
                    name = icon_ele.text.strip()
                except:
                    # Backup: If visual scraping fails, try regex on the text block under "Passenger information"
                    try:
                        if "Passenger information" in page_text:
                            # Split text by lines, look for the line that isn't a label
                            parts = page_text.split("Passenger information")[-1].strip().split("\n")
                            if parts: name = parts[0].strip()
                    except: pass

                if len(name) < 2: name = "Unknown"

                # --- ADDRESS PARSING ---
                pickup = "See Notes"
                dropoff = "See Notes"
                try:
                    # Address Strategy: Look for RoomIcon (Pickup) and RoomIcon (Dropoff)
                    # Or fallback to Regex "From:" / "To:" if the text exists
                    if "From:" in page_text and "To:" in page_text:
                        addr_block = re.search(r'From:\s*(.+?)\nTo:\s*(.+)', page_text)
                        if addr_block:
                            pickup = addr_block.group(1).strip()
                            dropoff = addr_block.group(2).strip()
                    else:
                        # Fallback for detailed view where "From:" might not be written
                        # We grab large text blocks that look like addresses
                        lines = [l for l in page_text.split('\n') if len(l) > 10]
                        # This is a guess, but often sufficient for now
                        pickup = lines[1] if len(lines) > 1 else "Unknown" 
                        dropoff = lines[2] if len(lines) > 2 else "Unknown"
                except: pass

                vehicle = "Standard"
                if "Business" in page_text: vehicle = "Business"
                elif "Van" in page_text: vehicle = "Minivan"

                normalized_ride = {
                    "source": "GETE",
                    "id": r_id,
                    "pickup_dt_raw": full_dt_str,
                    "name": name,
                    "phone": "",
                    "pax": "1",
                    "pickup_addr": pickup,
                    "dropoff_addr": dropoff,
                    "vehicle_raw": vehicle,
                    "driver_note": f"GETE-{r_id}",
                    "inbound_hint": True,
                    "flight": ""
                }
                rides_found.append(normalized_ride)
                print(f"         + Scraped: {r_id} ({name})")
                
                driver.back()
                time.sleep(3)

            except Exception as e:
                print(f"         ⚠️ Error scraping row {r_idx}: {e}")
                driver.get(GETE_LOGIN_URL) 
                time.sleep(3)
                return rides_found 

        # 3. Pagination
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, "button[title='Go to next page']")
            if not next_btn.is_enabled() or "disabled" in next_btn.get_attribute("class"):
                break
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(5)
        except: 
            break
            
    return rides_found

def fetch_gete_rides(driver, processed_list):
    print("\n--- STARTING SOURCE C: GET-E ---")
    driver.get(GETE_LOGIN_URL)
    time.sleep(3)
    
    # --- 1. LOGIN ---
    print("   -> Checking Login Status...")
    try:
        try:
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']")))
            is_login_page = True
        except:
            is_login_page = False

        if is_login_page:
            print("   -> Login Required. Entering credentials...")
            user_in = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='email']")))
            user_in.clear()
            user_in.send_keys(GETE_EMAIL)
            
            pass_in = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            pass_in.clear()
            pass_in.send_keys(GETE_PASS)
            
            try:
                login_btn = driver.find_element(By.XPATH, "//button[contains(., 'Sign in') or contains(., 'Log in')]")
                login_btn.click()
            except:
                pass_in.send_keys(Keys.RETURN)
            
            WebDriverWait(driver, 20).until(EC.url_contains("rides"))
            time.sleep(5)
            print("   -> Login Successful.")
    except Exception as e:
        print(f"   ⚠️ Login Check Error: {e}")

    # --- 2. RESET FILTERS ---
    try:
        reset_btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Reset filters')]")))
        driver.execute_script("arguments[0].click();", reset_btn)
        time.sleep(3)
        print("   -> 🔄 Filters reset.")
    except: pass

    all_rides = []

    # --- 3. PROCESS 'TO CONFIRM' TAB ---
    try:
        ensure_only_tab_active(driver, "To confirm")
        print("   -> 🟢 Scraping 'To confirm' tab...")
        rides = scrape_current_view(driver, processed_list)
        all_rides.extend(rides)
    except Exception as e:
        print(f"   ⚠️ Error processing 'To confirm': {e}")

    # --- 4. PROCESS 'CONFIRMED' TAB ---
    try:
        ensure_only_tab_active(driver, "Confirmed")
        print("   -> 🔵 Scraping 'Confirmed' tab...")
        rides = scrape_current_view(driver, processed_list)
        all_rides.extend(rides)
    except Exception as e:
        print(f"   ⚠️ Error processing 'Confirmed': {e}")

    print(f"   -> Found {len(all_rides)} new rides from Get-e.")
    return all_rides

# ==========================================
# 6. DESTINATION: ACCOMMTRA
# ==========================================

def process_single_order(driver, ride, wait):
    try:
        raw_date = ride['pickup_dt_raw']
        try:
            if "T" in raw_date:
                dt_obj = datetime.strptime(raw_date, "%Y-%m-%dT%H:%M:%S")
            else:
                try: 
                    dt_obj = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
                except: 
                    dt_obj = datetime.strptime(raw_date, "%Y-%m-%d %H:%M") 
        except:
            print(f"   ❌ Date format error: {raw_date}")
            return False

        # --- ADDRESS LOGIC (INBOUND/OUTBOUND) ---
        pickup = ride['pickup_addr']
        dropoff = ride['dropoff_addr']
        
        is_inbound = True
        if any(x in dropoff.upper() for x in ["AIRPORT", "VACLA", "PRG"]):
            is_inbound = False 
        if any(x in pickup.upper() for x in ["AIRPORT", "VACLA", "PRG"]):
            is_inbound = True 

        # --- TIME ADJUSTMENT (Transferz + Airport Pickup) ---
        if ride['source'] == "TRANSFERZ" and is_inbound:
            print("      ⏳ Transferz Airport Pickup: Subtracting 15 mins...")
            dt_obj = dt_obj - timedelta(minutes=15)

        dt_obj = round_time_to_nearest_5(dt_obj)
        date_formatted = dt_obj.strftime("%d.%m.%Y")
        time_formatted = dt_obj.strftime("%H:%M")
        url_date = dt_obj.strftime("%Y-%m-%d")

        # --- CONTRACTOR SWITCH (UPDATED) ---
        current_contractor = CONTRACTOR_ID_TZ 
        if ride['source'] == "KOI":
            if CONTRACTOR_ID_KOI != "FIX_ME": current_contractor = CONTRACTOR_ID_KOI
            else: current_contractor = "TEXT_MODE"
        elif ride['source'] == "GETE":
            current_contractor = "TEXT_MODE_GETE" # Always select by text "GE CZ"

        # --- VEHICLE MAPPING ---
        veh_raw = ride['vehicle_raw'].upper()
        veh_val = "1" 
        template_id = "205" if is_inbound else "207"

        if "BUSINESS" in veh_raw:
            veh_val = "18"
            template_id = "205" if is_inbound else "207"
        elif "MINIVAN" in veh_raw or "PEOPLE" in veh_raw or "MINI" in veh_raw or "VAN" in veh_raw:
            veh_val = "2" 
            template_id = "206" if is_inbound else "208"
        else:
            veh_val = "1"
            template_id = "205" if is_inbound else "207"

        print(f"   -> Filling: {ride['name']} ({date_formatted} {time_formatted}) [{ride['source']}]")
        driver.get(DEST_FORM_URL_BASE + url_date)
        
        try:
            wait.until(EC.presence_of_element_located((By.NAME, "firstname")))
        except:
            try:
                driver.find_element(By.ID, "tippw-folink").click()
                wait.until(EC.presence_of_element_located((By.NAME, "firstname")))
            except:
                print("      ❌ Form load failed.")
                return False
        
        time.sleep(1)

        try:
            Select(driver.find_element(By.NAME, "OrderTemplate")).select_by_value(template_id)
            time.sleep(1)
            
            contractor_dropdown = Select(driver.find_element(By.NAME, "Contractor"))
            if current_contractor == "TEXT_MODE":
                try: contractor_dropdown.select_by_visible_text(CONTRACTOR_TEXT_KOI)
                except: pass
            elif current_contractor == "TEXT_MODE_GETE":
                try: 
                    contractor_dropdown.select_by_visible_text(CONTRACTOR_TEXT_GETE)
                except: 
                    print(f"      ⚠️ Failed to select '{CONTRACTOR_TEXT_GETE}'.")
            else:
                contractor_dropdown.select_by_value(current_contractor)
            time.sleep(1)
        except: pass

        force_fill(driver, "firstname", ride['name'])
        force_fill(driver, "lastname", "")
        force_fill(driver, "phone", ride['phone'])
        force_fill(driver, "firstway__persons", str(ride['pax']))
        
        try: Select(driver.find_element(By.NAME, "firstway__vehicle_category_id")).select_by_value(veh_val)
        except: pass

        force_fill(driver, "firstway__date", date_formatted)
        force_fill(driver, "firstway__time", time_formatted)

        try: driver.find_element(By.TAG_NAME, "body").click()
        except: pass

        force_fill(driver, "firstway__from", pickup)
        force_fill(driver, "firstway__to", dropoff)

        flight_val = ride['flight'] if is_inbound else ""
        force_fill(driver, "firstway__flight", flight_val)

        force_fill(driver, "firstway__price_1", DEFAULT_PRICE)
        force_fill(driver, "firstway__price_2", "")

        try:
            driver.execute_script(f"document.getElementById('firstway__driver_note').value = '{ride['driver_note']}';")
        except: pass

        try:
            driver.find_element(By.ID, "sendButton").click()
            print("      ✅ Order Saved.")
            time.sleep(3)
            return True
        except:
            print("      ❌ Save Click Failed.")
            return False

    except Exception as e:
        print(f"      ❌ Form Error: {e}")
        return False

# ==========================================
# 7. MAIN EXECUTION
# ==========================================

def show_menu():
    """Display quick selection menu and return chosen sources"""
    print("\n" + "=" * 50)
    print("       🚗 RideSyncBot V2 - Source Selection")
    print("=" * 50)
    print("\n  Quick Select:")
    print("    [1] Transferz only")
    print("    [2] KOI Ride only")
    print("    [3] Get-e only")
    print("    [4] Transferz + KOI")
    print("    [5] Transferz + Get-e")
    print("    [6] KOI + Get-e")
    print("    [7] ALL (Transferz + KOI + Get-e)")
    print("    [0] Exit")
    print("\n" + "-" * 50)
    
    while True:
        try:
            choice = input("  Enter choice [0-7]: ").strip()
            if choice == "0":
                return None, None, None  # Exit signal
            elif choice == "1":
                return True, False, False
            elif choice == "2":
                return False, True, False
            elif choice == "3":
                return False, False, True
            elif choice == "4":
                return True, True, False
            elif choice == "5":
                return True, False, True
            elif choice == "6":
                return False, True, True
            elif choice == "7":
                return True, True, True
            else:
                print("  ❌ Invalid choice. Enter 0-7.")
        except KeyboardInterrupt:
            return None, None, None

def run_bot(enable_tz=None, enable_koi=None, enable_gete=None):
    """Main bot execution with optional source overrides"""
    
    # Use passed values or fall back to global config
    use_transferz = enable_tz if enable_tz is not None else ENABLE_TRANSFERZ
    use_koi = enable_koi if enable_koi is not None else ENABLE_KOI
    use_gete = enable_gete if enable_gete is not None else ENABLE_GETE
    
    print(f"\n🚀 Starting RideSyncBot V2...")
    print(f"   📊 Active Sources:")
    print(f"      • Transferz: {'✅ ON' if use_transferz else '❌ OFF'}")
    print(f"      • KOI Ride:  {'✅ ON' if use_koi else '❌ OFF'}")
    print(f"      • Get-e:     {'✅ ON' if use_gete else '❌ OFF'}")
    print("")
    
    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    prefs = {"profile.default_content_setting_values.notifications": 2}
    options.add_experimental_option("prefs", prefs)
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)
    
    processed_ids = load_processed_ids()
    all_new_rides = []

    if use_transferz:
        try:
            tz_rides = fetch_transferz_rides(driver, processed_ids)
            all_new_rides.extend(tz_rides)
        except Exception as e:
            print(f"❌ Transferz Critical Error: {e}")
    else:
        print("⏸️ Transferz is DISABLED. Skipping...")

    if use_koi:
        try:
            koi_rides = fetch_koi_rides(driver, wait, processed_ids)
            all_new_rides.extend(koi_rides)
        except Exception as e:
            print(f"❌ Koi Ride Critical Error: {e}")
    else:
        print("⏸️ KOI Ride is DISABLED. Skipping...")

    if use_gete:
        try:
            gete_rides = fetch_gete_rides(driver, processed_ids)
            all_new_rides.extend(gete_rides)
        except Exception as e:
            print(f"❌ Get-e Critical Error: {e}")
    else:
        print("⏸️ Get-e is DISABLED. Skipping...")

    print(f"\n👉 Total New Rides to Process: {len(all_new_rides)}")

    if not all_new_rides:
        print("🏁 No new rides. Exiting.")
        driver.quit()
        return

    print("\n--- STARTING DESTINATION: ACCOMMTRA ---")
    driver.get(DEST_URL_LOGIN)
    time.sleep(5)
    
    if "login" in driver.current_url:
        print("   -> Auto-detecting login fields...")
        user_selectors = [(By.NAME, "username"), (By.NAME, "email"), (By.NAME, "login"), (By.ID, "username"), (By.ID, "email")]
        found_user = False
        for sel in user_selectors:
            try:
                user_box = driver.find_element(*sel)
                user_box.clear()
                user_box.send_keys(DEST_EMAIL)
                found_user = True
                break
            except: continue
        
        if not found_user: print("      ❌ Could not find username field. Please login manually.")

        try:
            pass_box = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            pass_box.clear()
            pass_box.send_keys(DEST_PASS)
            pass_box.send_keys(Keys.RETURN)
            print("      ✅ Password entered.")
            time.sleep(10)
        except:
            print("      ❌ Password field not found. Manual login required.")
            time.sleep(60)
    
    count = 1
    for ride in all_new_rides:
        print(f"--- Processing {count}/{len(all_new_rides)} ---")
        if process_single_order(driver, ride, wait):
            save_processed_id(ride['id'], ride.get('source', 'UNKNOWN'))
        count += 1
    
    clean_memory()
    print("🏁 Done.")
    driver.quit()

if __name__ == "__main__":
    # Show interactive menu
    tz, koi, gete = show_menu()
    
    if tz is None:  # User chose to exit
        print("\n👋 Goodbye!")
    else:
        run_bot(enable_tz=tz, enable_koi=koi, enable_gete=gete)
    
    # Keep console open
    input("\nPress Enter to close...")