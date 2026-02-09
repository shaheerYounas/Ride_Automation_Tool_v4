import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


# ============================================================
# 0) CONFIG (USE ENV VARS - DO NOT HARDCODE PASSWORDS)
# ============================================================

def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

# --- TRANSFERZ (SOURCE A) ---
TZ_LOGIN_URL = "https://rides.transferz.com/login"
TZ_JOURNEYS_URL = "https://rides.transferz.com/journeys"
# TZ_EMAIL = env("TZ_EMAIL")
# TZ_PASS  = env("TZ_PASS")
# TZ_COMPANY_ID = int(env("TZ_COMPANY_ID", "3843"))
TZ_EMAIL = "haytham97@live.com"
TZ_PASS  = "ZEuoHFzP78cp"
TZ_COMPANY_ID = 3843

# --- KOI RIDE (SOURCE B) ---
KOI_LOGIN_URL = "https://taxiportal.koiride.com/auth/sign-in"
KOI_ASSIGNED_URL = "https://taxiportal.koiride.com/ridesAssigned"
# KOI_USER = env("KOI_USER")
# KOI_PASS = env("KOI_PASS")
KOI_USER = "Haytham Montana"
KOI_PASS = "montana123"

# --- GET-E (SOURCE C) ---
GETE_LOGIN_URL = "https://app.portal.get-e.com/rides"
# GETE_EMAIL = env("GETE_EMAIL")
# GETE_PASS  = env("GETE_PASS")
GETE_EMAIL = "haytham97@live.com"
GETE_PASS  = "Stuntman1997!@"

# --- ACCOMMTRA (DESTINATION) ---
DEST_URL_LOGIN = "https://driver.accommtra.com/login"
# DEST_EMAIL = env("DEST_EMAIL")
# DEST_PASS  = env("DEST_PASS")
DEST_EMAIL = "haytham97@live.com"
DEST_PASS  = "tham97"
DEST_FORM_URL_BASE = "https://driver.accommtra.com/order-filter/item/date/"

# --- CONTRACTOR MAPPING ---
# CONTRACTOR_ID_TZ = env("CONTRACTOR_ID_TZ", "227")  # Transferz
# CONTRACTOR_ID_KOI = env("CONTRACTOR_ID_KOI", "269")  # Koi
# CONTRACTOR_TEXT_KOI = env("CONTRACTOR_TEXT_KOI", "Koi Ride")
# CONTRACTOR_TEXT_GETE = env("CONTRACTOR_TEXT_GETE", "GE CZ")
CONTRACTOR_ID_TZ   = "227"     # Transferz
CONTRACTOR_ID_KOI  = "269"     # Koi Ride
CONTRACTOR_TEXT_KOI = "Koi Ride" 
CONTRACTOR_TEXT_GETE = "GE CZ" # Get-e (Select by Text)

# SETTINGS
# DEFAULT_PRICE = env("DEFAULT_PRICE", "800")
# MEMORY_FILE = env("MEMORY_FILE", "processed_rides.txt")
DEFAULT_PRICE = "800" 
MEMORY_FILE = "processed_rides.txt"

# Which KOI dates to fetch
KOI_FETCH_DAYS_AHEAD = [1, 2]  # tomorrow + day after


# ============================================================
# 1) DATA MODEL + NORMALIZATION / DEDUP
# ============================================================

@dataclass(frozen=True)
class Ride:
    source: str
    supplier_id: str
    pickup_dt_raw: str
    name: str
    phone: str
    pax: str
    luggage: int
    flight: str
    pickup_addr: str
    dropoff_addr: str
    vehicle_raw: str
    driver_note: str
    inbound_hint: bool

def normalize_text(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[\n\r\t]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # remove common junk words to reduce “same address but formatted differently”
    junk = {"street","st","road","rd","avenue","ave","building","bldg","apt","apartment","floor","fl","the"}
    parts = [p for p in s.split() if p not in junk]
    return " ".join(parts)

def parse_dt(raw_date: str) -> Optional[datetime]:
    if not raw_date:
        return None
    raw_date = raw_date.strip()
    fmts = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:00",
    ]
    for f in fmts:
        try:
            return datetime.strptime(raw_date, f)
        except:
            pass
    return None

