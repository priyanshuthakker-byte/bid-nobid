import json
from pathlib import Path
from typing import List, Dict
from datetime import datetime, date

PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"

# ── Rules cache — loaded ONCE per process, not per tender row ──
_rules_cache = None

def load_rules() -> dict:
    """
    Load bid rules from nascent_profile.json ONLY.
    Google Sheets fetch removed — was causing 429 quota errors
    (called once per tender row = 800+ API calls per import).
    Rules are managed via Settings → Bid Rules in the app UI,
    which saves to nascent_profile.json and syncs to Drive.
    """
    global _rules_cache
    if _rules_cache is not None:
        return _rules_cache
    try:
        p = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        _rules_cache = p.get("bid_rules", {})
        return _rules_cache
    except Exception:
        _rules_cache = {}
        return _rules_cache


def invalidate_rules_cache():
    """Call this after saving new rules so classify_tender picks them up."""
    global _rules_cache
    _rules_cache = None


def classify_tender(brief: str, estimated_cost: float,
                    eligibility: str = "", checklist: str = "") -> Dict:
    """
    Classify tender as BID / NO-BID / CONDITIONAL / REVIEW.
    DO NOT BID rules always win — cannot be overridden by preferred sectors.
    """
    rules = load_rules()

    dnb_keywords = [k.lower().strip() for k in rules.get("do_not_bid", [])]
    dnb_remarks  = {k.lower(): v for k, v in rules.get("do_not_bid_remarks", {}).items()}
    cond_kw      = [k.lower().strip() for k in rules.get("conditional", [])]
    bid_kw       = [k.lower().strip() for k in rules.get("bid", [])]
    pref_kw      = [k.lower().strip() for k in rules.get("preferred_sectors", [])]
    min_val_cr   = float(rules.get("min_project_value_cr", 0.5))
    max_val_cr   = float(rules.get("max_project_value_cr", 150))

    brief_lower  = str(brief or "").lower().strip()
    full_text    = " ".join([str(brief or ""), str(eligibility or ""), str(checklist or "")]).lower()

    # Step 1: Value too low
    cost_cr = estimated_cost / 1_00_00_000 if estimated_cost and estimated_cost > 1000 else (estimated_cost or 0)
    if cost_cr and 0 < cost_cr < min_val_cr:
        return {"verdict": "NO-BID", "verdict_color": "RED",
                "reason": f"Value ₹{cost_cr:.2f} Cr — below Nascent minimum ₹{min_val_cr} Cr."}

    # Step 2: Value too high
    if cost_cr and cost_cr > max_val_cr:
        return {"verdict": "CONDITIONAL", "verdict_color": "AMBER",
                "reason": f"Value ₹{cost_cr:.1f} Cr — exceeds ₹{max_val_cr} Cr. Verify turnover PQ and consider consortium."}

    # Step 3: NO-BID rules — HARD BLOCK, checked before preferred sectors
    for kw in dnb_keywords:
        if kw and kw in brief_lower:
            remark = dnb_remarks.get(kw, f"Matches NO-BID rule: '{kw}'.")
            return {"verdict": "NO-BID", "verdict_color": "RED", "reason": remark}

    # Step 4: CONDITIONAL rules
    for kw in cond_kw:
        if kw and kw in full_text:
            return {"verdict": "CONDITIONAL", "verdict_color": "AMBER",
                    "reason": f"Conditional trigger: '{kw}' — verify eligibility before deciding."}

    # Step 5: BID rules (explicit capability match)
    for kw in bid_kw:
        if kw and kw in full_text:
            return {"verdict": "BID", "verdict_color": "GREEN",
                    "reason": f"Matches Nascent core capability: '{kw}'."}

    # Step 6: Preferred sectors
    matched = [kw for kw in pref_kw if kw and kw in full_text]
    if matched:
        return {"verdict": "BID", "verdict_color": "GREEN",
                "reason": f"Matches Nascent preferred sector: {', '.join(matched[:3])}."}

    # Step 7: Default → REVIEW
    return {"verdict": "REVIEW", "verdict_color": "BLUE",
            "reason": "Needs manual review — no strong match found."}


