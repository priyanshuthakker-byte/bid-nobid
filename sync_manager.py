import gspread
import json
import os

def load_combined_profile():
    try:
        # 1. Connect to Google
        gc = gspread.service_account(filename='google_key.json')
        sh = gc.open_by_key("1lgq44BOQaOpQFuAb-yyPcEGfQME1Wa6jvJKMg1_C_Y8")
        
        # 2. Get Raw Data
        identity_rows = sh.worksheet("Firm_Identity").get_all_records()
        financial_rows = sh.worksheet("Financial_Credentials").get_all_records()
        project_rows = sh.worksheet("Project_Experience").get_all_records()

        # 3. THE FORMATTER (This fixes your "No Changes" issue)
        # We turn the flat sheet rows into the nested JSON your code expects
        id_data = identity_rows[0] if identity_rows else {}
        
        profile = {
            "firm_identity": {
                "legal_name": id_data.get("Legal_Name"),
                "gst_number": id_data.get("GST_Number"),
                "pan_number": id_data.get("PAN_Number"),
                "address": id_data.get("Registered_Address")
            },
            "financial_strength": {
                "turnover": {row["Year"]: row["Turnover_INR"] for row in financial_rows}
            },
            "technical_credentials": {
                "projects": project_rows # Keeps all your project rows as a list
            }
        }
        
        print("✅ Live Data Mapped Successfully!")
        return profile

    except Exception as e:
        print(f"⚠️ Sync Error: {e}. Using local fallback.")
        with open('nascent_profile.json', 'r') as f:
            return json.load(f)
