import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Set

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# =========================
# CONFIG - KOI
# =========================
KOI_LOGIN_URL = "https://taxiportal.koiride.com/auth/sign-in"
KOI_ASSIGNED_URL = "https://taxiportal.koiride.com/ridesAssigned"
KOI_USER = "Haytham Montana"
KOI_PASS = "montana123"

# which days to pull (1=tomm, 2=day after)
KOI_FETCH_DAYS_AHEAD = [1, 2]

# =========================
# CONFIG - ACCOMMTRA
# =========================
DEST_URL_LOGIN = "https://driver.accommtra.com/login"
DEST_EMAIL = "haytham97@live.com"
DEST_PASS  = "tham97"
DEST_FORM_URL_BASE = "https://driver.accommtra.com/order-filter/item/date/"

CONTRACTOR_ID_KOI = "269"
CONTRACTOR_TEXT_KOI = "Koi Ride"  # fallback if ID breaks

DEFAULT_PRICE = "800"
MEMORY_FILE = "processed_koi.txt"

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
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
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
# MEMORY
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
# SELENIUM
# =========================
def get_driver() -> webdriver.Chrome:
    options = Options()
    prefs = {"profile.default_content_setting_values.notifications": 2}
    options.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    return driver

def force_fill(driver, name: str, value: str) -> None:
    try:
        el = driver.find_element(By.NAME, name)
        driver.execute_script("arguments[0].value = arguments[1];", el, value)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", el)
    except:
        pass

# =========================
# KOI FETCH
# =========================
def login_koi(driver, wait: WebDriverWait) -> None:
    driver.get(KOI_LOGIN_URL)
    time.sleep(4)
    if "auth" in driver.current_url:
        user_in = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']")))
        user_in.clear()
        user_in.send_keys(KOI_USER)
        pw = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pw.clear()
        pw.send_keys(KOI_PASS)
        pw.send_keys(Keys.RETURN)
        time.sleep(7)

def fetch_koi(driver, wait: WebDriverWait, processed: Set[str]) -> List[Ride]:
    print("\n--- KOI ---")
    login_koi(driver, wait)

    today = datetime.now()
    target_dates = [today + timedelta(days=d) for d in KOI_FETCH_DAYS_AHEAD]

    out: List[Ride] = []
    seen_ids = set()

    for target in target_dates:
        print(f"-> KOI date: {target.strftime('%Y-%m-%d')}")
        driver.get(KOI_ASSIGNED_URL)
        time.sleep(3.5)

        try:
            date_input = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[contains(@placeholder, 'Select date')]")))
            date_input.click()
            time.sleep(0.8)

            day_str = str(target.day)
            cells = driver.find_elements(By.XPATH, "//div[contains(@class, 'day') or contains(@class, 'cell')]")
            clicked = False
            for c in cells:
                if c.text.strip() == day_str:
                    try:
                        c.click()
                        clicked = True
                        break
                    except:
                        pass
            if not clicked:
                continue

            # select/confirm button (if exists)
            try:
                driver.find_element(By.XPATH, "//span[contains(text(), 'Select')] | //button[contains(text(), 'Select')]").click()
            except:
                pass
            time.sleep(0.6)

            # search
            try:
                driver.find_element(By.XPATH, "//div[contains(@class, 'btn-content') and contains(text(), 'Search')]").click()
            except:
                pass
            time.sleep(4)

            page_num = 1
            while True:
                rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                if not rows or "No results" in driver.page_source:
                    break

                print(f"   page {page_num} rows={len(rows)}")

                for row in rows:
                    cols = row.find_elements(By.TAG_NAME, "td")
                    if not cols:
                        continue

                    r_id = cols[0].text.strip()
                    if not r_id:
                        continue
                    key = f"KOI:{r_id}"
                    if key in processed or r_id in seen_ids:
                        continue
                    seen_ids.add(r_id)

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
                        if any(x in line.lower() for x in ["people carrier", "vito", "van"]):
                            vehicle = "Minivan"

                    pickup = cols[7].text.replace("\n", " ").strip() if len(cols) > 7 else "Unknown"
                    dropoff = cols[8].text.replace("\n", " ").strip() if len(cols) > 8 else "Unknown"
                    comment = cols[10].text.strip() if len(cols) > 10 else ""

                    out.append(Ride(
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
                        inbound_hint=True
                    ))

                # next page
                try:
                    next_btns = driver.find_elements(By.XPATH, "//li[not(contains(@class,'disabled'))]//button[contains(., 'Next')]")
                    moved = False
                    for btn in next_btns:
                        if btn.is_displayed():
                            driver.execute_script("arguments[0].click();", btn)
                            time.sleep(3.5)
                            page_num += 1
                            moved = True
                            break
                    if not moved:
                        break
                except:
                    break

        except Exception as e:
            print(f"⚠️ KOI date failed: {e}")
            continue

    print(f"-> New KOI rides: {len(out)}")
    return out

# =========================
# ACCOMMTRA
# =========================
def login_accommtra(driver, wait: WebDriverWait) -> None:
    driver.get(DEST_URL_LOGIN)
    time.sleep(4)
    if "login" not in driver.current_url.lower():
        return
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

    print(f"-> Filling: {ride.name} ({date_formatted} {time_formatted}) [KOI]")
    driver.get(DEST_FORM_URL_BASE + url_date)
    wait.until(EC.presence_of_element_located((By.NAME, "firstname")))
    time.sleep(0.7)

    if ACCOMMTRA_FP_GUARD and fp_exists_on_page(driver, fp):
        print("   ⚠️ Duplicate FP detected on page; skipping save.")
        return False

    # template + contractor
    try:
        Select(driver.find_element(By.NAME, "OrderTemplate")).select_by_value(template_id)
        time.sleep(0.3)
        dd = Select(driver.find_element(By.NAME, "Contractor"))
        if CONTRACTOR_ID_KOI:
            dd.select_by_value(CONTRACTOR_ID_KOI)
        else:
            dd.select_by_visible_text(CONTRACTOR_TEXT_KOI)
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
        rides = fetch_koi(driver, wait, processed)
        if not rides:
            print("No new rides.")
            return

        print("\n--- ACCOMMTRA ---")
        login_accommtra(driver, wait)

        saved = 0
        for r in rides:
            key = f"KOI:{r.supplier_id}"
            if key in processed:
                continue
            if process_order(driver, wait, r):
                append_processed("KOI", r.supplier_id)
                processed.add(key)
                saved += 1

        print(f"\nDone. Saved: {saved}/{len(rides)}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