def round_time_to_nearest_5(dt_obj: datetime) -> datetime:
    total_minutes = dt_obj.hour * 60 + dt_obj.minute
    rounded_minutes = 5 * round(total_minutes / 5)
    new_hour = (rounded_minutes // 60) % 24
    new_min = rounded_minutes % 60
    return dt_obj.replace(hour=new_hour, minute=new_min, second=0)

def ride_fingerprint(ride: Ride) -> str:
    """
    Content-based fingerprint (NOT supplier id).
    This is what stops duplicates across tabs / sources / parsing failures.
    """
    dt = parse_dt(ride.pickup_dt_raw)
    dt_key = dt.strftime("%Y-%m-%d %H:%M") if dt else "unknown_dt"
    dt_key = dt_key  # we keep minutes; Accommtra will be rounded anyway

    name_key = normalize_text(ride.name)[:30]
    from_key = normalize_text(ride.pickup_addr)[:60]
    to_key   = normalize_text(ride.dropoff_addr)[:60]

    # Add flight only when it exists (helps inbound duplication)
    flight_key = normalize_text(ride.flight)[:20]

    return f"{dt_key}|{name_key}|{from_key}|{to_key}|{flight_key}"

def supplier_key(ride: Ride) -> str:
    return f"{ride.source}:{(ride.supplier_id or '').strip()}"


# ============================================================
# 2) MEMORY (PROCESSED IDs)
# ============================================================

def load_processed_ids() -> Set[str]:
    if not os.path.exists(MEMORY_FILE):
        return set()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return set(x.strip() for x in f.read().splitlines() if x.strip())

def append_processed_id(supplier_id: str) -> None:
    if not supplier_id:
        return
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(supplier_id.strip() + "\n")

def clean_memory(max_lines: int = 5000) -> None:
    try:
        if not os.path.exists(MEMORY_FILE):
            return
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            lines = [x.strip() for x in f.read().splitlines() if x.strip()]
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
    except:
        pass


# ============================================================
# 3) SELENIUM HELPERS
# ============================================================

def check_logout_screen(driver) -> bool:
    try:
        buttons = driver.find_elements(By.XPATH, "//span[contains(@class, 'button-title') and contains(text(), 'Click here')]")
        if buttons and buttons[0].is_displayed():
            print("   ⚠️ Logged-out screen found. Clicking...")
            buttons[0].click()
            time.sleep(5)
            return True
    except:
        pass
    return False

def force_fill(driver, element_name: str, value: str) -> bool:
    try:
        el = driver.find_element(By.NAME, element_name)
        driver.execute_script("arguments[0].value = arguments[1];", el, value)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", el)
        return True
    except:
        return False

def get_tz_api_token(driver) -> Optional[str]:
    logs = driver.get_log("performance")
    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]
            if message.get("method") == "Network.requestWillBeSent":
                params = message.get("params", {})
                request_url = params.get("request", {}).get("url", "")
                if "graphql" in request_url or "api" in request_url:
                    headers = params.get("request", {}).get("headers", {})
                    token = headers.get("Authorization") or headers.get("authorization")
                    if token and "Bearer" in token:
                        return token
        except:
            continue
    return None


# ============================================================
# 4) SOURCE A: TRANSFERZ
# ============================================================

