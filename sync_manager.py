"""
sync_manager.py — Two-way Google Sheets sync
RULE: Nothing syncs automatically. Sync ONLY when the user presses the Sync button.
  Sheet → App: reads all tabs, rebuilds nascent_profile.json
  App → Sheet: writes current bid_rules + profile back to Sheet
"""
import json, os
from pathlib import Path

SHEET_ID   = "1lgq44BOQaOpQFuAb-yyPcEGfQME1Wa6jvJKMg1_C_Y8"
BASE_DIR   = Path(__file__).parent
PROFILE_PATH = BASE_DIR / "nascent_profile.json"

# ── Credentials ───────────────────────────────────────────────
def _get_creds():
    raw = os.environ.get("GDRIVE_CREDENTIALS","").strip()
    if raw:
        try: return json.loads(raw)
        except: pass
    local = BASE_DIR / "google_key.json"
    if local.exists():
        try: return json.loads(local.read_text())
        except: pass
    return None

def _connect():
    """Return authorised gspread client or None."""
    creds_data = _get_creds()
    if not creds_data:
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Sheet connect failed: {e}")
        return None


# ── SHEET → APP ────────────────────────────────────────────────
def pull_from_sheet() -> dict:
    """
    Read ALL tabs from Google Sheet → return merged profile dict.
    Called only when user presses Sync button.
    """
    gc = _connect()
    if not gc:
        return {"error": "Cannot connect to Google Sheets — check GDRIVE_CREDENTIALS"}

    try:
        sh = gc.open_by_key(SHEET_ID)
        result = {"tabs_read": [], "tabs_missing": [], "tabs_skipped": []}

        # Load existing profile as base
        profile = json.loads(PROFILE_PATH.read_text()) if PROFILE_PATH.exists() else {}

        # ── Tab 1: Firm_Identity ──────────────────────────────
        try:
            rows = sh.worksheet("Firm_Identity").get_all_records()
            if rows:
                r = rows[0]
                profile.setdefault("company", {}).update({
                    k: v for k, v in {
                        "name":         r.get("Legal_Name") or r.get("Company_Name",""),
                        "cin":          r.get("CIN",""),
                        "pan":          r.get("PAN_Number") or r.get("PAN",""),
                        "gstin":        r.get("GST_Number") or r.get("GSTIN",""),
                        "msme_udyam":   r.get("UDYAM",""),
                        "address":      r.get("Registered_Address") or r.get("Address",""),
                        "phone":        r.get("Phone",""),
                        "email":        r.get("Email",""),
                        "website":      r.get("Website",""),
                    }.items() if v
                })
                profile.setdefault("employees", {}).update({
                    k: v for k, v in {
                        "total":          r.get("Employee_Count") or r.get("Total_Employees",""),
                        "gis_specialists": r.get("GIS_Specialists",""),
                        "it_developers":   r.get("IT_Developers",""),
                    }.items() if v
                })
                profile.setdefault("authorised_signatory", {}).update({
                    k: v for k, v in {
                        "name":        r.get("Signatory_Name") or r.get("Signatory",""),
                        "designation": r.get("Signatory_Designation",""),
                    }.items() if v
                })
            result["tabs_read"].append("Firm_Identity")
        except Exception as e:
            result["tabs_missing"].append(f"Firm_Identity ({e})")

        # ── Tab 2: Financial_Credentials ─────────────────────
        try:
            rows = sh.worksheet("Financial_Credentials").get_all_records()
            tv = {}
            total = 0
            for r in rows:
                yr  = str(r.get("Year") or r.get("FY","")).strip()
                val = r.get("Turnover_INR") or r.get("Turnover") or r.get("Amount","")
                if yr and val:
                    try:
                        amt = float(str(val).replace(",","").replace("₹","").strip())
                        tv[yr] = amt
                        total += amt
                    except: pass
            if tv:
                avg = round(total / len(tv), 2)
                profile.setdefault("financials",{}).update({
                    "turnover_by_year": tv,
                    "avg_turnover_3yr": avg,
                    "avg_turnover_3yr_cr": f"Rs. {round(avg/100,2)} Cr" if avg > 100 else f"Rs. {avg} Cr",
                })
            result["tabs_read"].append("Financial_Credentials")
        except Exception as e:
            result["tabs_missing"].append(f"Financial_Credentials ({e})")

        # ── Tab 3: Project_Experience ─────────────────────────
        try:
            rows = sh.worksheet("Project_Experience").get_all_records()
            projects = []
            for r in rows:
                name = r.get("Project_Name") or r.get("Name","")
                if not name: continue
                projects.append({
                    "name":        name,
                    "client":      r.get("Client",""),
                    "client_type": r.get("Client_Type",""),
                    "state":       r.get("State",""),
                    "value_lakhs": r.get("Value_Lakhs") or r.get("Value_Cr",""),
                    "value_display": r.get("Value_Display") or r.get("Value",""),
                    "status":      r.get("Status",""),
                    "domains":     r.get("Domains") or r.get("Domain",""),
                    "scope":       r.get("Scope",""),
                    "technologies":r.get("Technologies") or r.get("Tech",""),
                    "year_started":r.get("Year_Started") or r.get("Start_Year",""),
                    "year_completed": r.get("Year_Completed") or r.get("End_Year",""),
                })
            if projects:
                profile["projects"] = projects
            result["tabs_read"].append("Project_Experience")
        except Exception as e:
            result["tabs_missing"].append(f"Project_Experience ({e})")

        # ── Tab 4: Certifications (optional tab) ──────────────
        try:
            rows = sh.worksheet("Certifications").get_all_records()
            certs = {}
            for r in rows:
                name = r.get("Cert_Name") or r.get("Name","")
                if not name: continue
                key = name.lower().replace(" ","_").replace("/","_").replace("-","_")[:20]
                certs[key] = {
                    "standard":   r.get("Standard",""),
                    "valid_till": r.get("Valid_Till") or r.get("Expiry",""),
                    "cert_body":  r.get("Cert_Body") or r.get("Issuer",""),
                    "status":     r.get("Status","Active"),
                }
            if certs:
                profile["certifications"] = certs
            result["tabs_read"].append("Certifications")
        except:
            result["tabs_skipped"].append("Certifications (tab not created yet)")

        # ── Tab 5: Bid_Rules ──────────────────────────────────
        try:
            rows = sh.worksheet("Bid_Rules").get_all_records()
            do_not_bid = []
            preferred  = []
            conditional = []
            min_cr = 0.5
            max_cr = 150.0

            for r in rows:
                rtype   = str(r.get("Rule_Type") or r.get("Type","")).strip().lower()
                keyword = str(r.get("Keyword") or r.get("Value","")).strip().lower()
                if not keyword: continue

                if rtype in ("do_not_bid","no_bid","nobid"):
                    do_not_bid.append(keyword)
                elif rtype in ("preferred_sector","preferred","bid","bid_sector"):
                    preferred.append(keyword)
                elif rtype in ("conditional","cond"):
                    conditional.append(keyword)
                elif rtype == "value_range":
                    if "min" in keyword:
                        try: min_cr = float(keyword.split("=")[-1].strip())
                        except: pass
                    elif "max" in keyword:
                        try: max_cr = float(keyword.split("=")[-1].strip())
                        except: pass

            # Merge with existing rules (Sheet wins if rows exist)
            existing_rules = profile.get("bid_rules", {})
            profile["bid_rules"] = {
                "do_not_bid":        sorted(set(do_not_bid)) or existing_rules.get("do_not_bid",[]),
                "preferred_sectors": sorted(set(preferred))  or existing_rules.get("preferred_sectors",[]),
                "conditional":       sorted(set(conditional)) or existing_rules.get("conditional",[]),
                "min_project_value_cr": min_cr,
                "max_project_value_cr": max_cr,
                "do_not_bid_remarks":  existing_rules.get("do_not_bid_remarks", {}),
            }
            result["tabs_read"].append("Bid_Rules")
        except:
            result["tabs_skipped"].append("Bid_Rules (tab not created yet — rules unchanged)")

        # Update metadata
        from datetime import datetime
        profile["metadata"] = {
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "google_sheet",
            "version": "2.1",
        }

        # Save to local JSON
        PROFILE_PATH.parent.mkdir(exist_ok=True, parents=True)
        PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False))

        result["status"]  = "success"
        result["message"] = (
            f"Pulled from Sheet: {', '.join(result['tabs_read'])}. "
            + (f"Missing tabs (create to use): {', '.join(result['tabs_skipped'])}." if result['tabs_skipped'] else "")
        )
        result["profile_saved"] = True
        return result

    except Exception as e:
        return {"error": str(e), "status": "failed"}


