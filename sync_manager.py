import gspread
import json
import os

def load_combined_profile():
    """
    Tries to fetch fresh data from Google Sheets.
    Falls back to nascent_profile.json if connection fails.
    """
    local_file = 'nascent_profile.json'
    
    try:
        # 1. Connect to Google Sheets using the Secret File on Render
        # Make sure you uploaded 'google_key.json' to Render Secrets!
        gc = gspread.service_account(filename='google_key.json')
        
        # 2. Open your specific Master Sheet
        sh = gc.open_by_key("1lgq44BOQaOpQFuAb-yyPcEGfQME1Wa6jvJKMg1_C_Y8")
        
        # 3. Pull the data from each tab
        live_data = {
            "identity": sh.worksheet("Firm_Identity").get_all_records(),
            "financials": sh.worksheet("Financial_Credentials").get_all_records(),
            "projects": sh.worksheet("Project_Experience").get_all_records(),
            "bid_rules": sh.worksheet("Bid_Rules").get_all_records()
        }
        
        print("✅ Sync Successful: Using Live data from Google Sheets.")
        return live_data

    except Exception as e:
        print(f"⚠️ Sync Failed ({e}). Using local backup: {local_file}")
        
        # Fallback to your old JSON file
        if os.path.exists(local_file):
            with open(local_file, 'r') as f:
                return json.load(f)
        else:
            return {"error": "No profile data found anywhere."}
