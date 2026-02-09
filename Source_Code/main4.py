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
CONTRACTOR_ID_TZ   = "227"     # Transferz ID
CONTRACTOR_ID_KOI  = "269"     # Koi Ride ID
CONTRACTOR_TEXT_GETE = "GE CZ" # Get-e Text (Selected by visible text)

DEST_FORM_URL_BASE = "https://driver.accommtra.com/order-filter/item/date/"

# SETTINGS
DEFAULT_PRICE = "800" 
MEMORY_FILE = "processed_rides.txt"

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def load_processed_ids():
    if not os.path.exists(MEMORY_FILE):
        return []
    with open(MEMORY_FILE, "r") as f:
        return f.read().splitlines()

def save_processed_id(journey_code):
    with open(MEMORY_FILE, "a") as f:
        f.write(f"{journey_code}\n")

def clean_memory():
    try:
        if not os.path.exists(MEMORY_FILE): return
        with open(MEMORY_FILE, "r") as f:
            lines = f.read().splitlines()
        if len(lines) > 5000:
            new_lines = lines[-5000:]
            with open(MEMORY_FILE, "w") as f:
                f.write("\n".join(new_lines) + "\n")
    except: pass

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
            if r['journeyCode'] not in processed_list:
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
                    "id": r['journeyCode'],
                    "pickup_dt_raw": j_info.get('pickupDate'),
                    "name": clean_name,
                    "phone": t_info.get('phone', ''),
                    "pax": t_info.get('passengerCount', 1),
                    "luggage": t_info.get('luggageCount', 0),
                    "flight": t_info.get('flightNumber', ''),
                    "pickup_addr": pickup_addr,
                    "dropoff_addr": dropoff_addr,
                    "vehicle_raw": j_info.get('vehicleCategory', 'Standard'),
                    "driver_note": f"{r['journeyCode']} | Driver: {driver_info.get('name','No Driver')} | {t_info.get('driverComments','')}",
                    "inbound_hint": r.get('inbound', True)
                }
                rides_found.append(normalized_ride)
                
    print(f"   -> Found {len(rides_found)} new rides from Transferz.")
    return rides_found

# ==========================================
# 4. SOURCE B: KOI RIDE (Date Loop)
# ==========================================