def fetch_transferz_rides(driver, processed_ids: Set[str]) -> List[Ride]:
    print("\n--- SOURCE A: TRANSFERZ ---")
    if not TZ_EMAIL or not TZ_PASS:
        print("   ❌ Missing TZ_EMAIL / TZ_PASS env vars. Skipping Transferz.")
        return []

    driver.get(TZ_LOGIN_URL)
    time.sleep(5)
    check_logout_screen(driver)

    if "login" in driver.current_url:
        try:
            email_field = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "email")))
            email_field.clear()
            email_field.send_keys(TZ_EMAIL)
            pw = driver.find_element(By.NAME, "password")
            pw.clear()
            pw.send_keys(TZ_PASS)
            pw.send_keys(Keys.RETURN)
            time.sleep(8)
        except Exception as e:
            print(f"   ⚠️ Transferz login issue: {e}")

    driver.get(TZ_JOURNEYS_URL)
    time.sleep(6)

    access_token = get_tz_api_token(driver)
    if not access_token:
        print("   ❌ No Transferz API token captured. Skipping.")
        return []

    now = datetime.now()
    future = now + timedelta(days=90)
    date_start = now.strftime("%Y-%m-%dT00:00:00")
    date_end = future.strftime("%Y-%m-%dT23:59:59")

    js_script = f"""
    const query = `query Journeys($params: JourneysRequestParams!, $skip: Boolean! = false) {{
      journeys(params: $params) @skip(if: $skip) {{
        results {{
          id journeyCode inbound
          travellerInfo {{ firstName lastName phone flightNumber passengerCount driverComments luggageCount }}
          journeyExecutionInfo {{ pickupDate vehicleCategory }}
          originLocation {{ address {{ formattedAddress originalAddress }} }}
          destinationLocation {{ address {{ formattedAddress originalAddress }} }}
          driver {{ name }}
        }}
      }}
    }}`;
    const variables = {{
        "params": {{
            "assignedTransferCompanyId": [{TZ_COMPANY_ID}],
            "excludedStatuses": ["NOT_PAID"],
            "includedStatuses": ["PLANNED","CONFIRMED","DRIVER_ARRIVED","DRIVER_UNDERWAY","JOURNEY_IN_PROGRESS","COMPLETED"],
            "page": 0, "size": 50, "sort": ["pickup;asc"],
            "pickupDateAfter": "{date_start}", "pickupDateBefore": "{date_end}"
        }}, "skip": false
    }};
    var callback = arguments[arguments.length - 1];
    fetch('https://rides.transferz.com/api/graphql', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json', 'Authorization': '{access_token}' }},
        body: JSON.stringify({{ query: query, variables: variables }})
    }}).then(r => r.json()).then(d => callback(d)).catch(e => callback({{"error": e.toString()}}));
    """

    response = driver.execute_async_script(js_script)
    if not response or "data" not in response:
        print("   ❌ Transferz GraphQL returned no data.")
        return []

    results = response.get("data", {}).get("journeys", {}).get("results", []) or []
    rides: List[Ride] = []
    seen_in_this_source: Set[str] = set()

    for r in results:
        journey_code = str(r.get("journeyCode") or "").strip()
        if not journey_code:
            continue

        # processed check (by supplier id)
        if journey_code in processed_ids:
            continue
        if journey_code in seen_in_this_source:
            continue
        seen_in_this_source.add(journey_code)

        t = r.get("travellerInfo") or {}
        j = r.get("journeyExecutionInfo") or {}
        origin = (r.get("originLocation") or {}).get("address") or {}
        dest = (r.get("destinationLocation") or {}).get("address") or {}
        driver_info = r.get("driver") or {}

        pickup_addr = origin.get("originalAddress") or origin.get("formattedAddress") or "Unknown"
        dropoff_addr = dest.get("originalAddress") or dest.get("formattedAddress") or "Unknown"

        raw_name = f"{t.get('firstName','')} {t.get('lastName','')}".strip()
        name = raw_name.title() if raw_name else "Unknown"

        rides.append(Ride(
            source="TRANSFERZ",
            supplier_id=journey_code,
            pickup_dt_raw=str(j.get("pickupDate") or ""),
            name=name,
            phone=str(t.get("phone") or ""),
            pax=str(t.get("passengerCount") or "1"),
            luggage=int(t.get("luggageCount") or 0),
            flight=str(t.get("flightNumber") or ""),
            pickup_addr=pickup_addr,
            dropoff_addr=dropoff_addr,
            vehicle_raw=str(j.get("vehicleCategory") or "Standard"),
            driver_note=f"{journey_code} | Driver: {driver_info.get('name','No Driver')} | {t.get('driverComments','')}",
            inbound_hint=bool(r.get("inbound", True)),
        ))

    print(f"   -> Transferz new rides: {len(rides)}")
    return rides


# ============================================================
# 5) SOURCE B: KOI RIDE
# ============================================================