# ── APP → SHEET ────────────────────────────────────────────────
def push_to_sheet() -> dict:
    """
    Write current nascent_profile.json back to Google Sheet.
    Called only when user presses Sync button.
    """
    gc = _connect()
    if not gc:
        return {"error": "Cannot connect to Google Sheets — check GDRIVE_CREDENTIALS"}

    if not PROFILE_PATH.exists():
        return {"error": "nascent_profile.json not found — nothing to push"}

    profile = json.loads(PROFILE_PATH.read_text())
    result  = {"tabs_written": [], "tabs_skipped": []}

    try:
        sh = gc.open_by_key(SHEET_ID)

        # ── Push Bid_Rules ─────────────────────────────────────
        try:
            rules = profile.get("bid_rules", {})
            rows  = [["Rule_Type", "Keyword", "Remarks"]]

            # Value range
            rows.append(["value_range", f"min_cr={rules.get('min_project_value_cr',0.5)}", "Min project value in Cr"])
            rows.append(["value_range", f"max_cr={rules.get('max_project_value_cr',150)}", "Max project value in Cr"])

            remarks = rules.get("do_not_bid_remarks", {})
            for kw in rules.get("do_not_bid", []):
                rows.append(["do_not_bid", kw, remarks.get(kw, "")])
            for kw in rules.get("preferred_sectors", []):
                rows.append(["preferred_sector", kw, ""])
            for kw in rules.get("conditional", []):
                rows.append(["conditional", kw, "Raise pre-bid query"])

            try:
                ws = sh.worksheet("Bid_Rules")
            except:
                ws = sh.add_worksheet(title="Bid_Rules", rows=300, cols=5)

            ws.clear()
            ws.update("A1", rows)
            result["tabs_written"].append("Bid_Rules")
        except Exception as e:
            result["tabs_skipped"].append(f"Bid_Rules ({e})")

        # ── Push Financial_Credentials ─────────────────────────
        try:
            fin   = profile.get("financials", {})
            by_yr = fin.get("turnover_by_year", {})
            rows  = [["Year", "Turnover_INR", "Turnover_Cr"]]
            for yr, amt in sorted(by_yr.items()):
                rows.append([yr, amt, round(float(amt)/100, 2) if float(amt) > 100 else amt])
            ws = sh.worksheet("Financial_Credentials")
            ws.clear()
            ws.update("A1", rows)
            result["tabs_written"].append("Financial_Credentials")
        except Exception as e:
            result["tabs_skipped"].append(f"Financial_Credentials ({e})")

        # ── Push Project_Experience ────────────────────────────
        try:
            projects = profile.get("projects", [])
            if projects:
                cols = ["Project_Name","Client","Client_Type","State",
                        "Value_Lakhs","Value_Display","Status","Domains",
                        "Scope","Technologies","Year_Started","Year_Completed"]
                rows = [cols]
                for p in projects:
                    rows.append([
                        p.get("name",""), p.get("client",""), p.get("client_type",""),
                        p.get("state",""), p.get("value_lakhs",""), p.get("value_display",""),
                        p.get("status",""), str(p.get("domains","") or ""),
                        p.get("scope",""), str(p.get("technologies","") or ""),
                        p.get("year_started",""), p.get("year_completed",""),
                    ])
                ws = sh.worksheet("Project_Experience")
                ws.clear()
                ws.update("A1", rows)
                result["tabs_written"].append("Project_Experience")
        except Exception as e:
            result["tabs_skipped"].append(f"Project_Experience ({e})")

        # ── Push Certifications ────────────────────────────────
        try:
            certs = profile.get("certifications", {})
            rows  = [["Cert_Name","Standard","Valid_Till","Cert_Body","Status"]]
            for key, c in certs.items():
                rows.append([
                    c.get("level") or c.get("standard") or key,
                    c.get("standard",""), c.get("valid_till",""),
                    c.get("cert_body",""), c.get("status","Active"),
                ])
            try:
                ws = sh.worksheet("Certifications")
            except:
                ws = sh.add_worksheet(title="Certifications", rows=50, cols=6)
            ws.clear()
            ws.update("A1", rows)
            result["tabs_written"].append("Certifications")
        except Exception as e:
            result["tabs_skipped"].append(f"Certifications ({e})")

        result["status"]  = "success"
        result["message"] = f"Pushed to Sheet: {', '.join(result['tabs_written'])}."
        if result["tabs_skipped"]:
            result["message"] += f" Skipped: {', '.join(result['tabs_skipped'])}."
        return result

    except Exception as e:
        return {"error": str(e), "status": "failed"}


