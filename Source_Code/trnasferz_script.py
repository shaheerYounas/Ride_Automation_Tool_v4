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

# =========================
# CONFIG - TRANSFERZ
# =========================
TZ_LOGIN_URL = "https://rides.transferz.com/login"
TZ_JOURNEYS_URL = "https://rides.transferz.com/journeys"
TZ_EMAIL = "haytham97@live.com"
TZ_PASS  = "ZEuoHFzP78cp"
TZ_COMPANY_ID = 3843

# =========================
# CONFIG - ACCOMMTRA
# =========================
DEST_URL_LOGIN = "https://driver.accommtra.com/login"
DEST_EMAIL = "haytham97@live.com"
DEST_PASS  = "tham97"
DEST_FORM_URL_BASE = "https://driver.accommtra.com/order-filter/item/date/"

# Contractor (Transferz)
CONTRACTOR_ID_TZ = "227"

DEFAULT_PRICE = "800"
MEMORY_FILE = "processed_transferz.txt"   # separate memory per source
FETCH_DAYS_AHEAD = 90

ACCOMMTRA_FP_GUARD = True

# =========================
# DATA MODEL
# =========================
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
    junk = {"street","st","road","rd","avenue","ave","building","bldg","apt","apartment","floor","fl","the"}
    parts = [p for p in s.split() if p not in junk]
    return " ".join(parts)

def parse_dt(raw_date: str) -> Optional[datetime]:
    if not raw_date:
        return None
    s = raw_date.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except:
        pass
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, f)
        except:
            pass
    return None

def round_time_to_nearest_5(dt_obj: datetime) -> datetime:
    total_minutes = dt_obj.hour * 60 + dt_obj.minute
    rounded_minutes = 5 * round(total_minutes / 5)
    new_hour = (rounded_minutes // 60) % 24
    new_min = rounded_minutes % 60
    return dt_obj.replace(hour=new_hour, minute=new_min, second=0)

def ride_fingerprint(r: Ride) -> str:
    dt = parse_dt(r.pickup_dt_raw)
    dt_key = dt.strftime("%Y-%m-%d %H:%M") if dt else "unknown_dt"
    name_key = normalize_text(r.name)[:30]
    from_key = normalize_text(r.pickup_addr)[:60]
    to_key = normalize_text(r.dropoff_addr)[:60]
    flight_key = normalize_text(r.flight)[:20]
    return f"{dt_key}|{name_key}|{from_key}|{to_key}|{flight_key}"

# =========================
# MEMORY (namespaced)
# =========================
def load_processed() -> Set[str]:
    if not os.path.exists(MEMORY_FILE):
        return set()
    out = set()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            line = line.strip()
            if line:
                out.add(line)
    return out

def append_processed(source: str, rid: str) -> None:
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{source.upper()}:{rid}\n")

# =========================
# SELENIUM HELPERS
# =========================
def get_driver() -> webdriver.Chrome:
    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    prefs = {"profile.default_content_setting_values.notifications": 2}
    options.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    return driver

def check_logout_screen(driver) -> None:
    try:
        buttons = driver.find_elements(By.XPATH, "//span[contains(@class,'button-title') and contains(text(),'Click here')]")
        if buttons and buttons[0].is_displayed():
            buttons[0].click()
            time.sleep(4)
    except:
        pass

def get_tz_api_token(driver) -> Optional[str]:
    logs = driver.get_log("performance")
    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]
            if message.get("method") == "Network.requestWillBeSent":
                params = message.get("params", {})
                url = params.get("request", {}).get("url", "")
                if "graphql" in url:
                    headers = params.get("request", {}).get("headers", {})
                    token = headers.get("Authorization") or headers.get("authorization")
                    if token and "Bearer" in token:
                        return token
        except:
            continue
    return None

def force_fill(driver, name: str, value: str) -> None:
    try:
        el = driver.find_element(By.NAME, name)
        driver.execute_script("arguments[0].value = arguments[1];", el, value)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", el)
    except:
        pass