def fetch_koi_rides(driver, wait: WebDriverWait, processed_ids: Set[str]) -> List[Ride]:
    print("\n--- SOURCE B: KOI RIDE ---")
    if not KOI_USER or not KOI_PASS:
        print("   ❌ Missing KOI_USER / KOI_PASS env vars. Skipping Koi.")
        return []

    driver.get(KOI_LOGIN_URL)
    time.sleep(5)

    try:
        if "auth" in driver.current_url:
            user_in = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']")))
            user_in.clear()
            user_in.send_keys(KOI_USER)
            pass_in = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            pass_in.clear()
            pass_in.send_keys(KOI_PASS)
            pass_in.send_keys(Keys.RETURN)
            time.sleep(8)
    except Exception as e:
        print(f"   ⚠️ Koi login issue: {e}")

    today = datetime.now()
    target_dates = [today + timedelta(days=d) for d in KOI_FETCH_DAYS_AHEAD]
    all_rides: List[Ride] = []
    seen_in_this_source: Set[str] = set()

    for target_date in target_dates:
        print(f"   -> 📅 KOI date: {target_date.strftime('%Y-%m-%d')}")
        driver.get(KOI_ASSIGNED_URL)
        time.sleep(4)

        try:
            date_input = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//input[contains(@placeholder, 'Select date')]"))
            )
            date_input.click()
            time.sleep(1)

            day_str = str(target_date.day)
            calendar_days = driver.find_elements(By.XPATH, "//div[contains(@class, 'day') or contains(@class, 'cell')]")
            clicked = False
            for cell in calendar_days:
                if cell.text.strip() == day_str:
                    try:
                        cell.click()
                        clicked = True
                        break
                    except:
                        pass
            if not clicked:
                continue

            # Confirm date selection if needed
            try:
                driver.find_element(By.XPATH, "//span[contains(text(), 'Select')] | //button[contains(text(), 'Select')]").click()
            except:
                pass
            time.sleep(1)

            # Search
            try:
                driver.find_element(By.XPATH, "//div[contains(@class, 'btn-content') and contains(text(), 'Search')]").click()
            except:
                driver.execute_script("document.querySelectorAll('.btn-primary').forEach(b => { if(b.innerText.includes('Search')) b.click() })")
            time.sleep(5)

            page_num = 1
            while True:
                rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                if not rows or "No results" in driver.page_source:
                    break

                print(f"      -> Scraping page {page_num} ({len(rows)} rows)")
                for row in rows:
                    try:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if not cols:
                            continue

                        r_id = cols[0].text.strip()
                        if not r_id:
                            continue

                        if r_id in processed_ids or r_id in seen_in_this_source:
                            continue
                        seen_in_this_source.add(r_id)

                        raw_dt = cols[2].text.split("\n")
                        date_part = raw_dt[0].strip()
                        time_part = raw_dt[1].strip() if len(raw_dt) > 1 else "00:00"
                        full_dt_str = f"{date_part}T{time_part}:00"

                        flight_text = cols[3].text.strip()
                        flight_match = re.search(r'([A-Z0-9]{2,}\d+)', flight_text)
                        flight = flight_match.group(1) if flight_match else ""

                        pax_lines = cols[5].text.split("\n") if len(cols) > 5 else []
                        name = pax_lines[0].strip() if pax_lines else "Unknown"
                        phone = ""
                        pax_count = "1"
                        vehicle = "Standard"

                        for line in pax_lines:
                            digits = line.replace(" ", "").replace("-", "")
                            if digits.isdigit() and len(digits) > 6:
                                phone = line.strip()
                                if not phone.startswith("+"):
                                    phone = "+" + phone
                            if "passengers" in line.lower():
                                pax_count = re.sub(r"[^0-9]", "", line) or "1"
                            if "executive" in line.lower():
                                vehicle = "Business"
                            if "people carrier" in line.lower() or "vito" in line.lower() or "van" in line.lower():
                                vehicle = "Minivan"

                        pickup = cols[7].text.replace("\n", " ").strip() if len(cols) > 7 else "Unknown"
                        dropoff = cols[8].text.replace("\n", " ").strip() if len(cols) > 8 else "Unknown"
                        comment = cols[10].text.strip() if len(cols) > 10 else ""

                        all_rides.append(Ride(
                            source="KOI",
                            supplier_id=r_id,
                            pickup_dt_raw=full_dt_str,
                            name=name,
                            phone=phone,
                            pax=pax_count,
                            luggage=0,
                            flight=flight,
                            pickup_addr=pickup,
                            dropoff_addr=dropoff,
                            vehicle_raw=vehicle,
                            driver_note=f"KOI-{r_id} | {comment}",
                            inbound_hint=True,
                        ))
                    except:
                        continue

                # Next page
                try:
                    next_btns = driver.find_elements(By.XPATH, "//li[not(contains(@class, 'disabled'))]//button[contains(@class, 'page-link') and contains(text(), 'Next')]")
                    moved = False
                    for btn in next_btns:
                        if btn.is_displayed():
                            driver.execute_script("arguments[0].click();", btn)
                            time.sleep(5)
                            page_num += 1
                            moved = True
                            break
                    if not moved:
                        break
                except:
                    break

        except Exception as e:
            print(f"   ⚠️ KOI interaction failed: {e}")
            continue

    print(f"   -> KOI new rides: {len(all_rides)}")
    return all_rides


