import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ============================================================
# 0) ENV CONFIG
# ============================================================

# def env(name: str, default: str = "") -> str:
#     return os.getenv(name, default).strip()

# --- TRANSFERZ ---
TZ_LOGIN_URL = "https://rides.transferz.com/login"
TZ_JOURNEYS_URL = "https://rides.transferz.com/journeys"
TZ_EMAIL = "haytham97@live.com"
TZ_PASS  = "ZEuoHFzP78cp"
TZ_COMPANY_ID = 3843

# --- KOI RIDE ---
KOI_LOGIN_URL = "https://taxiportal.koiride.com/auth/sign-in"
KOI_ASSIGNED_URL = "https://taxiportal.koiride.com/ridesAssigned"
KOI_USER = "Haytham Montana"
KOI_PASS = "montana123"

# --- GET-E (API) ---
GETE_LOGIN_URL = "https://app.portal.get-e.com/rides"
GETE_EMAIL = "haytham97@live.com"
GETE_PASS  = "Stuntman1997!@"
GETE_API_URL = "https://portal.get-e.com/portal-api/trips"  # confirmed by your Network tab

# --- ACCOMMTRA ---
DEST_URL_LOGIN = "https://driver.accommtra.com/login"
DEST_EMAIL = "haytham97@live.com"
DEST_PASS  = "tham97"
DEST_FORM_URL_BASE = "https://driver.accommtra.com/order-filter/item/date/"

# --- CONTRACTORS ---
CONTRACTOR_ID_TZ   = "227"     # Transferz
CONTRACTOR_ID_KOI  = "269"     # Koi Ride
CONTRACTOR_TEXT_KOI = "Koi Ride" 
CONTRACTOR_TEXT_GETE = "GE CZ" # Get-e (Select by Text)

# SETTINGS
DEFAULT_PRICE = "800" 
MEMORY_FILE = "processed_rides.txt"
KOI_FETCH_DAYS_AHEAD = [1, 2]


GETE_DEBUG = True
GETE_DEBUG_LIMIT = 15

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
    junk = {"street","st","road","rd","avenue","ave","building","bldg","apt","apartment","floor","fl","the"}
    parts = [p for p in s.split() if p not in junk]
    return " ".join(parts)

def parse_dt(raw_date: str) -> Optional[datetime]:
    if not raw_date:
        return None
    s = raw_date.strip()

    # Handle ISO 8601 with timezone like: 2025-12-14T13:25:00+00:00
    # datetime.fromisoformat supports "+00:00" but not "Z" unless replaced.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except:
        pass

    fmts = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:00",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        # If some strings arrive without colon in offset (rare):
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    for f in fmts:
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

def supplier_key(r: Ride) -> str:
    return f"{r.source}:{(r.supplier_id or '').strip()}"

def global_dedup(rides: List[Ride]) -> List[Ride]:
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


# ============================================================
# 2) NAMESPACED MEMORY
# ============================================================

def _parse_memory_line(line: str) -> Tuple[Optional[str], str]:
    """
    Returns (source_or_none, id)
    Accepts:
      - "GETE:3908397326"
      - "TRANSFERZ:T3ZT56-1"
      - old style: "3908397326" -> (None, "3908397326")
    """
    line = (line or "").strip()
    if not line:
        return (None, "")
    if ":" in line:
        src, rid = line.split(":", 1)
        return (src.strip().upper(), rid.strip())
    return (None, line.strip())

def load_processed() -> Tuple[Set[str], Set[str]]:
    """
    Returns:
      - processed_namespaced: set of "SOURCE:ID"
      - processed_legacy_ids: set of raw ids from old file lines (no source)
    """
    if not os.path.exists(MEMORY_FILE):
        return set(), set()

    namespaced: Set[str] = set()
    legacy: Set[str] = set()

    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        for raw in f.read().splitlines():
            src, rid = _parse_memory_line(raw)
            if not rid:
                continue
            if src:
                namespaced.add(f"{src}:{rid}")
            else:
                legacy.add(rid)
    return namespaced, legacy