# =========================
# TRANSFERZ FETCH
# =========================
def fetch_transferz(driver, processed: Set[str]) -> List[Ride]:
    print("\n--- TRANSFERZ ---")

    driver.get(TZ_LOGIN_URL)
    time.sleep(4)
    check_logout_screen(driver)

    if "login" in driver.current_url:
        email_field = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "email")))
        email_field.clear()
        email_field.send_keys(TZ_EMAIL)
        pw = driver.find_element(By.NAME, "password")
        pw.clear()
        pw.send_keys(TZ_PASS)
        pw.send_keys(Keys.RETURN)
        time.sleep(7)

    driver.get(TZ_JOURNEYS_URL)
    time.sleep(6)

    token = get_tz_api_token(driver)
    if not token:
        raise RuntimeError("Could not capture Transferz API token.")

    now = datetime.now()
    future = now + timedelta(days=FETCH_DAYS_AHEAD)
    date_start = now.strftime("%Y-%m-%dT00:00:00")
    date_end = future.strftime("%Y-%m-%dT23:59:59")

    js = f"""
    const query = `query Journeys($params: JourneysRequestParams!, $skip: Boolean! = false) {{
      journeys(params: $params) @skip(if: $skip) {{
        results {{
          journeyCode inbound
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
        headers: {{ 'Content-Type': 'application/json', 'Authorization': '{token}' }},
        body: JSON.stringify({{ query: query, variables: variables }})
    }}).then(r => r.json()).then(d => callback(d)).catch(e => callback({{"error": e.toString()}}));
    """

    resp = driver.execute_async_script(js)
    rows = resp.get("data", {}).get("journeys", {}).get("results", []) if resp else []
    out: List[Ride] = []

    for r in rows:
        code = str(r.get("journeyCode") or "").strip()
        if not code:
            continue
        key = f"TRANSFERZ:{code}"
        if key in processed:
            continue

        t = r.get("travellerInfo") or {}
        j = r.get("journeyExecutionInfo") or {}
        origin = (r.get("originLocation") or {}).get("address") or {}
        dest = (r.get("destinationLocation") or {}).get("address") or {}
        drv = r.get("driver") or {}

        name = f"{t.get('firstName','')} {t.get('lastName','')}".strip().title() or "Unknown"
        pickup_addr = origin.get("originalAddress") or origin.get("formattedAddress") or "Unknown"
        dropoff_addr = dest.get("originalAddress") or dest.get("formattedAddress") or "Unknown"

        out.append(Ride(
            source="TRANSFERZ",
            supplier_id=code,
            pickup_dt_raw=str(j.get("pickupDate") or ""),
            name=name,
            phone=str(t.get("phone") or ""),
            pax=str(t.get("passengerCount") or "1"),
            luggage=int(t.get("luggageCount") or 0),
            flight=str(t.get("flightNumber") or ""),
            pickup_addr=pickup_addr,
            dropoff_addr=dropoff_addr,
            vehicle_raw=str(j.get("vehicleCategory") or "Standard"),
            driver_note=f"{code} | Driver: {drv.get('name','No Driver')} | {t.get('driverComments','')}",
            inbound_hint=bool(r.get("inbound", True))
        ))

    print(f"-> New Transferz rides: {len(out)}")
    return out

# =========================
# ACCOMMTRA
# =========================
def login_accommtra(driver, wait: WebDriverWait) -> None:
    driver.get(DEST_URL_LOGIN)
    time.sleep(4)
    if "login" not in driver.current_url.lower():
        return
    # username/email
    for sel in [(By.NAME,"username"),(By.NAME,"email"),(By.ID,"username"),(By.ID,"email")]:
        try:
            u = driver.find_element(*sel)
            u.clear()
            u.send_keys(DEST_EMAIL)
            break
        except:
            pass
    pw = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
    pw.clear()
    pw.send_keys(DEST_PASS)
    pw.send_keys(Keys.RETURN)
    time.sleep(7)