# ============================================================
# 6) SOURCE C: GET-E (NO DUPLICATED FUNCTIONS)
# ============================================================

def ensure_only_tab_active(driver, target_tab_text: str):
    all_tabs = ["To confirm", "Confirmed", "To close", "Completed", "Cancelled"]
    print(f"   -> Enforcing only tab: '{target_tab_text}'")

    for tab_name in all_tabs:
        try:
            btn = driver.find_element(By.XPATH, f"//button[contains(., '{tab_name}')]")
            bg_color = driver.execute_script("return window.getComputedStyle(arguments[0]).backgroundColor;", btn)

            is_active = False
            if "rgb" in bg_color:
                rgb = [int(x) for x in re.findall(r"\d+", bg_color)]
                if len(rgb) >= 3 and rgb[0] < 100 and rgb[2] > 100:
                    is_active = True

            if tab_name == target_tab_text:
                if not is_active:
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
            else:
                if is_active:
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
        except:
            pass

    time.sleep(3)

def scrape_gete_current_view(driver, processed_ids: Set[str], seen_ids: Set[str]) -> List[Ride]:
    rides: List[Ride] = []

    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, ".MuiDataGrid-row")
        if not rows:
            break

        row_indices = []
        for row in rows:
            try:
                row_indices.append(row.get_attribute("data-rowindex"))
            except:
                pass

        if not row_indices:
            break

        for r_idx in row_indices:
            try:
                target_row = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, f".MuiDataGrid-row[data-rowindex='{r_idx}']"))
                )
                driver.execute_script("arguments[0].click();", target_row)
                time.sleep(2)

                page_text = driver.find_element(By.TAG_NAME, "body").text

                # --- Ride ID: prefer URL ---
                r_id = "Unknown"
                url_part = driver.current_url.split("/")[-1]
                if url_part.replace("-", "").isdigit() and len(url_part.replace("-", "")) > 5:
                    r_id = url_part.replace("-", "")
                else:
                    m = re.search(r"Ride number[:\s]*([\d-]+)", page_text)
                    if m:
                        r_id = m.group(1).replace("-", "")

                # skip duplicates (important!)
                if r_id in processed_ids or r_id in seen_ids:
                    driver.back()
                    time.sleep(1)
                    continue

                # --- datetime ---
                full_dt_str = datetime.now().strftime("%Y-%m-%dT12:00:00")
                date_matches = re.findall(r'(\w{3}, \d{1,2} \w{3} \d{4} at \d{1,2}:\d{2} [AP]M)', page_text)
                if date_matches:
                    try:
                        clean_dt = date_matches[0].replace("at ", "")
                        dt_obj = datetime.strptime(clean_dt, "%a, %d %b %Y %H:%M %p")
                        full_dt_str = dt_obj.strftime("%Y-%m-%dT%H:%M:00")
                    except:
                        pass

                # --- name ---
                name = "Unknown"
                try:
                    # Most stable: find any hail icon and following span
                    name_ele = driver.find_element(By.XPATH, "//*[contains(@data-testid, 'Hail')]/following-sibling::span")
                    name = name_ele.text.strip()
                except:
                    m = re.search(r"Name:\s*(.+)", page_text)
                    if m:
                        name = m.group(1).strip()

                if len(name) < 2:
                    name = "Unknown"

                # --- addresses ---
                pickup, dropoff = "See Notes", "See Notes"
                if "From:" in page_text and "To:" in page_text:
                    m = re.search(r"From:\s*(.+?)\nTo:\s*(.+)", page_text)
                    if m:
                        pickup = m.group(1).strip()
                        dropoff = m.group(2).strip()

                # --- vehicle ---
                vehicle = "Standard"
                if "business" in page_text.lower():
                    vehicle = "Business"
                elif "van" in page_text.lower():
                    vehicle = "Minivan"

                rides.append(Ride(
                    source="GETE",
                    supplier_id=r_id,
                    pickup_dt_raw=full_dt_str,
                    name=name,
                    phone="",
                    pax="1",
                    luggage=0,
                    flight="",
                    pickup_addr=pickup,
                    dropoff_addr=dropoff,
                    vehicle_raw=vehicle,
                    driver_note=f"GETE-{r_id}",
                    inbound_hint=True,
                ))

                seen_ids.add(r_id)
                driver.back()
                time.sleep(2)

            except Exception as e:
                print(f"      ⚠️ GET-E scrape error row {r_idx}: {e}")
                try:
                    driver.get(GETE_LOGIN_URL)
                    time.sleep(3)
                except:
                    pass
                return rides

        # next page
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, "button[title='Go to next page']")
            if (not next_btn.is_enabled()) or ("disabled" in (next_btn.get_attribute("class") or "")):
                break
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(4)
        except:
            break

    return rides