def is_processed(source: str, rid: str, namespaced: Set[str], legacy: Set[str]) -> bool:
    """
    Backward compatible:
      - If old file contains raw rid, we treat it as processed for ANY source.
      - New writes are always namespaced.
    """
    if not rid:
        return False
    src = (source or "").upper()
    if f"{src}:{rid}" in namespaced:
        return True
    if rid in legacy:
        return True
    return False

def append_processed(source: str, rid: str) -> None:
    if not source or not rid:
        return
    line = f"{source.upper()}:{rid}".strip()
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

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

def get_driver() -> webdriver.Chrome:
    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    prefs = {"profile.default_content_setting_values.notifications": 2}
    options.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.maximize_window()
    return driver

def check_logout_screen(driver) -> bool:
    try:
        buttons = driver.find_elements(By.XPATH, "//span[contains(@class, 'button-title') and contains(text(), 'Click here')]")
        if buttons and buttons[0].is_displayed():
            buttons[0].click()
            time.sleep(5)
            return True
    except:
        pass
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

def force_fill(driver, element_name: str, value: str) -> bool:
    try:
        el = driver.find_element(By.NAME, element_name)
        driver.execute_script("arguments[0].value = arguments[1];", el, value)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", el)
        return True
    except:
        return False

def selenium_cookies_dict(driver) -> Dict[str, str]:
    cookies = {}
    for c in driver.get_cookies():
        cookies[c["name"]] = c["value"]
    return cookies


# ============================================================
# 4) SOURCE A: TRANSFERZ
# ============================================================

def fetch_transferz_rides(driver, processed_ns: Set[str], processed_legacy: Set[str]) -> List[Ride]:
    print("\n--- SOURCE A: TRANSFERZ ---")
    if not TZ_EMAIL or not TZ_PASS:
        print("   ❌ Missing TZ_EMAIL / TZ_PASS. Skipping Transferz.")
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
        print("   ❌ Could not capture Transferz API token. Skipping.")
        return []

    now = datetime.now()
    future = now + timedelta(days=90)
    date_start = now.strftime("%Y-%m-%dT00:00:00")
    date_end = future.strftime("%Y-%m-%dT23:59:59")

    js_script = f"""
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
        headers: {{ 'Content-Type': 'application/json', 'Authorization': '{access_token}' }},
        body: JSON.stringify({{ query: query, variables: variables }})
    }}).then(r => r.json()).then(d => callback(d)).catch(e => callback({{"error": e.toString()}}));
    """

    response = driver.execute_async_script(js_script)
    results = response.get("data", {}).get("journeys", {}).get("results", []) if response else []
    rides: List[Ride] = []
    seen: Set[str] = set()

    for r in results:
        journey_code = str(r.get("journeyCode") or "").strip()
        if not journey_code:
            continue

        if is_processed("TRANSFERZ", journey_code, processed_ns, processed_legacy):
            continue
        if journey_code in seen:
            continue
        seen.add(journey_code)

        t = r.get("travellerInfo") or {}
        j = r.get("journeyExecutionInfo") or {}
        origin = (r.get("originLocation") or {}).get("address") or {}
        dest = (r.get("destinationLocation") or {}).get("address") or {}
        drv = r.get("driver") or {}

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
            driver_note=f"{journey_code} | Driver: {drv.get('name','No Driver')} | {t.get('driverComments','')}",
            inbound_hint=bool(r.get("inbound", True))
        ))

    print(f"   -> Transferz new rides: {len(rides)}")
    return rides


# ============================================================
# 5) SOURCE B: KOI RIDE
# ============================================================

