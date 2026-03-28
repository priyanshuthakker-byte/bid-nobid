"""
sync_manager.py — Two-way Google Sheets sync
Sheet: Nascent_Tender_Master_v4  (14 tabs)
RULE: Nothing syncs automatically. Only when user presses Sync button.
Column names are EXACT matches from the actual sheet.
"""
import json, os
from pathlib import Path
from datetime import datetime

SHEET_ID     = "1lgq44BOQaOpQFuAb-yyPcEGfQME1Wa6jvJKMg1_C_Y8"
BASE_DIR     = Path(__file__).parent
PROFILE_PATH = BASE_DIR / "nascent_profile.json"

# ── Credentials ───────────────────────────────────────────────
def _get_creds():
    raw = os.environ.get("GDRIVE_CREDENTIALS", "").strip()
    if raw:
        try: return json.loads(raw)
        except: pass
    local = BASE_DIR / "google_key.json"
    if local.exists():
        try: return json.loads(local.read_text())
        except: pass
    return None

def _connect():
    creds_data = _get_creds()
    if not creds_data:
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(creds_data, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ])
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Sheet connect failed: {e}")
        return None

def _rows(ws):
    """Return list of dicts from worksheet (header row → keys)."""
    all_rows = ws.get_all_values()
    if not all_rows:
        return []
    headers = all_rows[0]
    result  = []
    for row in all_rows[1:]:
        if any(c.strip() for c in row):
            result.append({headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))})
    return result

def _v(d, *keys):
    """Return first non-empty value from dict using multiple key attempts."""
    for k in keys:
        val = d.get(k, "")
        if val and str(val).strip() and str(val).strip().upper() not in ("PENDING - UPDATE","PENDING","N/A",""):
            return str(val).strip()
    return ""

def _load_local():
    try:
        return json.loads(PROFILE_PATH.read_text()) if PROFILE_PATH.exists() else {}
    except:
        return {}

def _save_local(profile):
    PROFILE_PATH.parent.mkdir(exist_ok=True, parents=True)
    profile["metadata"] = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "google_sheet",
        "version": "3.0",
    }
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False))