# ── PROFILE HELPERS ────────────────────────────────────────────
def load_local_profile() -> dict:
    """Return profile from nascent_profile.json (local cache)."""
    try:
        return json.loads(PROFILE_PATH.read_text()) if PROFILE_PATH.exists() else {}
    except:
        return {}


def profile_to_ai_context(profile: dict) -> str:
    """Convert profile dict → text block for AI prompt."""
    if not profile:
        return "Profile data unavailable."

    co   = profile.get("company", {})
    fin  = profile.get("financials", {})
    emp  = profile.get("employees", {})
    certs= profile.get("certifications", {})
    proj = profile.get("projects", [])

    tv = fin.get("turnover_by_year", {})
    tv_lines = "\n".join(f"  FY {yr}: ₹{val} Lakhs" for yr, val in sorted(tv.items())) if tv else "  Not available"
    avg_cr = fin.get("avg_turnover_3yr_cr", fin.get("avg_turnover_3yr","—"))

    cert_lines = ""
    for k, c in certs.items():
        cert_lines += f"  {c.get('level') or c.get('standard',k)} — valid till {c.get('valid_till','?')}\n"

    proj_lines = ""
    for i, p in enumerate(proj[:12], 1):
        proj_lines += (
            f"  {i}. {p.get('name','')} | {p.get('client','')} | "
            f"₹{p.get('value_display') or p.get('value_lakhs','')} | "
            f"{p.get('status','')} | {p.get('scope','')[:80]}\n"
        )

    return f"""NASCENT INFO TECHNOLOGIES PVT. LTD. — PROFILE:
Company: {co.get('name','')} | CIN: {co.get('cin','')} | MSME: {co.get('msme_udyam','')}
PAN: {co.get('pan','')} | GSTIN: {co.get('gstin','')}
Employees: {emp.get('total',67)} (GIS: {emp.get('gis_specialists',11)}, Dev: {emp.get('it_developers',21)})
Signatory: {profile.get('authorised_signatory',{}).get('name','Hitesh Patel')}

FINANCIALS:
{tv_lines}
  Average (3yr): {avg_cr} | Net Worth: {fin.get('net_worth_cr','Rs. 26.09 Cr')}

CERTIFICATIONS:
{cert_lines or '  CMMI V2.0 L3, ISO 9001, ISO 27001, ISO 20000'}
KEY PROJECTS:
{proj_lines}"""
