def load_rules() -> dict:
    """
    Load bid classification rules from Google Sheet (Nascent Master).
    Falls back to nascent_profile.json if API fails.
    """
    try:
        import os, json
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        # Google Sheets API setup
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]

        # Load JSON from Render environment variable
        service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
        client = gspread.authorize(creds)

        # Open Nascent Master sheet by ID
        sheet = client.open_by_key("YOUR_SHEET_ID")   # replace with your actual sheet ID
        rules_ws = sheet.worksheet("BidRules")        # make sure you have a tab named BidRules

        # Read rules into dict
        rules_data = rules_ws.get_all_records()
        rules = {
            "do_not_bid": [r["Keyword"].lower() for r in rules_data if r["Type"] == "NO-BID"],
            "do_not_bid_remarks": {r["Keyword"].lower(): r["Remark"] for r in rules_data if r["Type"] == "NO-BID"},
            "conditional": [r["Keyword"].lower() for r in rules_data if r["Type"] == "CONDITIONAL"],
            "preferred_sectors": [r["Keyword"].lower() for r in rules_data if r["Type"] == "PREFERRED"],
            "min_project_value_cr": float(rules_ws.acell("B2").value or 0.5),
            "max_project_value_cr": float(rules_ws.acell("B3").value or 150),
        }
        return rules

    except Exception as e:
        print(f"⚠️ Google Sheets fetch failed: {e}")
        # Fallback to local JSON
        try:
            p = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            return p.get("bid_rules", {})
        except Exception:
            return {}