def fetch_gete_rides(driver, processed_ids: Set[str]) -> List[Ride]:
    print("\n--- SOURCE C: GET-E ---")
    if not GETE_EMAIL or not GETE_PASS:
        print("   ❌ Missing GETE_EMAIL / GETE_PASS env vars. Skipping Get-e.")
        return []

    driver.get(GETE_LOGIN_URL)
    time.sleep(3)

    # login if needed
    try:
        is_login = False
        try:
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']")))
            is_login = True
        except:
            is_login = False

        if is_login:
            user_in = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='email']")))
            user_in.clear()
            user_in.send_keys(GETE_EMAIL)

            pass_in = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            pass_in.clear()
            pass_in.send_keys(GETE_PASS)

            try:
                driver.find_element(By.XPATH, "//button[contains(., 'Sign in') or contains(., 'Log in')]").click()
            except:
                pass_in.send_keys(Keys.RETURN)

            WebDriverWait(driver, 20).until(EC.url_contains("rides"))
            time.sleep(4)
    except Exception as e:
        print(f"   ⚠️ Get-e login issue: {e}")

    # reset filters
    try:
        reset_btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Reset filters')]")))
        driver.execute_script("arguments[0].click();", reset_btn)
        time.sleep(3)
    except:
        pass

    all_rides: List[Ride] = []
    seen_gete_ids: Set[str] = set()  # prevents duplicates across tabs

    for tab in ["To confirm", "Confirmed"]:
        try:
            ensure_only_tab_active(driver, tab)
            print(f"   -> Scraping tab: {tab}")
            rides = scrape_gete_current_view(driver, processed_ids, seen_gete_ids)
            all_rides.extend(rides)
        except Exception as e:
            print(f"   ⚠️ Get-e tab '{tab}' error: {e}")

    print(f"   -> Get-e new rides: {len(all_rides)}")
    return all_rides


# ============================================================
# 7) ACCOMMTRA: DUP CHECK + SUBMIT
# ============================================================

def is_inbound_from_addresses(pickup: str, dropoff: str) -> bool:
    pu = (pickup or "").upper()
    do = (dropoff or "").upper()

    # crude but consistent with your logic
    if any(x in do for x in ["AIRPORT", "VACLA", "PRG"]):
        return False
    if any(x in pu for x in ["AIRPORT", "VACLA", "PRG"]):
        return True
    return True

def accommtra_page_maybe_contains_duplicate(driver, ride: Ride, dt_obj: datetime) -> bool:
    """
    Best-effort duplicate check:
    - We look at page text for normalized name + from + to + time.
    If Accommtra list doesn't show these, this won't work — but it helps when it does.
    """
    try:
        page = normalize_text(driver.find_element(By.TAG_NAME, "body").text)
        time_key = dt_obj.strftime("%H:%M")
        # We also normalize time_key to "hh mm" pattern
        time_key_norm = normalize_text(time_key)

        n = normalize_text(ride.name)
        f = normalize_text(ride.pickup_addr)
        t = normalize_text(ride.dropoff_addr)

        # weak contains check: any 3 signals = likely duplicate
        hits = 0
        if n and n[:12] in page:
            hits += 1
        if f and f[:18] in page:
            hits += 1
        if t and t[:18] in page:
            hits += 1
        if time_key_norm and time_key_norm in page:
            hits += 1

        return hits >= 3
    except:
        return False

