================================================================================
                        RideSyncBot V3 - USER GUIDE
================================================================================

OVERVIEW
--------
RideSyncBot V3 is an automated ride transfer management tool that syncs ride 
bookings from multiple sources (Transferz, Koi Ride, Get-e) to your Accommtra 
destination management system.

SYSTEM REQUIREMENTS
-------------------
- Windows 10 or later (64-bit)
- Internet connection
- Google Chrome browser (latest version recommended)
- Screen resolution: 1920x1080 or higher recommended

INSTALLATION
------------
No installation required! RideSyncBot_V3.exe is a standalone executable.

Simply:
1. Place RideSyncBot_V3.exe in any folder on your computer
2. Double-click to run

FIRST-TIME SETUP
----------------
Before running the bot, ensure you have:

1. Valid login credentials for:
   - Transferz account
   - Koi Ride account  
   - Get-e account
   - Accommtra account

2. Chrome browser installed and updated to the latest version

3. The processed_rides.txt file in the same folder as the executable
   (This file tracks which rides have already been processed)

HOW TO USE
----------
1. Double-click RideSyncBot_V3.exe to launch the application

2. The bot will automatically:
   - Open Chrome browser
   - Log into each ride source platform
   - Scrape new ride bookings
   - Log into Accommtra
   - Fill in ride details and submit

3. Monitor the console window for progress updates:
   - "Processing Transferz rides..."
   - "Processing Koi Ride bookings..."
   - "Processing GET-E bookings..."
   - "Successfully submitted ride [ID]"

4. Do NOT close the browser window while the bot is running

5. When complete, you'll see: "All rides processed successfully!"

IMPORTANT FILES
---------------
- RideSyncBot_V3.exe: Main executable (this file)
- processed_rides.txt: Tracking file for completed rides
- warn-RideSyncBot_V3.txt: Build warnings (can be ignored)

WHAT GETS AUTOMATED
-------------------
✓ Transferz GraphQL API data extraction
✓ Koi Ride web scraping (date-based table)
✓ Get-e web scraping (Smart Scrape 2.0 with text fallbacks)
✓ Automatic form filling in Accommtra
✓ Passenger name formatting (proper capitalization)
✓ Flight number extraction and validation
✓ Service class mapping (Executive → Business)
✓ Duplicate ride prevention

DATA SOURCES
------------
1. TRANSFERZ
   - Method: GraphQL API
   - Data: Reference, passenger name, pickup/dropoff, dates, flight info

2. KOI RIDE  
   - Method: Web scraping (date-based table)
   - Data: Reference, passenger, locations, times

3. GET-E
   - Method: Smart Scrape 2.0 (text-based with fallbacks)
   - Data: Booking reference, passenger, addresses, dates, flight details

DESTINATION
-----------
- ACCOMMTRA: All rides submitted via automated form filling

FEATURES
--------
→ Multi-source aggregation (3 platforms)
→ Intelligent duplicate detection
→ Automatic retry logic for failed submissions
→ Date/time parsing with multiple format support
→ Flight number regex extraction
→ Address preference (original over formatted)
→ Reset filters functionality for clean scraping
→ Comprehensive error logging

TROUBLESHOOTING
---------------
Problem: "Chrome driver not found"
Solution: Ensure Chrome browser is installed and updated

Problem: Login fails
Solution: Verify your credentials are correct for each platform

Problem: Rides not syncing
Solution: Check processed_rides.txt - may need to clear old entries

Problem: Browser doesn't close
Solution: Manually close Chrome if bot exits unexpectedly

Problem: "No new rides found"
Solution: Normal - means all current rides are already processed

Problem: Executable won't start
Solution: 
- Right-click → Run as Administrator
- Check Windows Defender didn't quarantine the file
- Ensure you have extracted from ZIP (don't run from archive)

SECURITY & PRIVACY
------------------
⚠ IMPORTANT: This executable contains your automation script but does NOT 
store any login credentials. You will need to enter credentials when prompted.

⚠ Keep your processed_rides.txt file backed up to prevent re-processing old rides

⚠ Do not share this executable with unauthorized users

PERFORMANCE TIPS
----------------
→ Run during off-peak hours for faster scraping
→ Close unnecessary browser tabs before running
→ Ensure stable internet connection
→ Don't move mouse/keyboard excessively during automation

LOGGING
-------
The bot creates detailed logs in the console window showing:
- Source being processed
- Number of rides found
- Submission status for each ride
- Any errors encountered

Keep the console window open to monitor progress!

LIMITATIONS
-----------
- Requires active internet connection
- Chrome browser must remain open during execution
- Cannot process rides that violate source platform rules
- Duplicate detection based on reference number + passenger name

UPDATES & MAINTENANCE
---------------------
Version: 3.0 (Final - Smart Scrape 2.0)
Build Date: December 2025
Python: 3.11.9
Selenium: Latest via webdriver-manager

To update:
- Replace old executable with new version
- Keep processed_rides.txt in the same folder

SUPPORT
-------
For issues or questions:
1. Check the troubleshooting section above
2. Review console output for specific error messages
3. Verify all source platforms are accessible via browser
4. Ensure Chrome is updated to latest version

LEGAL NOTICE
------------
This tool is for authorized use only. Ensure you have permission to automate 
data extraction from all configured platforms. Respect platform terms of service 
and rate limits.

================================================================================
                    © 2025 RideSyncBot V3 - Smart Scrape 2.0
================================================================================