def fetch_koi_rides(driver, wait: WebDriverWait, processed_ns: Set[str], processed_legacy: Set[str]) -> List[Ride]:
    print("\n--- SOURCE B: KOI RIDE ---")
    if not KOI_USER or not KOI_PASS:
        print("   ❌ Missing KOI_USER / KOI_PASS. Skipping Koi.")
        return []

    driver.get(KOI_LOGIN_URL)
    time.sleep(5)

    try:
        if "auth" in driver.current_url:
            user_in = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']")))
            user_in.clear()
            user_in.send_keys(KOI_USER)
            pw = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            pw.clear()
            pw.send_keys(KOI_PASS)
            pw.send_keys(Keys.RETURN)
            time.sleep(8)
    except Exception as e:
        print(f"   ⚠️ Koi login issue: {e}")

    today = datetime.now()
    target_dates = [today + timedelta(days=d) for d in KOI_FETCH_DAYS_AHEAD]

    all_rides: List[Ride] = []
    seen: Set[str] = set()

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

            try:
                driver.find_element(By.XPATH, "//span[contains(text(), 'Select')] | //button[contains(text(), 'Select')]").click()
            except:
                pass
            time.sleep(1)

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

                print(f"      -> Scraping KOI page {page_num} ({len(rows)} rows)")
                for row in rows:
                    try:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if not cols:
                            continue

                        r_id = cols[0].text.strip()
                        if not r_id:
                            continue
                        if is_processed("KOI", r_id, processed_ns, processed_legacy):
                            continue
                        if r_id in seen:
                            continue
                        seen.add(r_id)

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
                            inbound_hint=True
                        ))
                    except:
                        continue

                # next page
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
# 6) SOURCE C: GET-E (API MODE)
# ============================================================

def gete_login_if_needed(driver, wait: WebDriverWait) -> None:
    driver.get(GETE_LOGIN_URL)
    time.sleep(3)

    if not GETE_EMAIL or not GETE_PASS:
        return

    try:
        email_field = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
        )
        email_field.clear()
        email_field.send_keys(GETE_EMAIL)
        pw = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pw.clear()
        pw.send_keys(GETE_PASS)
        try:
            driver.find_element(By.XPATH, "//button[contains(., 'Sign in') or contains(., 'Log in')]").click()
        except:
            pw.send_keys(Keys.RETURN)
        WebDriverWait(driver, 20).until(EC.url_contains("rides"))
        time.sleep(3)
    except:
        # already logged in
        pass

def fetch_gete_api(driver, status: str) -> list:
    cookies = selenium_cookies_dict(driver)
    params = {"query": "", "statusFilters[]": status}
    headers = {
        "Accept": "application/json",
        "Origin": "https://app.portal.get-e.com",
        "Referer": "https://app.portal.get-e.com/",
        "User-Agent": "Mozilla/5.0",
    }
    resp = requests.get(GETE_API_URL, params=params, headers=headers, cookies=cookies, timeout=30)
    resp.raise_for_status()
    return resp.json()

def _gete_extract_id_and_name(item: dict) -> Tuple[str, str]:
    rid = str(item.get("unid") or item.get("prettifiedUnid") or "").strip()

    name = ""
    passengers = item.get("passengers") or []
    if isinstance(passengers, list) and passengers:
        p0 = passengers[0] or {}
        fn = str(p0.get("firstName") or "").strip()
        ln = str(p0.get("lastName") or "").strip()
        name = f"{fn} {ln}".strip()

    return rid, name


def _gete_extract_pickup(item: dict) -> str:
    pu = item.get("pickUp") or {}
    if isinstance(pu, dict):
        return str(pu.get("departAtLocal") or pu.get("departAt") or "").strip()
    return ""