def is_inbound_from_addresses(pickup: str, dropoff: str) -> bool:
    pu = (pickup or "").upper()
    do = (dropoff or "").upper()
    if any(x in do for x in ["AIRPORT", "VACLA", "PRG"]):
        return False
    if any(x in pu for x in ["AIRPORT", "VACLA", "PRG"]):
        return True
    return True

def fp_exists_on_page(driver, fp: str) -> bool:
    return fp and (fp in (driver.page_source or ""))

def process_order(driver, wait: WebDriverWait, ride: Ride) -> bool:
    dt_obj = parse_dt(ride.pickup_dt_raw)
    if not dt_obj:
        print(f"❌ Date parse failed: {ride.pickup_dt_raw}")
        return False

    if dt_obj.tzinfo is not None:
        dt_obj = dt_obj.replace(tzinfo=None)

    is_inbound = is_inbound_from_addresses(ride.pickup_addr, ride.dropoff_addr)

    # Transferz rule: inbound gets -15 minutes
    if is_inbound:
        dt_obj = dt_obj - timedelta(minutes=15)

    dt_obj = round_time_to_nearest_5(dt_obj)

    date_formatted = dt_obj.strftime("%d.%m.%Y")
    time_formatted = dt_obj.strftime("%H:%M")
    url_date = dt_obj.strftime("%Y-%m-%d")

    veh_raw = (ride.vehicle_raw or "").upper()
    veh_val = "1"
    template_id = "205" if is_inbound else "207"
    if "BUSINESS" in veh_raw:
        veh_val = "18"
    elif any(x in veh_raw for x in ["MINIVAN","VAN","VITO","PEOPLE"]):
        veh_val = "2"
        template_id = "206" if is_inbound else "208"

    fp = ride_fingerprint(ride).replace("'", "")
    note_value = f"{ride.driver_note} | FP={fp}".replace("'", "")

    print(f"-> Filling: {ride.name} ({date_formatted} {time_formatted}) [TRANSFERZ]")
    driver.get(DEST_FORM_URL_BASE + url_date)

    wait.until(EC.presence_of_element_located((By.NAME, "firstname")))
    time.sleep(0.7)

    if ACCOMMTRA_FP_GUARD and fp_exists_on_page(driver, fp):
        print("   ⚠️ Duplicate FP detected on page; skipping save.")
        return False

    try:
        Select(driver.find_element(By.NAME, "OrderTemplate")).select_by_value(template_id)
        time.sleep(0.3)
        Select(driver.find_element(By.NAME, "Contractor")).select_by_value(CONTRACTOR_ID_TZ)
    except Exception as e:
        print(f"   ⚠️ Template/contractor select issue: {e}")

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
    force_fill(driver, "firstway__from", ride.pickup_addr)
    force_fill(driver, "firstway__to", ride.dropoff_addr)
    force_fill(driver, "firstway__flight", ride.flight if is_inbound else "")
    force_fill(driver, "firstway__price_1", DEFAULT_PRICE)
    force_fill(driver, "firstway__price_2", "")

    try:
        driver.execute_script("document.getElementById('firstway__driver_note').value = arguments[0];", note_value)
    except:
        pass

    try:
        driver.find_element(By.ID, "sendButton").click()
        time.sleep(1.8)
        print("   ✅ Order Saved.")
        return True
    except Exception as e:
        print(f"   ❌ Save failed: {e}")
        return False

def main():
    if not DEST_EMAIL or not DEST_PASS:
        raise RuntimeError("Missing Accommtra credentials.")

    driver = get_driver()
    wait = WebDriverWait(driver, 20)

    processed = load_processed()
    try:
        rides = fetch_transferz(driver, processed)
        if not rides:
            print("No new rides.")
            return

        print("\n--- ACCOMMTRA ---")
        login_accommtra(driver, wait)

        saved = 0
        for r in rides:
            key = f"TRANSFERZ:{r.supplier_id}"
            if key in processed:
                continue
            if process_order(driver, wait, r):
                append_processed("TRANSFERZ", r.supplier_id)
                processed.add(key)
                saved += 1

        print(f"\nDone. Saved: {saved}/{len(rides)}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
