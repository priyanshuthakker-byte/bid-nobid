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


def classify_tender(brief: str, estimated_cost: float,
                    eligibility: str = "", checklist: str = "") -> Dict:
    """
    Classify tender as BID / NO-BID / CONDITIONAL / REVIEW.
    """
    rules = load_rules()

    dnb_keywords = [k.lower().strip() for k in rules.get("do_not_bid", [])]
    dnb_remarks  = {k.lower(): v for k, v in rules.get("do_not_bid_remarks", {}).items()}
    cond_kw      = [k.lower().strip() for k in rules.get("conditional", [])]
    pref_kw      = [k.lower().strip() for k in rules.get("preferred_sectors", [])]
    min_val_cr   = rules.get("min_project_value_cr", 0.5)
    max_val_cr   = rules.get("max_project_value_cr", 150)

    brief_lower  = str(brief or "").lower().strip()
    full_text    = " ".join([str(brief or ""), str(eligibility or ""), str(checklist or "")]).lower()

    # Step 1: Hard NO-BID
    for kw in HARD_NO_BID:
        if kw in brief_lower:
            return {"verdict": "NO-BID", "verdict_color": "RED",
                    "reason": f"Civil/physical work — outside Nascent IT domain: '{kw}'"}

    # Step 2: Value too low
    cost_cr = estimated_cost / 1_00_00_000 if estimated_cost and estimated_cost > 1000 else estimated_cost
    if cost_cr and 0 < cost_cr < min_val_cr:
        return {"verdict": "NO-BID", "verdict_color": "RED",
                "reason": f"Value Rs.{cost_cr:.2f} Cr — below Nascent minimum Rs.{min_val_cr} Cr."}

    # Step 3: Value too high
    if cost_cr and cost_cr > max_val_cr:
        return {"verdict": "CONDITIONAL", "verdict_color": "AMBER",
                "reason": f"Value Rs.{cost_cr:.1f} Cr — exceeds Rs.{max_val_cr} Cr. Verify turnover PQ and consider consortium."}

    # Step 4: Soft NO-BID
    for kw in dnb_keywords:
        if kw in brief_lower:
            remark = dnb_remarks.get(kw, f"Matches NO-BID rule: '{kw}'.")
            return {"verdict": "NO-BID", "verdict_color": "RED", "reason": remark}

    # Step 5: CONDITIONAL triggers
    for kw in cond_kw:
        if kw in full_text:
            return {"verdict": "CONDITIONAL", "verdict_color": "AMBER",
                    "reason": f"Conditional trigger: '{kw}' — verify eligibility before deciding."}

    # Step 6: Preferred sector → BID
    matched = [kw for kw in pref_kw if kw in full_text]
    if matched:
        return {"verdict": "BID", "verdict_color": "GREEN",
                "reason": f"Matches Nascent preferred domain: {', '.join(matched[:3])}."}

    # Step 7: Default → REVIEW
    return {"verdict": "REVIEW", "verdict_color": "BLUE",
            "reason": "Needs manual review — insufficient data to auto-classify."}


def days_left(deadline_str: str) -> int:
    if not deadline_str:
        return 999
    for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y",
                "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"]:
        try:
            d = datetime.strptime(str(deadline_str).strip()[:10], fmt[:8]).date()
            return (d - date.today()).days
        except Exception:
            continue
    return 999


def deadline_status(dl: int) -> str:
    if dl < 0:   return "EXPIRED"
    if dl == 0:  return "TODAY"
    if dl <= 3:  return "URGENT"
    if dl <= 7:  return "THIS_WEEK"
    if dl <= 14: return "SOON"
    return "OK"


def process_excel(filepath: str) -> List[Dict]:
    """Read T247 Excel export and return list of classified tenders."""
    import openpyxl
    wb = openpyxl.load_workbook(filepath, data_only=True)
    all_tenders = []

    for sheet_name in wb.sheetnames:
        ws  = wb[sheet_name]
        is_gem = "gem" in sheet_name.lower()

        headers = [str(cell.value or "").strip().lower() for cell in ws[1]]

        def col(*names):
            for name in names:
                for i, h in enumerate(headers):
                    if name in h:
                        return i
            return None

        idx = {
            "t247_id":   col("t247 id", "tender id", "sr.no"),
            "ref_no":    col("reference no", "tender no"),
            "brief":     col("tender brief", "description", "title
