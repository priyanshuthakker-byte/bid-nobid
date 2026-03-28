import gspread

def get_live_master_data():
    # Connect using the secret file on Render
    gc = gspread.service_account(filename='google_key.json')
    
    # Open your Nascent Master Sheet by its ID
    sh = gc.open_by_key("1lgq44BOQaOpQFuAb-yyPcEGfQME1Wa6jvJKMg1_C_Y8")
    
    # Fetch all key tabs
    profile = {
        "identity": sh.worksheet("Firm_Identity").get_all_records(),
        "financials": sh.worksheet("Financial_Credentials").get_all_records(),
        "projects": sh.worksheet("Project_Experience").get_all_records(),
        "rules": sh.worksheet("Bid_Rules").get_all_records()
    }
    
    print("✅ Live Data Synced from Google Sheets")
    return profile