def normalize_gete_item(x: dict) -> Ride:
    rid = str(x.get("unid") or x.get("prettifiedUnid") or "").strip()

    pu = x.get("pickUp") or {}
    do = x.get("dropOff") or {}

    pickup_dt = ""
    if isinstance(pu, dict):
        pickup_dt = str(pu.get("departAtLocal") or pu.get("departAt") or "").strip()

    def fmt_loc(block: dict) -> str:
        if not isinstance(block, dict):
            return "See Notes"
        loc = block.get("location") or {}
        if not isinstance(loc, dict):
            return "See Notes"
        name = str(loc.get("name") or "").strip()
        addr = str(loc.get("address") or "").strip()
        out = f"{name} {addr}".strip()
        return out if out else "See Notes"

    pickup_addr = fmt_loc(pu)
    dropoff_addr = fmt_loc(do)

    pax = str(x.get("numberOfPassengers") or 1)
    bags = int(x.get("numberOfBags") or 0)

    # passenger name / phone
    name = "Unknown"
    phone = ""
    passengers = x.get("passengers") or []
    if isinstance(passengers, list) and passengers:
        p0 = passengers[0] or {}
        fn = str(p0.get("firstName") or "").strip()
        ln = str(p0.get("lastName") or "").strip()
        name = f"{fn} {ln}".strip() or "Unknown"
        phone = str(p0.get("phone") or "").strip()

    fd = x.get("flightDetails") or {}
    flight = ""
    if isinstance(fd, dict):
        flight = str(fd.get("number") or "").strip()

    veh = x.get("vehicle") or {}
    vehicle_raw = "Standard"
    if isinstance(veh, dict):
        vehicle_raw = str(veh.get("name") or veh.get("type") or veh.get("class") or "Standard").strip()

    return Ride(
        source="GETE",
        supplier_id=rid if rid else "UNKNOWN_GETE",
        pickup_dt_raw=pickup_dt,
        name=name,
        phone=phone,
        pax=pax,
        luggage=bags,
        flight=flight,
        pickup_addr=pickup_addr,
        dropoff_addr=dropoff_addr,
        vehicle_raw=vehicle_raw,
        driver_note=f"GETE-{rid}",
        inbound_hint=True
    )

def fetch_gete_rides_api(driver, wait: WebDriverWait, processed_ns: Set[str], processed_legacy: Set[str]) -> List[Ride]:
    print("\n--- SOURCE C: GET-E (API MODE) ---")
    if not GETE_EMAIL or not GETE_PASS:
        print("   ❌ Missing GETE_EMAIL / GETE_PASS. Skipping Get-e.")
        return []

    gete_login_if_needed(driver, wait)

    all_rides: List[Ride] = []
    seen: Set[str] = set()

    for status in ["TO_CONFIRM", "CONFIRMED"]:
        data = fetch_gete_api(driver, status)
        print(f"   -> {status}: {len(data)} rides from API")

        # Debug: show a small sample of raw payload fields
        if GETE_DEBUG and isinstance(data, list) and data:
            try:
                keys = sorted(list((data[0] or {}).keys()))
                keys_preview = keys[:25]
                extra = "" if len(keys) <= 25 else f" (+{len(keys)-25} more)"
                print(f"      [GETE_DEBUG] sample keys: {keys_preview}{extra}")
            except Exception as e:
                print(f"      [GETE_DEBUG] could not read sample keys: {e}")

        # Counters to explain why we end up with 0 new rides
        added = 0
        skipped_unknown = 0
        skipped_processed = 0
        skipped_seen = 0
        normalize_errors = 0

        for item in data:
            raw_dict = item if isinstance(item, dict) else {}
            raw_id, raw_name = _gete_extract_id_and_name(raw_dict)
            raw_pickup = _gete_extract_pickup(raw_dict)
            try:
                r = normalize_gete_item(item)
                rid = r.supplier_id
                if not rid or rid.startswith("UNKNOWN"):
                    skipped_unknown += 1
                    if GETE_DEBUG and (skipped_unknown + skipped_processed + skipped_seen + added + normalize_errors) <= GETE_DEBUG_LIMIT:
                        print(f"      [GETE_DEBUG:{status}] SKIP unknown id | raw_id='{raw_id}' name='{raw_name}' pickup='{raw_pickup}'")
                    continue
                if is_processed("GETE", rid, processed_ns, processed_legacy):
                    skipped_processed += 1
                    if GETE_DEBUG and (skipped_unknown + skipped_processed + skipped_seen + added + normalize_errors) <= GETE_DEBUG_LIMIT:
                        print(f"      [GETE_DEBUG:{status}] SKIP processed | id='{rid}' name='{r.name}' pickup='{r.pickup_dt_raw}'")
                    continue
                if rid in seen:
                    skipped_seen += 1
                    if GETE_DEBUG and (skipped_unknown + skipped_processed + skipped_seen + added + normalize_errors) <= GETE_DEBUG_LIMIT:
                        print(f"      [GETE_DEBUG:{status}] SKIP duplicate-in-run | id='{rid}' name='{r.name}'")
                    continue
                seen.add(rid)
                all_rides.append(r)
                added += 1
                if GETE_DEBUG and added <= GETE_DEBUG_LIMIT:
                    print(f"      [GETE_DEBUG:{status}] ADD | id='{rid}' name='{r.name}' pickup='{r.pickup_dt_raw}'")
            except Exception:
                normalize_errors += 1
                if GETE_DEBUG and normalize_errors <= GETE_DEBUG_LIMIT:
                    try:
                        raw_id, raw_name = _gete_extract_id_and_name(item if isinstance(item, dict) else {})
                        raw_pickup = _gete_extract_pickup(item if isinstance(item, dict) else {})
                        print(f"      [GETE_DEBUG:{status}] ERROR normalizing | raw_id='{raw_id}' name='{raw_name}' pickup='{raw_pickup}'")
                    except Exception:
                        print(f"      [GETE_DEBUG:{status}] ERROR normalizing | (raw item unreadable)")
                continue

        if GETE_DEBUG:
            print(
                f"      [GETE_DEBUG:{status}] summary: added={added}, "
                f"skipped_unknown={skipped_unknown}, skipped_processed={skipped_processed}, "
                f"skipped_seen={skipped_seen}, normalize_errors={normalize_errors}"
            )

    print(f"   -> Get-e new rides: {len(all_rides)}")
    return all_rides


