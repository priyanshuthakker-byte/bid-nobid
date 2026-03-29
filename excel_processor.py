"""
Excel Processor v3 — Nascent Info Technologies Bid/No-Bid System

Reads T247 Excel export and auto-classifies each tender as:
  BID / NO-BID / CONDITIONAL / REVIEW

Classification rules loaded from nascent_profile.json → bid_rules section.
No hardcoding — update the JSON to change behaviour.

Key fix v3:
- bid_rules was missing from nascent_profile.json → all tenders showed REVIEW
- Civil/construction no-bid keywords now cannot be overridden by IT keywords
- Preferred sector override only applies to "soft" no-bid (non-civil) keywords
"""

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
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        # Google Sheets API setup
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            "google_service_account.json", scope
        )
        client = gspread.authorize(creds)

        # Open Nascent Master sheet by ID
        sheet = client.open_by_key("1IE6ho0uDMM2emhci_h8OD__sk41_Uj5v")
        rules_ws = sheet.worksheet("BidRules")  # <-- make sure you have a tab named BidRules

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



def classify_tender(brief: str, estimated_cost: float,
                    eligibility: str = "", checklist: str = "") -> Dict:
    """
    Classify tender as BID / NO-BID / CONDITIONAL / REVIEW.

    Logic order:
    1. Hard no-bid (civil/physical) — cannot be overridden
    2. Value too low → NO-BID
    3. Value too high → CONDITIONAL
    4. Soft no-bid keywords (overridable if preferred sector also matches)
    5. Conditional triggers → CONDITIONAL
    6. Preferred sector match → BID
    7. Default → REVIEW (needs manual review)
    """
    rules = load_rules()

    dnb_keywords = [k.lower().strip() for k in rules.get("do_not_bid", [])]
    dnb_remarks  = {k.lower(): v for k, v in rules.get("do_not_bid_remarks", {}).items()}
    cond_kw      = [k.lower().strip() for k in rules.get("conditional", [])]
    pref_kw      = [k.lower().strip() for k in rules.get("preferred_sectors", [])]
    min_val_cr   = rules.get("min_project_value_cr", 0.5)
    max_val_cr   = rules.get("max_project_value_cr", 150)

    brief_lower  = str(brief or "").lower().strip()
    full_text    = " ".join([
        str(brief or ""), str(eligibility or ""), str(checklist or "")
    ]).lower()

    # ── STEP 1: Hard NO-BID (civil/physical — cannot be overridden) ──────────
    for kw in HARD_NO_BID:
        if kw in brief_lower:
            return {
                "verdict":       "NO-BID",
                "verdict_color": "RED",
                "reason":        f"Civil/physical work — outside Nascent IT domain: '{kw}'",
            }

    # ── STEP 2: Value too low ─────────────────────────────────────────────────
    cost_cr = estimated_cost / 1_00_00_000 if estimated_cost and estimated_cost > 1000 else estimated_cost
    if cost_cr and 0 < cost_cr < min_val_cr:
        return {
            "verdict":       "NO-BID",
            "verdict_color": "RED",
            "reason":        f"Value Rs.{cost_cr:.2f} Cr — below Nascent minimum Rs.{min_val_cr} Cr.",
        }

    # ── STEP 3: Value too high ────────────────────────────────────────────────
    if cost_cr and cost_cr > max_val_cr:
        return {
            "verdict":       "CONDITIONAL",
            "verdict_color": "AMBER",
            "reason":        f"Value Rs.{cost_cr:.1f} Cr — exceeds Rs.{max_val_cr} Cr. Verify turnover PQ and consider consortium.",
        }

    # ── STEP 4: Soft NO-BID keywords ─────────────────────────────────────────
    # NO override — if user added a keyword to DO NOT BID, it wins unconditionally.
    # User explicitly said "supply of gis software" → NO-BID. That is final.
    # Preferred sector can only promote a REVIEW to BID, not override an explicit NO-BID rule.
    for kw in dnb_keywords:
        if kw in brief_lower:
            remark = dnb_remarks.get(kw, f"Matches NO-BID rule: '{kw}'.")
            return {
                "verdict":       "NO-BID",
                "verdict_color": "RED",
                "reason":        remark,
            }

    # ── STEP 5: CONDITIONAL triggers ─────────────────────────────────────────
    for kw in cond_kw:
        if kw in full_text:
            return {
                "verdict":       "CONDITIONAL",
                "verdict_color": "AMBER",
                "reason":        f"Conditional trigger: '{kw}' — verify eligibility before deciding.",
            }

    # ── STEP 6: Preferred sector → BID ───────────────────────────────────────
    matched = [kw for kw in pref_kw if kw in full_text]
    if matched:
        return {
            "verdict":       "BID",
            "verdict_color": "GREEN",
            "reason":        f"Matches Nascent preferred domain: {', '.join(matched[:3])}.",
        }

    # ── STEP 7: Default → REVIEW ─────────────────────────────────────────────
    return {
        "verdict":       "REVIEW",
        "verdict_color": "BLUE",
        "reason":        "Needs manual review — insufficient data to auto-classify. Open tender details to decide.",
    }


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

        # Read and normalise headers
        headers = [str(cell.value or "").strip().lower() for cell in ws[1]]

        def col(*names):
            for name in names:
                for i, h in enumerate(headers):
                    if name in h:
                        return i
            return None

        idx = {
            "t247_id":   col("t247 id", "t247id", "tender id", "sr.no", "sr no"),
            "ref_no":    col("reference no", "ref no", "reference number", "tender no"),
            "brief":     col("tender brief", "brief", "description", "title", "work name", "name of work"),
            "cost":      col("estimated cost", "cost", "value", "estimated value", "amount"),
            "deadline":  col("deadline", "last date", "submission date", "bid submission", "closing date"),
            "location":  col("location", "state", "city", "district"),
            "org":       col("organization", "organisation", "department", "dept", "authority"),
            "doc_fee":   col("document fee", "doc fee", "tender fee", "processing fee"),
            "emd":       col("emd", "earnest money", "bid security"),
            "msme":      col("msme exemption", "msme", "msme/startup"),
            "startup":   col("startup exemption", "startup"),
            "eligibility": col("eligibility criteria", "eligibility", "qualification"),
            "checklist": col("checklist", "documents required"),
            # GeM specific
            "bid_opening":   col("bid opening date", "opening date"),
            "bid_validity":  col("bid offer validity", "validity"),
            "ministry":      col("ministry", "ministry/state"),
            "department":    col("department name"),
            "office":        col("office name"),
            "turnover_req":  col("minimum average annual turnover", "turnover of the bidder", "minimum turnover"),
            "exp_years":     col("years of past experience", "past experience required"),
            "contract_period": col("contract period"),
            "similar_cat":   col("similar category"),
            "eval_method":   col("evaluation method"),
            "pbg_pct":       col("epbg percentage", "pbg percentage"),
            "mse_pref":      col("mse purchase preference"),
            "type_of_bid":   col("type of bid"),
        }

        for row in ws.iter_rows(min_row=2, values_only=True):
            t247_id = row[idx["t247_id"]] if idx["t247_id"] is not None else None
            if not t247_id:
                continue

            def cell(key):
                i = idx.get(key)
                return row[i] if i is not None and i < len(row) else ""

            def cs(key):
                return str(cell(key) or "").strip()

            brief    = cs("brief")
            cost_raw = cell("cost")
            deadline = cs("deadline")
            org      = cs("org")
            location = cs("location")
            emd      = cs("emd")
            doc_fee  = cs("doc_fee")
            msme     = cs("msme")
            ref_no   = cs("ref_no")
            elig     = cs("eligibility")
            chklist  = cs("checklist")

            # Parse cost — handles "1,50,00,000" and "1.5 Cr" and plain number
            try:
                cost_str = str(cost_raw or "").replace(",", "").strip()
                # Handle "Cr" suffix
                if "cr" in cost_str.lower():
                    cost_str = cost_str.lower().replace("cr", "").strip()
                    cost = float(cost_str) * 1_00_00_000
                elif "lakh" in cost_str.lower() or "lac" in cost_str.lower():
                    cost_str = re.sub(r'[^\d.]', '', cost_str)
                    cost = float(cost_str) * 1_00_000
                else:
                    cost = float(re.sub(r'[^\d.]', '', cost_str)) if cost_str else 0
            except Exception:
                cost = 0

            classification = classify_tender(brief, cost, elig, chklist)

            dl      = days_left(deadline)
            cost_cr = round(cost / 1_00_00_000, 2) if cost > 1000 else round(cost, 2)

            tender = {
                "t247_id":           str(t247_id),
                "ref_no":            ref_no,
                "brief":             brief[:200],
                "org_name":          org,
                "location":          location,
                "estimated_cost_raw":  cost,
                "estimated_cost_cr":   cost_cr,
                "deadline":          deadline,
                "days_left":         dl,
                "deadline_status":   deadline_status(dl),
                "doc_fee":           doc_fee,
                "emd":               emd,
                "msme_exemption":    msme,
                "startup_exemption": cs("startup"),
                "eligibility":       elig[:1000],
                "checklist":         chklist[:1000],
                "is_gem":            is_gem,
                "verdict":           classification["verdict"],
                "verdict_color":     classification["verdict_color"],
                "reason":            classification["reason"],
                "bid_no_bid_done":   False,
                "status":            "Identified",
                "imported_at":       datetime.now().isoformat(),
            }

            # GeM extra fields
            if is_gem:
                tender.update({
                    "bid_opening_date":   cs("bid_opening"),
                    "bid_validity":       cs("bid_validity"),
                    "ministry":           cs("ministry"),
                    "department":         cs("department"),
                    "office":             cs("office"),
                    "turnover_required":  cs("turnover_req"),
                    "experience_years":   cs("exp_years"),
                    "contract_period":    cs("contract_period"),
                    "similar_category":   cs("similar_cat"),
                    "evaluation_method":  cs("eval_method"),
                    "pbg_percentage":     cs("pbg_pct"),
                    "mse_preference":     cs("mse_pref"),
                    "type_of_bid":        cs("type_of_bid"),
                })

            all_tenders.append(tender)

    return all_tenders


def quick_classify(brief: str, cost: float = 0, eligibility: str = "") -> Dict:
    return classify_tender(brief, cost, eligibility, "")