# ══════════════════════════════════════════════════════════════
# PULL — Sheet → App
# ══════════════════════════════════════════════════════════════
def pull_from_sheet() -> dict:
    gc = _connect()
    if not gc:
        return {"error": "Cannot connect — check GDRIVE_CREDENTIALS env var", "status": "failed"}
    try:
        sh      = gc.open_by_key(SHEET_ID)
        profile = _load_local()
        result  = {"tabs_read": [], "tabs_skipped": [], "fields_updated": []}

        # ── Company Profile ───────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Company Profile"))
            # This sheet is Field→Details format (vertical)
            cp = {r.get("Field","").strip(): r.get("Details","").strip() for r in rows if r.get("Field")}
            co = profile.setdefault("company", {})
            mapping = {
                "name":             ["Company Name"],
                "cin":              ["CIN"],
                "pan":              ["PAN"],
                "gstin":            ["GSTIN"],
                "msme_udyam":       ["Udyam Registration"],
                "legal_status":     ["Legal Status"],
                "year_of_incorporation": ["Year of Incorporation"],
                "type":             ["Type of Organization"],
                "address":          ["Registered Address"],
                "email":            ["Corporate Email"],
                "tender_email":     ["Tender Email"],
                "website":          ["Website"],
                "phone":            ["Registered Office Phone"],
                "authorised_signatory": ["Authorised Signatory"],
                "poa_validity":     ["POA Validity"],
                "total_employees":  ["Total Employees"],
                "gis_staff":        ["GIS Staff Count"],
                "office_locations": ["Office Locations"],
            }
            for field, keys in mapping.items():
                val = next((cp.get(k,"") for k in keys if cp.get(k,"").strip()), "")
                if val and val.upper() not in ("PENDING - UPDATE","PENDING"):
                    co[field] = val
                    result["fields_updated"].append(f"company.{field}")
            # POA alert
            poa = co.get("poa_validity","")
            if poa and "2026" in poa:
                co["poa_alert"] = f"POA validity: {poa} — check if renewal needed"
            result["tabs_read"].append("Company Profile")
        except Exception as e:
            result["tabs_skipped"].append(f"Company Profile ({e})")

        # ── Finance ───────────────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Finance"))
            fin  = profile.setdefault("finance", {})
            tv   = {}
            last_nw_cr = ""
            ca_name    = ""
            for r in rows:
                fy = _v(r, "Financial Year")
                tc = _v(r, "Annual Turnover (Rs. Cr)")
                nw = _v(r, "Net Worth (Rs. Cr)")
                ca = _v(r, "CA Name")
                # Only real FY rows (format like 2022-23)
                if fy and "-" in fy and len(fy) == 7:
                    try:
                        tv[fy] = float(tc)
                        if nw: last_nw_cr = nw
                        if ca: ca_name = ca
                    except: pass
            if tv:
                last3 = sorted(tv.keys())[-3:]
                avg3  = round(sum(tv[k] for k in last3) / len(last3), 2)
                fin.update({
                    "turnover_by_year":      tv,
                    "avg_turnover_last_3_fy": avg3,
                    "avg_turnover_cr":        avg3,
                    "net_worth_cr":           last_nw_cr,
                    "ca_name":                ca_name,
                })
                result["fields_updated"].append(f"finance ({len(tv)} years, avg ₹{avg3} Cr)")
            result["tabs_read"].append("Finance")
        except Exception as e:
            result["tabs_skipped"].append(f"Finance ({e})")

        # ── Projects ──────────────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Projects"))
            projects = []
            for r in rows:
                name = _v(r, "Project Name")
                if not name: continue
                projects.append({
                    "name":          name,
                    "client":        _v(r, "Client"),
                    "client_type":   _v(r, "Client Type\n(Govt/PSU/Private)", "Client Type"),
                    "state":         _v(r, "State"),
                    "value_cr":      _v(r, "Value (Rs. Cr)"),
                    "value_display": _v(r, "Value (Rs. Cr)"),
                    "status":        _v(r, "Status\n(Completed/Ongoing)", "Status"),
                    "role":          _v(r, "Nascent Role\n(Solo/Consortium/Sub)", "Nascent Role"),
                    "tags":          _v(r, "Project Category Tags"),
                    "scope":         _v(r, "Scope Summary"),
                    "technologies":  _v(r, "Technology & Tools"),
                    "start_date":    _v(r, "Start Date"),
                    "end_date":      _v(r, "End Date / Go-Live"),
                    "duration":      _v(r, "Duration"),
                    "tender_ref":    _v(r, "Tender Ref"),
                    "contact_person":_v(r, "Client Contact Person"),
                    "contact_phone": _v(r, "Contact Number"),
                    "contact_email": _v(r, "Email ID"),
                    "docs_available":_v(r, "Supporting Docs Available"),
                    "description":   _v(r, "Description of Services Provided"),
                    "team_size":     _v(r, "Team Size"),
                    "consortium_lead": _v(r, "Consortium Lead (if any)"),
                })
            if projects:
                profile["projects"] = projects
                result["fields_updated"].append(f"projects ({len(projects)} entries)")
            result["tabs_read"].append("Projects")
        except Exception as e:
            result["tabs_skipped"].append(f"Projects ({e})")

        # ── Employees ─────────────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Employees"))
            emp_list = []
            gis_count = it_count = key_count = 0
            for r in rows:
                name = _v(r, "Employee Name")
                if not name: continue
                is_gis = r.get("GIS Staff (Y/N)","").strip().lower() == "yes"
                is_it  = r.get("IT/Dev Staff (Y/N)","").strip().lower() == "yes"
                is_key = r.get("Key Person for Tenders (Y/N)","").strip().lower() == "yes"
                if is_gis: gis_count += 1
                if is_it:  it_count  += 1
                if is_key: key_count += 1
                emp_list.append({
                    "name":        name,
                    "designation": _v(r, "Designation"),
                    "department":  _v(r, "Department / Function"),
                    "years":       _v(r, "Years In Service"),
                    "is_gis":      is_gis,
                    "is_it":       is_it,
                    "is_key":      is_key,
                })
            co = profile.setdefault("company", {})
            co["total_employees"] = str(len(emp_list))
            profile["employees"] = {
                "total":         len(emp_list),
                "gis_staff":     gis_count,
                "it_dev_staff":  it_count,
                "key_persons":   key_count,
                "list":          emp_list,
            }
            result["fields_updated"].append(f"employees ({len(emp_list)} total, {gis_count} GIS, {it_count} IT)")
            result["tabs_read"].append("Employees")
        except Exception as e:
            result["tabs_skipped"].append(f"Employees ({e})")

        # ── Certifications ────────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Certifications"))
            certs = {}
            for r in rows:
                name = _v(r, "Cert_Name")
                if not name: continue
                key  = name.lower().replace(" ","_").replace("/","_").replace(":","")[:20]
                certs[key] = {
                    "name":       name,
                    "standard":   _v(r, "Standard") or name,
                    "valid_till": _v(r, "Valid_Till"),
                    "cert_body":  _v(r, "Cert_Body"),
                    "status":     _v(r, "Status") or "Active",
                }
            if certs:
                profile["certifications"] = certs
                result["fields_updated"].append(f"certifications ({len(certs)})")
            result["tabs_read"].append("Certifications")
        except Exception as e:
            result["tabs_skipped"].append(f"Certifications ({e})")

        # ── Key Contacts ──────────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Key Contacts"))
            contacts = {}
            for r in rows:
                role = _v(r, "Department / Role")
                name = _v(r, "Name")
                if not role or not name: continue
                contacts[role] = {
                    "name":        name,
                    "designation": _v(r, "Designation"),
                    "email":       _v(r, "Email"),
                    "mobile":      _v(r, "Mobile"),
                    "notes":       _v(r, "Notes"),
                }
                # Populate company signatory from Key Contacts
                if "signatory" in role.lower() or "authorized" in role.lower():
                    profile.setdefault("company",{})["authorised_signatory"] = f"{name}, {_v(r,'Designation')}"
                if "bid executive" in role.lower():
                    profile["bid_executive"] = {
                        "name":        name,
                        "designation": _v(r, "Designation"),
                        "email":       _v(r, "Email"),
                        "phone":       _v(r, "Mobile"),
                    }
            profile["key_contacts"] = contacts
            result["fields_updated"].append(f"key_contacts ({len(contacts)})")
            result["tabs_read"].append("Key Contacts")
        except Exception as e:
            result["tabs_skipped"].append(f"Key Contacts ({e})")

        # ── POA & Authorization ───────────────────────────────
        try:
            rows = _rows(sh.worksheet("POA & Authorization"))
            poas = []
            for r in rows:
                doc = _v(r, "Document")
                if not doc: continue
                poas.append({
                    "document":          doc,
                    "issued_to":         _v(r, "Issued To"),
                    "designation":       _v(r, "Designation"),
                    "issued_by":         _v(r, "Issued By"),
                    "valid_from":        _v(r, "Valid From"),
                    "valid_to":          _v(r, "Valid To"),
                    "status":            _v(r, "Status"),
                    "type":              _v(r, "Type"),
                })
            if poas:
                profile["poa_authorization"] = poas
                # Set poa_validity on company from active POA
                for p in poas:
                    if p.get("valid_to") and "PENDING" not in p.get("issued_to",""):
                        profile.setdefault("company",{})["poa_validity"] = f"{p['valid_from']} to {p['valid_to']}"
                        break
            result["tabs_read"].append("POA & Authorization")
        except Exception as e:
            result["tabs_skipped"].append(f"POA & Authorization ({e})")

        # ── Bank Details ──────────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Bank Details"))
            banks = []
            for r in rows:
                acc = _v(r, "Account No.")
                if not acc: continue
                banks.append({
                    "account_type": _v(r, "Account Type"),
                    "account_no":   acc,
                    "bank_name":    _v(r, "Bank Name"),
                    "branch":       _v(r, "Branch Name"),
                    "ifsc":         _v(r, "IFSC Code"),
                    "pan":          _v(r, "PAN No."),
                })
            if banks:
                profile["bank_details"] = banks
                co = profile.setdefault("company",{})
                co["bank"]      = banks[0].get("bank_name","")
                co["bank_ifsc"] = banks[0].get("ifsc","")
            result["tabs_read"].append("Bank Details")
        except Exception as e:
            result["tabs_skipped"].append(f"Bank Details ({e})")

        # ── Bid_Rules (machine-readable tab) ─────────────────
        try:
            rows = _rows(sh.worksheet("Bid_Rules"))
            dnb = []; pref = []; cond = []
            min_cr = 0.5; max_cr = 150.0
            for r in rows:
                rtype   = r.get("Rule_Type","").strip().lower()
                keyword = r.get("Keyword","").strip().lower()
                if not keyword or keyword == "keyword": continue
                if rtype == "do_not_bid":        dnb.append(keyword)
                elif rtype == "preferred_sector": pref.append(keyword)
                elif rtype == "conditional":      cond.append(keyword)
                elif rtype == "value_range":
                    if "min_cr=" in keyword:
                        try: min_cr = float(keyword.split("=")[1])
                        except: pass
                    elif "max_cr=" in keyword:
                        try: max_cr = float(keyword.split("=")[1])
                        except: pass
            existing = profile.get("bid_rules", {})
            profile["bid_rules"] = {
                "do_not_bid":            sorted(set(dnb))  or existing.get("do_not_bid",[]),
                "preferred_sectors":     sorted(set(pref)) or existing.get("preferred_sectors",[]),
                "conditional":           sorted(set(cond)) or existing.get("conditional",[]),
                "min_project_value_cr":  min_cr,
                "max_project_value_cr":  max_cr,
                "do_not_bid_remarks":    existing.get("do_not_bid_remarks",{}),
            }
            result["fields_updated"].append(
                f"bid_rules ({len(dnb)} no-bid, {len(pref)} preferred, {len(cond)} conditional)"
            )
            result["tabs_read"].append("Bid_Rules")
        except Exception as e:
            result["tabs_skipped"].append(f"Bid_Rules ({e})")

        # ── Technology Stack ──────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Technology Stack"))
            techs = []
            for r in rows:
                tech = _v(r, "Technology / Tool")
                if tech: techs.append({
                    "name":        tech,
                    "category":    _v(r, "Category"),
                    "proficiency": _v(r, "Proficiency\n(Expert/Good/Basic)", "Proficiency"),
                    "projects":    _v(r, "Used In Projects"),
                })
            if techs:
                profile["technology_stack"] = techs
                profile["key_technologies"] = [t["name"] for t in techs]
            result["tabs_read"].append("Technology Stack")
        except Exception as e:
            result["tabs_skipped"].append(f"Technology Stack ({e})")

        # ── Pre-Bid Policies ──────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Pre-Bid Policies"))
            policies = []
            for r in rows:
                pol = _v(r, "Guideline / Policy")
                if pol: policies.append({
                    "policy":    pol,
                    "authority": _v(r, "Issuing Authority"),
                    "relevance": _v(r, "Why It Applies to Nascent"),
                    "query":     _v(r, "Ready-to-Use Pre-Bid Query"),
                })
            if policies:
                profile["prebid_policies"] = policies
            result["tabs_read"].append("Pre-Bid Policies")
        except Exception as e:
            result["tabs_skipped"].append(f"Pre-Bid Policies ({e})")

        # ── Evaluation Methods ────────────────────────────────
        try:
            rows = _rows(sh.worksheet("Evaluation Methods"))
            methods = []
            for r in rows:
                m = _v(r, "Method")
                if m: methods.append({
                    "method":   m,
                    "name":     _v(r, "Full Name"),
                    "how":      _v(r, "How It Works"),
                    "strategy": _v(r, "Nascent Strategy"),
                })
            if methods:
                profile["evaluation_methods"] = methods
            result["tabs_read"].append("Evaluation Methods")
        except Exception as e:
            result["tabs_skipped"].append(f"Evaluation Methods ({e})")

        # ── Save ─────────────────────────────────────────────
        _save_local(profile)
        result["status"]  = "success"
        result["message"] = (
            f"Pulled {len(result['tabs_read'])} tabs: {', '.join(result['tabs_read'])}."
            + (f" Skipped: {', '.join(result['tabs_skipped'])}." if result["tabs_skipped"] else "")
        )
        return result

    except Exception as e:
        return {"error": str(e), "status": "failed"}