def process_single_order(driver, wait: WebDriverWait, ride: Ride) -> bool:
    raw_date = ride.pickup_dt_raw
    dt_obj = parse_dt(raw_date)
    if not dt_obj:
        print(f"   ❌ Date parse failed: {raw_date}")
        return False

    pickup = ride.pickup_addr
    dropoff = ride.dropoff_addr

    is_inbound = is_inbound_from_addresses(pickup, dropoff)

    # Transferz inbound adjustment
    if ride.source == "TRANSFERZ" and is_inbound:
        dt_obj = dt_obj - timedelta(minutes=15)

    dt_obj = round_time_to_nearest_5(dt_obj)

    date_formatted = dt_obj.strftime("%d.%m.%Y")
    time_formatted = dt_obj.strftime("%H:%M")
    url_date = dt_obj.strftime("%Y-%m-%d")

    # contractor selection
    current_contractor_mode = "VALUE"
    contractor_value = CONTRACTOR_ID_TZ

    if ride.source == "KOI":
        contractor_value = CONTRACTOR_ID_KOI
        if not contractor_value or contractor_value == "FIX_ME":
            current_contractor_mode = "TEXT"
    elif ride.source == "GETE":
        current_contractor_mode = "TEXT_GETE"

    # vehicle mapping + template
    veh_raw = (ride.vehicle_raw or "").upper()
    veh_val = "1"
    template_id = "205" if is_inbound else "207"

    if "BUSINESS" in veh_raw:
        veh_val = "18"
        template_id = "205" if is_inbound else "207"
    elif any(x in veh_raw for x in ["MINIVAN", "PEOPLE", "VAN", "VITO", "MINI"]):
        veh_val = "2"
        template_id = "206" if is_inbound else "208"
    else:
        veh_val = "1"
        template_id = "205" if is_inbound else "207"

    print(f"   -> Accommtra: {ride.name} | {dt_obj.strftime('%Y-%m-%d %H:%M')} | {ride.source}")

    driver.get(DEST_FORM_URL_BASE + url_date)

    # wait for form
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

    # PRE-CHECK DUP (best effort)
    if accommtra_page_maybe_contains_duplicate(driver, ride, dt_obj):
        print("      ⚠️ Likely duplicate already exists on this date page. Skipping.")
        return True  # treat as “done” to avoid reprocessing

    # template + contractor
    try:
        Select(driver.find_element(By.NAME, "OrderTemplate")).select_by_value(template_id)
        time.sleep(0.5)

        contractor_dropdown = Select(driver.find_element(By.NAME, "Contractor"))
        if current_contractor_mode == "TEXT":
            contractor_dropdown.select_by_visible_text(CONTRACTOR_TEXT_KOI)
        elif current_contractor_mode == "TEXT_GETE":
            contractor_dropdown.select_by_visible_text(CONTRACTOR_TEXT_GETE)
        else:
            contractor_dropdown.select_by_value(contractor_value)
        time.sleep(0.5)
    except Exception as e:
        print(f"      ⚠️ Contractor/template select issue: {e}")

    # fill form
    force_fill(driver, "firstname", ride.name)
    force_fill(driver, "lastname", "")
    force_fill(driver, "phone", ride.phone)
    force_fill(driver, "firstway__persons", str(ride.pax or "1"))

    try:
        Select(driver.find_element(By.NAME, "firstway__vehicle_category_id")).select_by_value(veh_val)
    except:
        pass

    force_fill(driver, "firstway__date", date_formatted)
    force_fill(driver, "firstway__time", time_formatted)

    try:
        driver.find_element(By.TAG_NAME, "body").click()
    except:
        pass

    force_fill(driver, "firstway__from", pickup)
    force_fill(driver, "firstway__to", dropoff)

    flight_val = ride.flight if is_inbound else ""
    force_fill(driver, "firstway__flight", flight_val)

    force_fill(driver, "firstway__price_1", DEFAULT_PRICE)
    force_fill(driver, "firstway__price_2", "")

    # Put a content fingerprint into driver note (VERY useful)
    fp = ride_fingerprint(ride)
    safe_note = (ride.driver_note or "").replace("'", "")
    note_value = f"{safe_note} | FP={fp}".replace("'", "")

    try:
        driver.execute_script("document.getElementById('firstway__driver_note').value = arguments[0];", note_value)
    except:
        pass

    # save
    try:
        driver.find_element(By.ID, "sendButton").click()
        time.sleep(2)
        print("      ✅ Saved.")
        return True
    except Exception as e:
        print(f"      ❌ Save failed: {e}")
        return False


