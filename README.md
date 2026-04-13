<div align="center">

# 🚗 RideSync Bot

### Automated Ride Transfer System

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Selenium](https://img.shields.io/badge/Selenium-Automation-43B02A?style=for-the-badge&logo=selenium&logoColor=white)](https://selenium.dev)
[![Status](https://img.shields.io/badge/Status-Active-success?style=for-the-badge)]()

_Seamlessly synchronize ride orders from Transferz & Koi Ride into Accommtra_

</div>

---

## 📋 Overview

**RideSync Bot** is a powerful **ETL (Extract, Transform, Load)** automation tool designed to bridge multiple ride logistics platforms into a single unified dispatch system.

<div align="center">

```
┌─────────────┐     ┌─────────────┐
│  Transferz  │     │  Koi Ride   │
│   (50 max)  │     │  (2 days)   │
└──────┬──────┘     └──────┬──────┘
       │                   │
       └─────────┬─────────┘
                 ▼
        ┌────────────────┐
        │  🔄 Transform  │
        │  Map Vehicles  │
        │  Format Dates  │
        └────────┬───────┘
                 ▼
        ┌────────────────┐
        │   Accommtra    │
        │   Dashboard    │
        └────────────────┘
```

</div>

### ✨ Key Features

| Feature                      | Description                                    |
| ---------------------------- | ---------------------------------------------- |
| 🔄 **Dual-Source Sync**      | Pulls from Transferz & Koi Ride simultaneously |
| 🚫 **Duplicate Prevention**  | Tracks processed rides to avoid re-entries     |
| 🚗 **Smart Vehicle Mapping** | Auto-maps vehicle categories across platforms  |
| 📅 **Date Normalization**    | Rounds times to nearest 5 minutes              |
| 🔐 **Auto-Login**            | Handles authentication for all three portals   |

---

## ⚙️ Configuration

> **📍 Important:** All settings are at the top of `main.py` for easy updates.

### 🏢 Contractor IDs

| Platform  | ID    | Label |
| --------- | ----- | ----- |
| Transferz | `227` | TZ    |
| Koi Ride  | `269` | KO    |

### 💰 Pricing

```python
DEFAULT_PRICE = "800"  # Fixed price for all rides
```

---

## ▶️ Quick Start

<table>
<tr>
<td width="60">

### 1️⃣

</td>
<td>

**Preparation**  
Ensure you have an active internet connection

</td>
</tr>
<tr>
<td>

### 2️⃣

</td>
<td>

**Launch**  
Double-click `RideSyncBot_v4.exe`

</td>
</tr>
<tr>
<td>

### 3️⃣

</td>
<td>

**Operation**  
A console window and Chrome browser will open

</td>
</tr>
<tr>
<td>

### 4️⃣

</td>
<td>

**Complete**  
Wait for `🏁 Done` message in console

</td>
</tr>
</table>

> ⚠️ **Important During Operation:**
>
> - ❌ Do NOT close the Chrome window
> - ❌ Do NOT click inside Chrome while the bot is typing
> - ✅ Let the bot run uninterrupted

---

## 📁 Project Structure

```
📦 Transfers_Automation
 ┣ 📜 RideSyncBot_v4.exe      # Main executable
 ┣ 📜 main.py                 # Source code
 ┣ 📜 processed_rides.txt     # Duplicate tracker ⚠️ Don't delete!
 ┗ 📜 README.md               # This file
```

---

## 🔧 Troubleshooting

<details>
<summary><b>🛡️ "Windows protected your PC" Warning</b></summary>

Since this is a private internal tool, Windows SmartScreen may flag it on first run.

**Solution:**

1. Click **"More Info"**
2. Click **"Run Anyway"**

</details>

<details>
<summary><b>🔐 Login Failed</b></summary>

If Accommtra changes their login page, the bot will wait 60 seconds.

**Solution:**

1. Manually click the password box
2. Type the password
3. Press Enter
4. The bot will detect the login and resume automatically

</details>

<details>
<summary><b>⏳ Bot stops at "Search"</b></summary>

If the internet is slow, the "Search" button on Koi Ride might not load in time.

**Solution:**

1. Close the bot
2. Run it again
3. It will skip already processed rides and pick up where it left off

</details>

---

## 🛠️ Technical Details

| Component          | Technology         |
| ------------------ | ------------------ |
| Language           | Python 3.13+       |
| Browser Automation | Selenium WebDriver |
| Driver Management  | webdriver-manager  |
| Packaging          | PyInstaller        |

---

<div align="center">

### 🚀 Built for Efficiency

_Automating the tedious, so you can focus on what matters_

---

Made with ❤️ for seamless ride management

</div>
# Ride_Automation_Tool_v4