# ══════════════════════════════════════════════════════════════
# PUSH — App → Sheet
# ══════════════════════════════════════════════════════════════
def push_to_sheet() -> dict:
    gc = _connect()
    if not gc:
        return {"error": "Cannot connect — check GDRIVE_CREDENTIALS env var", "status": "failed"}
    if not PROFILE_PATH.exists():
        return {"error": "nascent_profile.json not found", "status": "failed"}

    profile = _load_local()
    result  = {"tabs_written": [], "tabs_skipped": []}

    try:
        sh = gc.open_by_key(SHEET_ID)

        # ── Push Finance ──────────────────────────────────────
        try:
            fin = profile.get("finance", {})
            tv  = fin.get("turnover_by_year", {})
            if tv:
                rows = [["Financial Year","Annual Turnover (Rs. Cr)","Net Worth (Rs. Cr)","CA Name"]]
                nw = fin.get("net_worth_cr","")
                ca = fin.get("ca_name","")
                for fy, cr in sorted(tv.items()):
                    rows.append([fy, cr, nw, ca])
                ws = sh.worksheet("Finance")
                ws.batch_clear(["A2:D20"])
                ws.update("A2", rows[1:])
                result["tabs_written"].append("Finance")
        except Exception as e:
            result["tabs_skipped"].append(f"Finance ({e})")

        # ── Push Bid_Rules ────────────────────────────────────
        try:
            rules = profile.get("bid_rules", {})
            rows  = [["Rule_Type", "Keyword", "Remarks"]]
            rows.append(["value_range", f"min_cr={rules.get('min_project_value_cr',0.5)}", "Min project value in Cr"])
            rows.append(["value_range", f"max_cr={rules.get('max_project_value_cr',150)}", "Max project value in Cr"])
            remarks = rules.get("do_not_bid_remarks", {})
            for kw in sorted(rules.get("do_not_bid",[])):
                rows.append(["do_not_bid", kw, remarks.get(kw,"")])
            for kw in sorted(rules.get("preferred_sectors",[])):
                rows.append(["preferred_sector", kw, ""])
            for kw in sorted(rules.get("conditional",[])):
                rows.append(["conditional", kw, "Raise pre-bid query"])
            try:
                ws = sh.worksheet("Bid_Rules")
            except:
                ws = sh.add_worksheet("Bid_Rules", rows=300, cols=4)
            ws.clear()
            ws.update("A1", rows)
            result["tabs_written"].append("Bid_Rules")
        except Exception as e:
            result["tabs_skipped"].append(f"Bid_Rules ({e})")

        # ── Push Projects ─────────────────────────────────────
        try:
            projects = profile.get("projects", [])
            if projects:
                cols = ["Project Name","Client","Client Type\n(Govt/PSU/Private)","State",
                        "Value (Rs. Cr)","Status\n(Completed/Ongoing)","Nascent Role\n(Solo/Consortium/Sub)",
                        "Project Category Tags","Scope Summary","Technology & Tools",
                        "Start Date","End Date / Go-Live","Duration","Tender Ref",
                        "Client Contact Person","Contact Number","Email ID",
                        "Supporting Docs Available","Description of Services Provided","Team Size"]
                rows = [cols]
                for p in projects:
                    rows.append([
                        p.get("name",""),       p.get("client",""),
                        p.get("client_type",""),p.get("state",""),
                        p.get("value_cr",""),   p.get("status",""),
                        p.get("role",""),        p.get("tags",""),
                        p.get("scope",""),       p.get("technologies",""),
                        p.get("start_date",""), p.get("end_date",""),
                        p.get("duration",""),   p.get("tender_ref",""),
                        p.get("contact_person",""),p.get("contact_phone",""),
                        p.get("contact_email",""),p.get("docs_available",""),
                        p.get("description",""),p.get("team_size",""),
                    ])
                ws = sh.worksheet("Projects")
                ws.batch_clear(["A2:T50"])
                ws.update("A2", rows[1:])
                result["tabs_written"].append("Projects")
        except Exception as e:
            result["tabs_skipped"].append(f"Projects ({e})")

        result["status"]  = "success"
        result["message"] = (
            f"Pushed to: {', '.join(result['tabs_written'])}."
            + (f" Skipped: {', '.join(result['tabs_skipped'])}." if result["tabs_skipped"] else "")
        )
        return result

    except Exception as e:
        return {"error": str(e), "status": "failed"}