def fetch_koi_rides(driver, wait, processed_list):
    print("\n--- STARTING SOURCE B: KOI RIDE ---")
    driver.get(KOI_LOGIN_URL)
    time.sleep(5)
    
    if "auth" in driver.current_url:
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
            print(f"   ⚠️ Koi Login failed: {e}")

    today = datetime.now()
    target_dates = [today + timedelta(days=1), today + timedelta(days=2)]
    all_koi_rides = []

    for target_date in target_dates:
        print(f"   -> 📅 Processing Date: {target_date.strftime('%Y-%m-%d')}...")
        driver.get(KOI_ASSIGNED_URL)
        time.sleep(4)

        try:
            date_input = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//input[contains(@placeholder, 'Select date')]")))
            date_input.click()
            time.sleep(1)

            day_str = str(target_date.day)
            calendar_days = driver.find_elements(By.XPATH, "//div[contains(@class, 'day') or contains(@class, 'cell')]") 
            day_clicked = False
            for cell in calendar_days:
                if cell.text.strip() == day_str:
                    try:
                        cell.click()
                        day_clicked = True
                        break
                    except: pass
            
            if not day_clicked: continue

            try: select_btn = driver.find_element(By.XPATH, "//span[contains(text(), 'Select')] | //button[contains(text(), 'Select')]").click()
            except: pass
            time.sleep(1)

            try: search_btn = driver.find_element(By.XPATH, "//div[contains(@class, 'btn-content') and contains(text(), 'Search')]").click()
            except: driver.execute_script("document.querySelectorAll('.btn-primary').forEach(b => { if(b.innerText.includes('Search')) b.click() })")
            time.sleep(5) 

            page_num = 1
            while True:
                try:
                    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                    if len(rows) == 0 or "No results" in driver.page_source:
                        break

                    print(f"      -> Scraping Page {page_num}...")
                    for row in rows:
                        try:
                            text = row.text
                            if "No results" in text: continue
                            cols = row.find_elements(By.TAG_NAME, "td")
                            r_id = cols[0].text.strip()
                            
                            if r_id in processed_list or any(r['id'] == r_id for r in all_koi_rides): continue
                            
                            raw_dt = cols[2].text.split("\n")
                            date_part = raw_dt[0].strip()
                            time_part = raw_dt[1].strip() if len(raw_dt)>1 else "00:00"
                            full_dt_str = f"{date_part}T{time_part}:00"

                            # FLIGHT FILTER
                            flight_text = cols[3].text.strip()
                            flight_match = re.search(r'([A-Z0-9]{2,}\d+)', flight_text)
                            flight = flight_match.group(1) if flight_match else ""

                            pax_col = cols[5].text 
                            pax_lines = pax_col.split("\n")
                            name = pax_lines[0] if len(pax_lines)>0 else "Unknown"
                            phone = ""
                            pax_count = "1"
                            vehicle = "Standard"

                            for line in pax_lines:
                                clean_line = line.replace(" ", "").replace("-", "")
                                if clean_line.isdigit() and len(clean_line) > 6:
                                    raw_phone = line.strip()
                                    phone = "+" + raw_phone if not raw_phone.startswith("+") else raw_phone
                                
                                if "passengers" in line: pax_count = line.replace("passengers", "").strip()
                                if "Standard" in line: vehicle = "Standard"
                                if "Executive" in line: vehicle = "Business"
                                if "People carrier" in line or "Vito" in line: vehicle = "Minivan"

                            pickup = cols[7].text.replace("\n", " ").strip()
                            dropoff = cols[8].text.replace("\n", " ").strip()
                            comment = cols[10].text if len(cols) > 10 else ""

                            normalized_ride = {
                                "source": "KOI",
                                "id": r_id,
                                "pickup_dt_raw": full_dt_str,
                                "name": name,
                                "phone": phone,
                                "pax": pax_count,
                                "luggage": 0,
                                "flight": flight,
                                "pickup_addr": pickup,
                                "dropoff_addr": dropoff,
                                "vehicle_raw": vehicle,
                                "driver_note": f"KOI-{r_id} | {comment}",
                                "inbound_hint": True 
                            }
                            all_koi_rides.append(normalized_ride)
                        except Exception as e: continue
                    
                    try:
                        next_btns = driver.find_elements(By.XPATH, "//li[not(contains(@class, 'disabled'))]//button[contains(@class, 'page-link') and contains(text(), 'Next')]")
                        found_next = False
                        for btn in next_btns:
                            if btn.is_displayed():
                                driver.execute_script("arguments[0].click();", btn)
                                time.sleep(5)
                                page_num += 1
                                found_next = True
                                break
                        if not found_next: break
                    except: break
                except: break
        except Exception as e:
            print(f"   ⚠️ Interaction Failed: {e}")
            continue

    print(f"   -> Found {len(all_koi_rides)} TOTAL new rides from Koi Ride.")
    return all_koi_rides

# ==========================================
# 5. SOURCE C: GET-E (UPDATED)
# ==========================================

