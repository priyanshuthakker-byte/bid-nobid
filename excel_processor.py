import re
import json
from pathlib import Path
from typing import List, Dict
from datetime import datetime, date

PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"

# Civil/physical keywords — these are HARD no-bid, cannot be overridden
HARD_NO_BID = [
    "road construction", "road repair", "road maintenance", "road laying",
    "road widening", "road resurfacing", "construction of road",
    "building construction", "construction of building",
    "civil work", "civil construction", "rcc", "rcc work",
    "bridge construction", "flyover", "culvert", "dam", "canal",
    "sewerage network", "sewerage pipeline", "sewerage laying",
    "water pipeline", "water supply pipeline", "underground pipeline",
    "plumbing work", "electrical installation", "electrical work",
    "street light installation", "horticulture", "garden maintenance",
    "procurement of security", "hiring of security", "deployment of security",
    "security guard supply", "security service", "housekeeping service",
    "manpower supply", "labour supply",
    "catering service", "food supply",
    "supply of medicine", "pharmaceutical", "medical equipment supply",
    "insurance policy", "life insurance",
    "printing of", "offset printing",
    "purchase of vehicle", "procurement of vehicle",
    "supply of computer hardware", "procurement of hardware only",
    "furniture supply", "supply of furniture",
    "annual maintenance of vehicle",
    "construction of community hall", "construction of school",
    "construction of hospital",
]


def load_rules() -> dict:
    """
    Load bid classification rules from Google Sheet (Nascent Master).
    Falls back to nascent_profile.json if API fails.
    """
    try:
        import os, json
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]

        # Load JSON from Render environment variable
        service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
        client = gspread.authorize(creds)

        # Open Nascent Master sheet by ID
        sheet = client.open_by_key("1qYkeJP04bbD-vHDF4lPxiSWPy8TSqxUoFzZFzTCGXO0")
        rules_ws = sheet.worksheet("Bid Rules")  # <-- matches your tab name

        # Read rules into dict
        rules_data = rules_ws.get_all_records()
        rules = {
            "do_not_bid": [r["Rule / Condition"].lower() for r in rules_data if r["Rule Type"] == "DO NOT BID"],
            "do_not_bid_remarks": {r["Rule / Condition"].lower(): r["Reason / Notes"] for r in rules_data if r["Rule Type"] == "DO NOT BID"},
            "conditional": [r["Rule / Condition"].lower() for r in rules_data if r["Rule Type"] == "CONDITIONAL"],
            "preferred_sectors": [r["Rule / Condition"].lower() for r in rules_data if r["Rule Type"] == "PREFERRED"],
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

if __name__ == "__main__":
    rules = load_rules()
    print("Loaded rules:", json.dumps(rules, indent=2))