# ── Profile helpers ───────────────────────────────────────────
def load_local_profile() -> dict:
    return _load_local()

def profile_to_ai_context(profile: dict) -> str:
    if not profile: return "Profile data unavailable."
    co    = profile.get("company", {})
    fin   = profile.get("finance", {})
    emp   = profile.get("employees", {})
    certs = profile.get("certifications", {})
    proj  = profile.get("projects", [])
    tv    = fin.get("turnover_by_year", {})
    tv_lines = "\n".join(f"  FY {fy}: ₹{cr} Cr" for fy,cr in sorted(tv.items())[-4:]) if tv else "  Not available"
    cert_lines = "\n".join(
        f"  {c.get('standard',k)} — valid till {c.get('valid_till','?')}"
        for k,c in certs.items()
    ) if certs else "  CMMI V2.0 L3, ISO 9001, ISO 27001, ISO 20000"
    proj_lines = "".join(
        f"  {i}. {p.get('name','')} | {p.get('client','')} | ₹{p.get('value_cr','')} Cr | {p.get('status','')} | {p.get('scope','')[:80]}\n"
        for i,p in enumerate(proj[:12],1)
    )
    return f"""NASCENT INFO TECHNOLOGIES PVT. LTD. — LIVE PROFILE:
Company: {co.get('name','')} | CIN: {co.get('cin','')} | MSME: {co.get('msme_udyam','')}
PAN: {co.get('pan','')} | GSTIN: {co.get('gstin','')}
Employees: {emp.get('total', co.get('total_employees',67))} (GIS: {emp.get('gis_staff',11)}, Dev: {emp.get('it_dev_staff',21)})
Signatory: {co.get('authorised_signatory','Hitesh Patel')} | POA: {co.get('poa_validity','')}
Address: {co.get('address','')}

FINANCIALS:
{tv_lines}
  Average (last 3yr): ₹{fin.get('avg_turnover_last_3_fy','17.18')} Cr | Net Worth: ₹{fin.get('net_worth_cr','26.09')} Cr

CERTIFICATIONS:
{cert_lines}

KEY PROJECTS ({len(proj)} total):
{proj_lines}"""