def fetch_gete_rides(driver, processed_list):
    print("\n--- STARTING SOURCE C: GET-E ---")
    driver.get(GETE_LOGIN_URL)
    time.sleep(5)

    if "login" in driver.current_url or "auth" in driver.current_url:
        try:
            user_in = driver.find_elements(By.CSS_SELECTOR, "input[type='email'], input[name='email']")
            if user_in:
                user_in[0].clear()
                user_in[0].send_keys(GETE_EMAIL)
            pass_in = driver.find_elements(By.CSS_SELECTOR, "input[type='password'], input[name='password']")
            if pass_in:
                pass_in[0].clear()
                pass_in[0].send_keys(GETE_PASS)
                time.sleep(1)
                try:
                    login_btn = driver.find_element(By.XPATH, "//button[.//span[contains(text(), 'Sign in')]] | //button[contains(., 'Sign in')]")
                    driver.execute_script("arguments[0].click();", login_btn)
                except:
                    pass_in[0].send_keys(Keys.RETURN)
            
            time.sleep(10)
        except Exception as e:
            print(f"   ⚠️ Get-e Login failed: {e}")

    # RESET FILTERS
    try:
        reset_btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Reset filters')] | //button[.//div[contains(text(), 'Reset filters')]]")))
        reset_btn.click()
        time.sleep(3)
        print("   -> 🔄 Filters reset.")
    except:
        print("   -> ⚠️ Could not find Reset Filters (might be clean).")

    # PHASE 1: CONFIRM 25 RIDES
    try:
        to_confirm_tab = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), 'To confirm')]] | //button[contains(., 'To confirm')]")))
        to_confirm_tab.click()
        time.sleep(3)
        print("   -> 🟢 Checking 'To confirm' tab...")
        
        for i in range(25):
            try:
                confirm_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Confirm')] | //button//span[contains(text(), 'Confirm')]")
                driver.execute_script("arguments[0].scrollIntoView(true);", confirm_btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", confirm_btn)
                print(f"      ✅ Confirmed ride #{i+1}")
                time.sleep(2) 
            except: break
    except Exception as e:
        print(f"   ⚠️ Error during Confirm phase: {e}")

    # PHASE 2: SWITCH TO 'CONFIRMED' TAB
    try:
        print("   -> 🟢 Switching to 'Confirmed' tab to scrape...")
        confirmed_tab = driver.find_element(By.XPATH, "//button[.//span[contains(text(), 'Confirmed')]] | //button[contains(., 'Confirmed')]")
        confirmed_tab.click()
        time.sleep(5) 
    except:
        print("   ❌ Could not switch to Confirmed tab.")
        return []

    # PHASE 3: SCRAPE DETAILS
    rides_found = []
    row_ids_to_process = []
    try:
        try:
            rows = driver.find_elements(By.CSS_SELECTOR, ".MuiDataGrid-row")
            print(f"   -> Found {len(rows)} rows. Opening details for each...")
            for row in rows:
                try:
                    row_idx = row.get_attribute("data-rowindex")
                    ref_cell = row.find_element(By.CSS_SELECTOR, "[data-field='references']").text
                    r_id = "".join(filter(str.isdigit, ref_cell.split('\n')[-1]))
                    if r_id not in processed_list:
                        row_ids_to_process.append(row_idx)
                except: continue
        except: pass

        for idx in row_ids_to_process:
            try:
                target_row = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, f".MuiDataGrid-row[data-rowindex='{idx}']")))
                driver.execute_script("arguments[0].click();", target_row)
                time.sleep(3) 

                current_url = driver.current_url
                r_id = current_url.split("/")[-1] 

                full_dt_str = ""
                try:
                    # Priority 1: Yellow (Flight Arrival)
                    arrival_ele = driver.find_elements(By.XPATH, "//div[contains(text(), 'Arrival')]/following-sibling::div | //p[contains(text(), 'Arrival')]/following-sibling::p | //span[contains(text(), 'Arrival')]")
                    flight_section = driver.find_elements(By.XPATH, "//*[contains(text(), 'Flight information')]/ancestor::div[contains(@class, 'MuiPaper-root')]")
                    
                    # Priority 2: Blue (Main Pickup Header)
                    main_date_ele = driver.find_element(By.XPATH, "//*[contains(text(), 'Pickup date and time')]/following-sibling::p | //*[contains(text(), 'Pickup date and time')]/..//p")
                    main_date_text = main_date_ele.text.strip()
                    clean_dt = main_date_text.replace("at ", "").replace(" PM", "").replace(" AM", "").split("\n")[0]
                    dt_obj = datetime.strptime(clean_dt, "%a, %d %b %Y %H:%M")
                    full_dt_str = dt_obj.strftime("%Y-%m-%dT%H:%M:00")
                except:
                    full_dt_str = datetime.now().strftime("%Y-%m-%dT12:00:00") 

                flight = ""
                try:
                    body_text = driver.find_element(By.TAG_NAME, "body").text
                    matches = re.findall(r'\(([A-Z0-9]{2,8})\)', body_text)
                    if matches: flight = matches[0] 
                except: pass

                pickup = "Unknown"
                dropoff = "Unknown"
                try:
                    pickup = driver.find_element(By.XPATH, "//*[contains(text(), 'Pickup')]/following-sibling::p | //div[contains(@class, 'timeline')]//p[1]").text
                    dropoff = driver.find_element(By.XPATH, "//*[contains(text(), 'Drop-off')]/following-sibling::p | //div[contains(@class, 'timeline')]//p[2]").text
                except: pass

                name = "Unknown"
                phone = ""
                pax_count = "1"
                try:
                    info_section = driver.find_element(By.XPATH, "//*[contains(text(), 'Ride information')]/ancestor::div[contains(@class, 'MuiPaper-root')]").text
                    if "Name" in info_section: 
                        name_line = [l for l in info_section.split("\n") if "Name" in l][0] 
                        name = name_line.split("Name")[-1].strip(": ").strip()
                    
                    phone_match = re.search(r'\+\d{8,15}', info_section)
                    if phone_match: phone = phone_match.group(0)

                    if "Passengers" in info_section:
                        pax_match = re.search(r'Passengers\s+(\d+)', info_section)
                        if pax_match: pax_count = pax_match.group(1)
                except: pass

                vehicle = "Standard"
                try:
                    veh_section = driver.find_element(By.XPATH, "//*[contains(text(), 'Selected vehicle')]/ancestor::div[contains(@class, 'MuiPaper-root')]").text
                    veh_section_upper = veh_section.upper()
                    if "VAN" in veh_section_upper or "PEOPLE" in veh_section_upper: vehicle = "Minivan"
                    elif "BUSINESS" in veh_section_upper: vehicle = "Business"
                except: pass

                normalized_ride = {
                    "source": "GETE",
                    "id": r_id,
                    "pickup_dt_raw": full_dt_str,
                    "name": name,
                    "phone": phone,
                    "pax": pax_count,
                    "luggage": 0,
                    "flight": flight,
                    "pickup_addr": pickup,
                    "dropoff_addr": dropoff,
                    "vehicle_raw": vehicle,
                    "driver_note": f"GETE-{r_id}",
                    "inbound_hint": True 
                }
                rides_found.append(normalized_ride)
                driver.back()
                time.sleep(3) 

            except Exception as e:
                print(f"   ⚠️ Error scraping detail view: {e}")
                try: driver.back()
                except: pass
                time.sleep(2)

    except Exception as e:
        print(f"   ⚠️ Get-e General Error: {e}")

    print(f"   -> Found {len(rides_found)} new rides from Get-e.")
    return rides_found

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
                try: dt_obj = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
                except: dt_obj = datetime.strptime(raw_date, "%Y-%m-%d %H:%M") 
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
            # For GETE, use TEXT_MODE to select "GE CZ" by text
            current_contractor = "TEXT_MODE_GETE"

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
                    # Select "GE CZ" by visible text
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

def run_bot():
    print(f"🚀 Starting TRI-SOURCE Bot (Transferz + Koi + Get-e)...")
    
    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    prefs = {"profile.default_content_setting_values.notifications": 2}
    options.add_experimental_option("prefs", prefs)
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)
    
    processed_ids = load_processed_ids()
    all_new_rides = []

    try:
        tz_rides = fetch_transferz_rides(driver, processed_ids)
        all_new_rides.extend(tz_rides)
    except Exception as e:
        print(f"❌ Transferz Critical Error: {e}")

    try:
        koi_rides = fetch_koi_rides(driver, wait, processed_ids)
        all_new_rides.extend(koi_rides)
    except Exception as e:
        print(f"❌ Koi Ride Critical Error: {e}")

    try:
        gete_rides = fetch_gete_rides(driver, processed_ids)
        all_new_rides.extend(gete_rides)
    except Exception as e:
        print(f"❌ Get-e Critical Error: {e}")

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
            save_processed_id(ride['id'])
        count += 1
    
    clean_memory()
    print("🏁 Done.")
    driver.quit()

if __name__ == "__main__":
    run_bot()