# ============================================================
# 7) ACCOMMTRA ENTRY
# ============================================================

def is_inbound_from_addresses(pickup: str, dropoff: str) -> bool:
    pu = (pickup or "").upper()
    do = (dropoff or "").upper()
    if any(x in do for x in ["AIRPORT", "VACLA", "PRG"]):
        return False
    if any(x in pu for x in ["AIRPORT", "VACLA", "PRG"]):
        return True
    return True

def login_accommtra(driver, wait: WebDriverWait) -> None:
    driver.get(DEST_URL_LOGIN)
    time.sleep(5)

    if "login" not in driver.current_url.lower():
        return

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
        pw = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pw.clear()
        pw.send_keys(DEST_PASS)
        pw.send_keys(Keys.RETURN)
        time.sleep(8)
    except:
        print("   ❌ Accommtra password field not found. Manual login needed.")
        time.sleep(60)

def process_single_order(driver, wait: WebDriverWait, ride: Ride) -> bool:
    dt_obj = parse_dt(ride.pickup_dt_raw)
    if not dt_obj:
        print(f"   ❌ Date parse failed: {ride.pickup_dt_raw}")
        return False

    # Normalize aware -> naive (keeps the same clock time you got from API)
    if dt_obj.tzinfo is not None:
        dt_obj = dt_obj.replace(tzinfo=None)


    pickup = ride.pickup_addr
    dropoff = ride.dropoff_addr
    is_inbound = is_inbound_from_addresses(pickup, dropoff)

    if ride.source == "TRANSFERZ" and is_inbound:
        dt_obj = dt_obj - timedelta(minutes=15)

    dt_obj = round_time_to_nearest_5(dt_obj)

    date_formatted = dt_obj.strftime("%d.%m.%Y")
    time_formatted = dt_obj.strftime("%H:%M")
    url_date = dt_obj.strftime("%Y-%m-%d")

    contractor_mode = "VALUE"
    contractor_value = CONTRACTOR_ID_TZ
    if ride.source == "KOI":
        contractor_value = CONTRACTOR_ID_KOI
        if not contractor_value or contractor_value == "FIX_ME":
            contractor_mode = "TEXT"
    elif ride.source == "GETE":
        contractor_mode = "TEXT_GETE"

    veh_raw = (ride.vehicle_raw or "").upper()
    veh_val = "1"
    template_id = "205" if is_inbound else "207"

    if "BUSINESS" in veh_raw:
        veh_val = "18"
        template_id = "205" if is_inbound else "207"
    elif any(x in veh_raw for x in ["MINIVAN", "VAN", "VITO", "PEOPLE"]):
        veh_val = "2"
        template_id = "206" if is_inbound else "208"

    print(f"   -> Filling: {ride.name} ({date_formatted} {time_formatted}) [{ride.source}]")
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
        time.sleep(0.5)
        contractor_dropdown = Select(driver.find_element(By.NAME, "Contractor"))
        if contractor_mode == "TEXT":
            contractor_dropdown.select_by_visible_text(CONTRACTOR_TEXT_KOI)
        elif contractor_mode == "TEXT_GETE":
            contractor_dropdown.select_by_visible_text(CONTRACTOR_TEXT_GETE)
        else:
            contractor_dropdown.select_by_value(contractor_value)
        time.sleep(0.5)
    except Exception as e:
        print(f"      ⚠️ Template/contractor select issue: {e}")

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

    fp = ride_fingerprint(ride).replace("'", "")
    note = (ride.driver_note or "").replace("'", "")
    note_value = f"{note} | FP={fp}"

    try:
        driver.execute_script("document.getElementById('firstway__driver_note').value = arguments[0];", note_value)
    except:
        pass

    try:
        driver.find_element(By.ID, "sendButton").click()
        time.sleep(2)
        print("      ✅ Order Saved.")
        return True
    except Exception as e:
        print(f"      ❌ Save failed: {e}")
        return False