def days_left(deadline_str: str) -> int:
    if not deadline_str:
        return 999
    s = str(deadline_str).strip()
    for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y",
                "%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
        try:
            d = datetime.strptime(s[:10], fmt[:8]).date()
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
    """
    Read T247 Excel export (Non-GeM and GeM sheets) and return
    list of classified tenders with ALL required fields.
    """
    import openpyxl
    wb = openpyxl.load_workbook(filepath, data_only=True)
    all_tenders = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        is_gem = "gem" in sheet_name.lower()

        # Read headers from row 1
        raw_headers = [str(cell.value or "").strip() for cell in ws[1]]
        headers = [h.lower() for h in raw_headers]

        def col(*names):
            """Find column index by partial name match."""
            for name in names:
                nl = name.lower()
                for i, h in enumerate(headers):
                    if nl in h:
                        return i
            return None

        # Map all T247 columns
        idx = {
            "t247_id":        col("t247 id", "t247id"),
            "ref_no":         col("reference no", "ref no", "reference number"),
            "brief":          col("tender brief", "description", "title"),
            "value":          col("estimated cost", "value"),
            "deadline":       col("deadline", "bid submission", "closing date", "last date"),
            "location":       col("location"),
            "org_name":       col("organization", "organisation", "dept", "department"),
            "doc_fee":        col("document fees", "doc fee", "tender fee"),
            "emd":            col("emd"),
            "msme":           col("msme exemption", "msme"),
            "startup":        col("startup exemption", "startup"),
            "quantity":       col("quantity"),
            "checklist":      col("checklist"),
            "eligibility":    col("eligibility", "qualification"),
            # GeM-specific
            "ministry":       col("ministry", "state name"),
            "dept_name":      col("department name"),
            "turnover_req":   col("minimum average annual", "turnover of the bidder"),
            "exp_req":        col("years of past experience"),
            "eval_method":    col("evaluation method"),
            "contract_period":col("contract period"),
            "bid_type":       col("type of bid"),
            "similar_cat":    col("similar category"),
        }

        def cell(row, key):
            """Safely get cell value by column key."""
            i = idx.get(key)
            if i is None or i >= len(row):
                return ""
            v = row[i]
            return "" if v is None else str(v).strip()

        for row in ws.iter_rows(min_row=2, values_only=True):
            # Skip completely empty rows
            if not any(row):
                continue

            t247_id  = cell(row, "t247_id")
            brief    = cell(row, "brief")

            # Must have either T247 ID or brief to be useful
            if not t247_id and not brief:
                continue

            # Parse numeric values
            try:
                value = float(cell(row, "value") or 0)
            except (ValueError, TypeError):
                value = 0.0
            try:
                emd = float(cell(row, "emd") or 0)
            except (ValueError, TypeError):
                emd = 0.0
            try:
                doc_fee = float(cell(row, "doc_fee") or 0)
            except (ValueError, TypeError):
                doc_fee = 0.0

            deadline = cell(row, "deadline")
            dl_days  = days_left(deadline)
            dl_status = deadline_status(dl_days)

            eligibility = cell(row, "eligibility")
            checklist   = cell(row, "checklist")

            # Auto-classify
            verdict = classify_tender(brief, value, eligibility, checklist)

            # Cost in Cr for display
            cost_cr = ""
            if value > 0:
                if value >= 1e7:
                    cost_cr = f"{value/1e7:.2f}"
                elif value >= 1e5:
                    cost_cr = f"{value/1e5:.1f}L"
                else:
                    cost_cr = str(round(value))

            all_tenders.append({
                "t247_id":              t247_id,
                "ref_no":               cell(row, "ref_no"),
                "brief":                brief[:400] if brief else "",
                "org_name":             cell(row, "org_name")[:120],
                "location":             cell(row, "location")[:80],
                "estimated_cost_raw":   value,
                "estimated_cost_cr":    cost_cr,
                "deadline":             deadline,
                "days_left":            dl_days,
                "deadline_status":      dl_status,
                "doc_fee":              doc_fee,
                "emd":                  emd,
                "msme_exemption":       cell(row, "msme"),
                "eligibility":          eligibility[:500],
                "checklist":            checklist[:1000],
                "is_gem":               is_gem,
                "quantity":             cell(row, "quantity"),
                # GeM fields
                "ministry":             cell(row, "ministry"),
                "dept_name":            cell(row, "dept_name"),
                "turnover_req":         cell(row, "turnover_req"),
                "exp_req":              cell(row, "exp_req"),
                "eval_method":          cell(row, "eval_method"),
                "contract_period":      cell(row, "contract_period"),
                "bid_type":             cell(row, "bid_type"),
                "similar_cat":          cell(row, "similar_cat"),
                # Classification
                "verdict":              verdict["verdict"],
                "verdict_color":        verdict.get("verdict_color", ""),
                "reason":               verdict.get("reason", ""),
                "tags":                 [],
            })

    return all_tenders