# ============================================================
# 8) MAIN EXECUTION (GLOBAL DEDUP)
# ============================================================

def login_accommtra(driver, wait: WebDriverWait) -> None:
    driver.get(DEST_URL_LOGIN)
    time.sleep(5)

    if "login" not in driver.current_url.lower():
        return

    # try locate username
    user_selectors = [
        (By.NAME, "username"), (By.NAME, "email"), (By.NAME, "login"),
        (By.ID, "username"), (By.ID, "email")
    ]
    for sel in user_selectors:
        try:
            user_box = driver.find_element(*sel)
            user_box.clear()
            user_box.send_keys(DEST_EMAIL)
            break
        except:
            pass

    try:
        pass_box = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pass_box.clear()
        pass_box.send_keys(DEST_PASS)
        pass_box.send_keys(Keys.RETURN)
        time.sleep(8)
    except:
        print("   ❌ Accommtra password field not found. Manual login needed.")
        time.sleep(60)

def global_dedup(rides: List[Ride]) -> List[Ride]:
    """
    Dedup by:
    1) supplier_key (source+id) if present
    2) content fingerprint as fallback / cross-tab protection
    """
    out: List[Ride] = []
    seen_supplier: Set[str] = set()
    seen_fp: Set[str] = set()

    for r in rides:
        sk = supplier_key(r)
        fp = ride_fingerprint(r)

        if sk in seen_supplier:
            continue
        if fp in seen_fp:
            continue

        seen_supplier.add(sk)
        seen_fp.add(fp)
        out.append(r)
    return out

def run_bot():
    print("🚀 Starting improved TRI-SOURCE Bot (Dedup + Safer)")

    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    prefs = {"profile.default_content_setting_values.notifications": 2}
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)

    processed_ids = load_processed_ids()  # supplier IDs stored in file
    processed_ids_in_run = set(processed_ids)  # we update this in-memory on save

    # Fetch
    all_rides: List[Ride] = []
    try:
        all_rides.extend(fetch_transferz_rides(driver, processed_ids_in_run))
    except Exception as e:
        print(f"❌ Transferz error: {e}")

    try:
        all_rides.extend(fetch_koi_rides(driver, wait, processed_ids_in_run))
    except Exception as e:
        print(f"❌ KOI error: {e}")

    try:
        all_rides.extend(fetch_gete_rides(driver, processed_ids_in_run))
    except Exception as e:
        print(f"❌ Get-e error: {e}")

    print(f"\n👉 Raw collected rides: {len(all_rides)}")

    # GLOBAL DEDUP (fixes your “same run duplicates” problem)
    all_rides = global_dedup(all_rides)
    print(f"👉 After global dedup: {len(all_rides)}")

    if not all_rides:
        print("🏁 No new rides. Exiting.")
        driver.quit()
        return

    # Login to Accommtra
    if not DEST_EMAIL or not DEST_PASS:
        print("❌ Missing DEST_EMAIL / DEST_PASS env vars. Cannot proceed.")
        driver.quit()
        return

    print("\n--- DESTINATION: ACCOMMTRA ---")
    login_accommtra(driver, wait)

    # Process
    ok = 0
    for idx, ride in enumerate(all_rides, 1):
        print(f"\n--- Processing {idx}/{len(all_rides)} ---")

        # extra safety: skip if processed in this run
        if ride.supplier_id and ride.supplier_id in processed_ids_in_run:
            print("   ⚠️ Already marked processed in this run. Skipping.")
            continue

        success = process_single_order(driver, wait, ride)
        if success:
            ok += 1
            # record supplier id
            if ride.supplier_id:
                append_processed_id(ride.supplier_id)
                processed_ids_in_run.add(ride.supplier_id)  # <-- THIS FIXES SAME-RUN DUPES

    clean_memory()
    print(f"\n🏁 Done. Saved/Skipped as processed: {ok}/{len(all_rides)}")
    driver.quit()

if __name__ == "__main__":
    run_bot()