# ============================================================
# 8) MAIN
# ============================================================

def sanity_check_env():
    if not DEST_EMAIL or not DEST_PASS:
        raise RuntimeError("Missing DEST_EMAIL / DEST_PASS. Cannot submit to Accommtra.")

def run_bot():
    sanity_check_env()
    print("🚀 Starting FINAL TRI-SOURCE Bot (Get-e API + Namespaced Memory + Dedup)")

    driver = get_driver()
    wait = WebDriverWait(driver, 20)

    processed_ns, processed_legacy = load_processed()
    processed_in_run: Set[str] = set(processed_ns)  # namespaced keys like "GETE:123"

    all_rides: List[Ride] = []

    try:
        all_rides.extend(fetch_transferz_rides(driver, processed_ns, processed_legacy))
    except Exception as e:
        print(f"❌ Transferz error: {e}")

    try:
        all_rides.extend(fetch_koi_rides(driver, wait, processed_ns, processed_legacy))
    except Exception as e:
        print(f"❌ KOI error: {e}")

    try:
        all_rides.extend(fetch_gete_rides_api(driver, wait, processed_ns, processed_legacy))
    except Exception as e:
        print(f"❌ Get-e API error: {e}")

    print(f"\n👉 Raw collected rides: {len(all_rides)}")
    all_rides = global_dedup(all_rides)
    print(f"👉 After global dedup: {len(all_rides)}")

    if not all_rides:
        print("🏁 No new rides. Exiting.")
        driver.quit()
        return

    print("\n--- DESTINATION: ACCOMMTRA ---")
    login_accommtra(driver, wait)

    saved = 0
    for i, ride in enumerate(all_rides, 1):
        print(f"\n--- Processing {i}/{len(all_rides)} ---")

        ns_key = f"{ride.source.upper()}:{ride.supplier_id}"
        # same-run protection
        if ns_key in processed_in_run:
            print("   ⚠️ Already processed in this run. Skipping.")
            continue

        ok = process_single_order(driver, wait, ride)
        if ok:
            saved += 1
            append_processed(ride.source, ride.supplier_id)
            processed_in_run.add(ns_key)

    clean_memory()
    print(f"\n🏁 Done. Saved: {saved}/{len(all_rides)}")
    driver.quit()

if __name__ == "__main__":
    run_bot()
