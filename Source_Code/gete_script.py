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
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager


# ============================================================
# CONFIG
# ============================================================

# GET-E
GETE_LOGIN_URL = "https://app.portal.get-e.com/rides"
GETE_API_URL = "https://portal.get-e.com/portal-api/trips"
GETE_EMAIL = "haytham97@live.com"
GETE_PASS  = "Stuntman1997!@"

# ACCOMMTRA
DEST_URL_LOGIN = "https://driver.accommtra.com/login"
DEST_FORM_URL_BASE = "https://driver.accommtra.com/order-filter/item/date/"
DEST_EMAIL = "haytham97@live.com"
DEST_PASS  = "tham97"

CONTRACTOR_TEXT_GETE = "GE CZ"
DEFAULT_PRICE = "800"

MEMORY_FILE = "processed_rides_gete.txt"

GETE_DEBUG = True
GETE_DEBUG_LIMIT = 10


# ============================================================
# DATA MODEL
# ============================================================

@dataclass(frozen=True)
class Ride:
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


# ============================================================
# UTILITIES
# ============================================================

def get_driver() -> webdriver.Chrome:
    options = Options()
    options.add_experimental_option("prefs", {
        "profile.default_content_setting_values.notifications": 2
    })
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.maximize_window()
    return driver


def selenium_cookies_dict(driver) -> Dict[str, str]:
    return {c["name"]: c["value"] for c in driver.get_cookies()}


def parse_dt(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        return dt
    except:
        return None


def round_time_5(dt: datetime) -> datetime:
    minutes = dt.hour * 60 + dt.minute
    rounded = 5 * round(minutes / 5)
    return dt.replace(
        hour=(rounded // 60) % 24,
        minute=rounded % 60,
        second=0
    )


def load_processed() -> Set[str]:
    if not os.path.exists(MEMORY_FILE):
        return set()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return {x.strip() for x in f.read().splitlines() if x.strip()}


def save_processed(rid: str):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"GETE:{rid}\n")


# ============================================================
# GET-E FETCH
# ============================================================

def gete_login(driver, wait):
    driver.get(GETE_LOGIN_URL)
    time.sleep(3)

    try:
        email = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']")))
        email.clear()
        email.send_keys(GETE_EMAIL)

        pw = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pw.clear()
        pw.send_keys(GETE_PASS)
        pw.send_keys(Keys.RETURN)

        wait.until(EC.url_contains("rides"))
        time.sleep(3)
    except:
        pass


def fetch_gete_api(driver, status: str) -> list:
    cookies = selenium_cookies_dict(driver)
    resp = requests.get(
        GETE_API_URL,
        params={"statusFilters[]": status},
        headers={
            "Accept": "application/json",
            "Origin": "https://app.portal.get-e.com",
            "Referer": "https://app.portal.get-e.com/",
        },
        cookies=cookies,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def normalize_gete_item(x: dict) -> Ride:
    rid = re.sub(r"\D+", "", x.get("unid") or x.get("prettifiedUnid") or "")

    pu = x.get("pickUp") or {}
    do = x.get("dropOff") or {}

    pickup_dt = pu.get("departAtLocal") or pu.get("departAt") or ""

    def loc(block):
        loc = block.get("location") or {}
        return f"{loc.get('name','')} {loc.get('address','')}".strip() or "See Notes"

    passengers = x.get("passengers") or [{}]
    p0 = passengers[0]

    vehicle = x.get("vehicle") or {}

    return Ride(
        supplier_id=rid,
        pickup_dt_raw=pickup_dt,
        name=f"{p0.get('firstName','')} {p0.get('lastName','')}".strip(),
        phone=p0.get("phone",""),
        pax=str(x.get("numberOfPassengers") or 1),
        luggage=int(x.get("numberOfBags") or 0),
        flight=str((x.get("flightDetails") or {}).get("number") or ""),
        pickup_addr=loc(pu),
        dropoff_addr=loc(do),
        vehicle_raw=str(vehicle.get("name") or "Standard"),
        driver_note=f"GETE-{rid}"
    )


# ============================================================
# ACCOMMTRA
# ============================================================

def login_accommtra(driver, wait):
    driver.get(DEST_URL_LOGIN)
    time.sleep(3)

    if "login" not in driver.current_url.lower():
        return

    user = wait.until(EC.presence_of_element_located((By.NAME, "username")))
    user.clear()
    user.send_keys(DEST_EMAIL)

    pw = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
    pw.clear()
    pw.send_keys(DEST_PASS)
    pw.send_keys(Keys.RETURN)

    time.sleep(5)


def ensure_form(driver, wait) -> bool:
    if "login" in driver.current_url.lower():
        return False

    try:
        wait.until(EC.presence_of_element_located((By.NAME, "firstname")))
        return True
    except:
        try:
            driver.find_element(By.ID, "tippw-folink").click()
            time.sleep(1)
            wait.until(EC.presence_of_element_located((By.NAME, "firstname")))
            return True
        except:
            driver.save_screenshot("accommtra_fail.png")
            return False


def process_order(driver, wait, r: Ride) -> bool:
    dt = parse_dt(r.pickup_dt_raw)
    if not dt:
        print("❌ Bad date:", r.pickup_dt_raw)
        return False

    dt = round_time_5(dt)

    date_str = dt.strftime("%d.%m.%Y")
    time_str = dt.strftime("%H:%M")
    url_date = dt.strftime("%Y-%m-%d")

    print(f"-> Filling: {r.name} ({date_str} {time_str})")

    driver.get(DEST_FORM_URL_BASE + url_date)
    if not ensure_form(driver, wait):
        return False

    Select(driver.find_element(By.NAME, "OrderTemplate")).select_by_value("205")
    Select(driver.find_element(By.NAME, "Contractor")).select_by_visible_text(CONTRACTOR_TEXT_GETE)

    driver.find_element(By.NAME, "firstname").send_keys(r.name)
    driver.find_element(By.NAME, "phone").send_keys(r.phone)
    driver.find_element(By.NAME, "firstway__persons").send_keys(r.pax)

    driver.find_element(By.NAME, "firstway__date").send_keys(date_str)
    driver.find_element(By.NAME, "firstway__time").send_keys(time_str)
    driver.find_element(By.NAME, "firstway__from").send_keys(r.pickup_addr)
    driver.find_element(By.NAME, "firstway__to").send_keys(r.dropoff_addr)
    driver.find_element(By.NAME, "firstway__price_1").send_keys(DEFAULT_PRICE)

    driver.execute_script(
        "document.getElementById('firstway__driver_note').value = arguments[0];",
        r.driver_note
    )

    driver.find_element(By.ID, "sendButton").click()
    time.sleep(2)
    return True


# ============================================================
# MAIN
# ============================================================

def main():
    driver = get_driver()
    wait = WebDriverWait(driver, 25)

    processed = load_processed()

    gete_login(driver, wait)

    rides: List[Ride] = []
    for status in ["TO_CONFIRM", "CONFIRMED"]:
        data = fetch_gete_api(driver, status)
        print(f"-> {status}: {len(data)} rides")

        for item in data:
            r = normalize_gete_item(item)
            if not r.supplier_id or f"GETE:{r.supplier_id}" in processed:
                continue
            rides.append(r)

    print(f"-> New GET-E rides: {len(rides)}")

    login_accommtra(driver, wait)

    saved = 0
    for r in rides:
        if process_order(driver, wait, r):
            save_processed(r.supplier_id)
            saved += 1

    print(f"\n🏁 Done. Saved {saved}/{len(rides)}")
    driver.quit()


if __name__ == "__main__":
    main()
