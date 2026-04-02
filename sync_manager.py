"""
sync_manager.py — Google Sheets two-way sync
Sheet: Nascent_Tender_Master_v4
ALL field names match EXACTLY what loadProfile() reads in index.html.
Sync only when user presses button — never automatic.
"""
import json, os
from pathlib import Path
from datetime import datetime

SHEET_ID     = "1lgq44BOQaOpQFuAb-yyPcEGfQME1Wa6jvJKMg1_C_Y8"
BASE_DIR     = Path(__file__).parent
PROFILE_PATH = BASE_DIR / "nascent_profile.json"


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
    all_rows = ws.get_all_values()
    if not all_rows: return []
    headers = all_rows[0]
    return [
        {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        for row in all_rows[1:]
        if any(c.strip() for c in row)
    ]


def _v(d, *keys):
    for k in keys:
        val = d.get(k, "")
        if val and str(val).strip() and str(val).strip().upper() not in ("PENDING - UPDATE", "PENDING", "N/A", ""):
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
        "version": "4.0",
    }
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False))


def load_local_profile() -> dict:
    return _load_local()


def load_combined_profile() -> dict:
    """Load profile — used by ai_analyzer for context."""
    return _load_local()


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

        # ── Company Profile ──
        try:
            rows = _rows(sh.worksheet("Company Profile"))
            cp   = {r.get("Field", "").strip(): r.get("Details", "").strip()
                    for r in rows if r.get("Field", "").strip()}

            def gf(*keys):
                for k in keys:
                    v = cp.get(k, "")
                    if v and "PENDING" not in v.upper(): return v
                return ""

            company = profile.get("company", {})
            updates = {
                "name":                  gf("Company Name"),
                "cin":                   gf("CIN"),
                "pan":                   gf("PAN"),
                "gstin":                 gf("GSTIN"),
                "udyam":                 gf("Udyam Registration"),
                "msme_udyam":            gf("Udyam Registration"),
                "legal_status":          gf("Legal Status"),
                "year_of_incorporation": gf("Year of Incorporation"),
                "type":                  gf("Type of Organization"),
                "email":                 gf("Corporate Email"),
                "tender_email":          gf("Tender Email"),
                "phone":                 gf("Registered Office Phone"),
                "website":               gf("Website"),
                "address":               gf("Registered Address"),
                "authorised_signatory":  gf("Authorised Signatory"),
                "total_employees":       gf("Total Employees"),
                "gis_staff":             gf("GIS Staff Count"),
                "office_locations":      gf("Office Locations"),
            }
            for k, v in updates.items():
                if v: company[k] = v

            poa = gf("POA Validity")
            if poa: company["poa_validity"] = poa
            if poa and "2026" in poa:
                company["poa_alert"] = f"POA validity: {poa} — check if renewal needed"

            profile["company"] = company
            result["fields_updated"].append(f"company ({sum(1 for v in updates.values() if v)} fields)")
            result["tabs_read"].append("Company Profile")
        except Exception as e:
            result["tabs_skipped"].append(f"Company Profile ({e})")

        # ── Key Contacts ──
        try:
            rows = _rows(sh.worksheet("Key Contacts"))
            co   = profile.setdefault("company", {})
            for r in rows:
                role = _v(r, "Department / Role")
                name = _v(r, "Name")
                if not name: continue
                if "authorized signatory" in role.lower() or "authorised" in role.lower():
                    desig = _v(r, "Designation")
                    co["authorised_signatory"] = f"{name}, {desig}" if desig else name
                if "managing director" in role.lower():
                    co["md"] = name
            result["tabs_read"].append("Key Contacts")
        except Exception as e:
            result["tabs_skipped"].append(f"Key Contacts ({e})")

        # ── POA & Authorization ──
        try:
            rows = _rows(sh.worksheet("POA & Authorization"))
            co   = profile.setdefault("company", {})
            poas = []
            for r in rows:
                doc = _v(r, "Document")
                if not doc: continue
                issued_to = _v(r, "Issued To")
                valid_to  = _v(r, "Valid To")
                poas.append({
                    "document":    doc,
                    "issued_to":   issued_to,
                    "designation": _v(r, "Designation"),
                    "issued_by":   _v(r, "Issued By"),
                    "valid_from":  _v(r, "Valid From"),
                    "valid_to":    valid_to,
                    "status":      _v(r, "Status"),
                })
                if issued_to and "PENDING" not in issued_to.upper() and valid_to:
                    co["poa_validity"] = f"{_v(r, 'Valid From')} to {valid_to}"
                    if "2026" in valid_to or "2025" in valid_to:
                        co["poa_alert"] = f"POA expires {valid_to} — renew before tender submission"
            profile["poa_authorization"] = poas
            result["tabs_read"].append("POA & Authorization")
        except Exception as e:
            result["tabs_skipped"].append(f"POA & Authorization ({e})")

        # ── Finance ──
        try:
            rows  = _rows(sh.worksheet("Finance"))
            tv    = {}
            nw_cr = ""; ca_name = ""
            for r in rows:
                fy  = _v(r, "Financial Year")
                cr  = _v(r, "Annual Turnover (Rs. Cr)")
                nw  = _v(r, "Net Worth (Rs. Cr)")
                ca  = _v(r, "CA Name")
                if fy and "-" in fy and len(fy) == 7:
                    try:
                        tv[fy] = float(cr)
                        if nw: nw_cr = nw
                        if ca: ca_name = ca
                    except: pass
            if tv:
                last3 = sorted(tv.keys())[-3:]
                last2 = sorted(tv.keys())[-2:]
                avg3  = round(sum(tv[k] for k in last3) / len(last3), 2)
                avg2  = round(sum(tv[k] for k in last2) / len(last2), 2)
                profile["finance"] = {
                    "turnover_by_year":       tv,
                    "avg_turnover_last_3_fy": avg3,
                    "avg_turnover_last_2_fy": avg2,
                    "avg_turnover_cr":        avg3,
                    "net_worth_cr":           nw_cr,
                    "ca_name":                ca_name,
                    "solvency_amount_cr":     "",
                }
                result["fields_updated"].append(
                    f"finance ({len(tv)} years, avg3=₹{avg3}Cr, networth=₹{nw_cr}Cr)"
                )
            result["tabs_read"].append("Finance")
        except Exception as e:
            result["tabs_skipped"].append(f"Finance ({e})")

        # ── Certifications ──
        try:
            rows  = _rows(sh.worksheet("Certifications"))
            certs = profile.get("certifications", {})
            for r in rows:
                name       = _v(r, "Cert_Name")
                standard   = _v(r, "Standard") or name
                valid_till = _v(r, "Valid_Till")
                if not name: continue
                nl = name.lower()
                if "cmmi" in nl:
                    certs["cmmi"] = {"level": name, "version": standard, "valid_to": valid_till,
                                     "valid_till": valid_till, "status": _v(r, "Status") or "Active"}
                elif "9001" in name:
                    certs["iso_9001"] = {"standard": standard, "valid_to": valid_till,
                                         "valid_till": valid_till, "cert_no": "", "status": _v(r, "Status") or "Active"}
                elif "27001" in name:
                    certs["iso_27001"] = {"standard": standard, "valid_to": valid_till,
                                          "valid_till": valid_till, "cert_no": "", "status": _v(r, "Status") or "Active"}
                elif "20000" in name:
                    certs["iso_20000"] = {"standard": standard, "valid_to": valid_till,
                                          "valid_till": valid_till, "cert_no": "", "status": _v(r, "Status") or "Active"}
                else:
                    key = name.lower().replace(" ", "_").replace("/", "_")[:20]
                    certs[key] = {"standard": standard, "valid_to": valid_till,
                                  "valid_till": valid_till, "status": _v(r, "Status") or "Active"}
            profile["certifications"] = certs
            result["fields_updated"].append(f"certifications ({len(certs)} certs)")
            result["tabs_read"].append("Certifications")
        except Exception as e:
            result["tabs_skipped"].append(f"Certifications ({e})")

        # ── Employees ──
        try:
            rows      = _rows(sh.worksheet("Employees"))
            emp_list  = []
            gis_count = it_count = 0
            for r in rows:
                name = _v(r, "Employee Name")
                if not name: continue
                is_gis = r.get("GIS Staff (Y/N)", "").strip().lower() == "yes"
                is_it  = r.get("IT/Dev Staff (Y/N)", "").strip().lower() == "yes"
                if is_gis: gis_count += 1
                if is_it:  it_count  += 1
                emp_list.append({
                    "name":        name,
                    "designation": _v(r, "Designation"),
                    "department":  _v(r, "Department / Function"),
                    "years":       _v(r, "Years In Service"),
                    "is_gis":      is_gis,
                    "is_it":       is_it,
                    "is_key":      r.get("Key Person for Tenders (Y/N)", "").strip().lower() == "yes",
                })
            total = len(emp_list)
            profile["employees"] = {
                "total": total, "total_confirmed": total, "total_listed": total,
                "gis_staff": gis_count, "it_dev_staff": it_count,
                "gis_specialists": gis_count,
                "list": emp_list,
            }
            profile.setdefault("company", {})["total_employees"] = str(total)
            result["fields_updated"].append(
                f"employees ({total} total, {gis_count} GIS, {it_count} IT/Dev)"
            )
            result["tabs_read"].append("Employees")
        except Exception as e:
            result["tabs_skipped"].append(f"Employees ({e})")

        # ── Projects ──
        try:
            rows     = _rows(sh.worksheet("Projects"))
            projects = []
            for r in rows:
                name = _v(r, "Project Name")
                if not name: continue
                # tags: handle both string and list
                raw_tags = _v(r, "Project Category Tags")
                if isinstance(raw_tags, list):
                    tags = raw_tags
                elif raw_tags:
                    tags = [t.strip() for t in raw_tags.replace(";", ",").split(",") if t.strip()]
                else:
                    tags = []
                projects.append({
                    "name":           name,
                    "client":         _v(r, "Client"),
                    "client_type":    _v(r, "Client Type\n(Govt/PSU/Private)", "Client Type"),
                    "state":          _v(r, "State"),
                    "value_cr":       _v(r, "Value (Rs. Cr)"),
                    "value_display":  _v(r, "Value (Rs. Cr)"),
                    "status":         _v(r, "Status\n(Completed/Ongoing)", "Status"),
                    "role":           _v(r, "Nascent Role\n(Solo/Consortium/Sub)", "Nascent Role"),
                    "tags":           tags,
                    "scope":          _v(r, "Scope Summary"),
                    "technologies":   _v(r, "Technology & Tools"),
                    "start_date":     _v(r, "Start Date"),
                    "end_date":       _v(r, "End Date / Go-Live"),
                    "duration":       _v(r, "Duration"),
                    "tender_ref":     _v(r, "Tender Ref"),
                    "contact_person": _v(r, "Client Contact Person"),
                    "contact_phone":  _v(r, "Contact Number"),
                    "contact_email":  _v(r, "Email ID"),
                    "docs_available": _v(r, "Supporting Docs Available"),
                    "description":    _v(r, "Description of Services Provided"),
                    "team_size":      _v(r, "Team Size"),
                })
            if projects:
                profile["projects"] = projects
                result["fields_updated"].append(f"projects ({len(projects)})")
            result["tabs_read"].append("Projects")
        except Exception as e:
            result["tabs_skipped"].append(f"Projects ({e})")

        # ── Technology Stack ──
        try:
            rows  = _rows(sh.worksheet("Technology Stack"))
            techs = []
            for r in rows:
                t = _v(r, "Technology / Tool")
                if t: techs.append(t)
            profile["capabilities"] = {
                "tech_stack":    techs,
                "not_available": ["CERT-In", "STQC", "SAP Partner", "Oracle Partner"],
            }
            profile["key_technologies"] = techs
            result["tabs_read"].append("Technology Stack")
        except Exception as e:
            result["tabs_skipped"].append(f"Technology Stack ({e})")

        # ── Bank Details ──
        try:
            rows  = _rows(sh.worksheet("Bank Details"))
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
                co = profile.setdefault("company", {})
                co["bank"]      = banks[0].get("bank_name", "")
                co["bank_ifsc"] = banks[0].get("ifsc", "")
            result["tabs_read"].append("Bank Details")
        except Exception as e:
            result["tabs_skipped"].append(f"Bank Details ({e})")

        # ── Bid_Rules ──
        try:
            rows = _rows(sh.worksheet("Bid_Rules"))
            dnb  = []; pref = []; cond = []
            min_cr = 0.5; max_cr = 150.0
            for r in rows:
                rtype   = r.get("Rule_Type", "").strip().lower()
                keyword = r.get("Keyword", "").strip().lower()
                if not keyword or keyword == "keyword": continue
                if rtype == "do_not_bid":         dnb.append(keyword)
                elif rtype == "preferred_sector":  pref.append(keyword)
                elif rtype == "conditional":       cond.append(keyword)
                elif rtype == "value_range":
                    if "min_cr=" in keyword:
                        try: min_cr = float(keyword.split("=")[1])
                        except: pass
                    elif "max_cr=" in keyword:
                        try: max_cr = float(keyword.split("=")[1])
                        except: pass
            ex = profile.get("bid_rules", {})
            profile["bid_rules"] = {
                "do_not_bid":           sorted(set(dnb)) or ex.get("do_not_bid", []),
                "preferred_sectors":    sorted(set(pref)) or ex.get("preferred_sectors", []),
                "conditional":          sorted(set(cond)) or ex.get("conditional", []),
                "min_project_value_cr": min_cr,
                "max_project_value_cr": max_cr,
                "do_not_bid_remarks":   ex.get("do_not_bid_remarks", {}),
            }
            result["fields_updated"].append(
                f"bid_rules ({len(dnb)} no-bid, {len(pref)} preferred, {len(cond)} conditional)"
            )
            result["tabs_read"].append("Bid_Rules")
        except Exception as e:
            result["tabs_skipped"].append(f"Bid_Rules ({e})")

        # ── Pre-Bid Policies ──
        try:
            rows     = _rows(sh.worksheet("Pre-Bid Policies"))
            policies = []
            for r in rows:
                pol = _v(r, "Guideline / Policy")
                if pol:
                    policies.append({
                        "policy":    pol,
                        "authority": _v(r, "Issuing Authority"),
                        "relevance": _v(r, "Why It Applies to Nascent"),
                        "query":     _v(r, "Ready-to-Use Pre-Bid Query"),
                    })
            if policies: profile["prebid_policies"] = policies
            result["tabs_read"].append("Pre-Bid Policies")
        except Exception as e:
            result["tabs_skipped"].append(f"Pre-Bid Policies ({e})")

        # ── Evaluation Methods ──
        try:
            rows    = _rows(sh.worksheet("Evaluation Methods"))
            methods = []
            for r in rows:
                m = _v(r, "Method")
                if m:
                    methods.append({
                        "method":   m,
                        "name":     _v(r, "Full Name"),
                        "how":      _v(r, "How It Works"),
                        "strategy": _v(r, "Nascent Strategy"),
                    })
            if methods: profile["evaluation_methods"] = methods
            result["tabs_read"].append("Evaluation Methods")
        except Exception as e:
            result["tabs_skipped"].append(f"Evaluation Methods ({e})")

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
        return {"error": "Cannot connect — check GDRIVE_CREDENTIALS", "status": "failed"}
    if not PROFILE_PATH.exists():
        return {"error": "nascent_profile.json not found", "status": "failed"}

    profile = _load_local()
    result  = {"tabs_written": [], "tabs_skipped": []}

    try:
        sh = gc.open_by_key(SHEET_ID)

        # Finance
        try:
            fin = profile.get("finance", {})
            tv  = fin.get("turnover_by_year", {})
            if tv:
                rows = [["Financial Year", "Annual Turnover (Rs. Cr)", "Net Worth (Rs. Cr)", "CA Name"]]
                for fy, cr in sorted(tv.items()):
                    rows.append([fy, cr, fin.get("net_worth_cr", ""), fin.get("ca_name", "")])
                ws = sh.worksheet("Finance")
                ws.batch_clear(["A2:D20"])
                ws.update("A2", rows[1:])
                result["tabs_written"].append("Finance")
        except Exception as e:
            result["tabs_skipped"].append(f"Finance ({e})")

        # Bid_Rules
        try:
            rules = profile.get("bid_rules", {})
            rows  = [["Rule_Type", "Keyword", "Remarks"]]
            rows.append(["value_range", f"min_cr={rules.get('min_project_value_cr', 0.5)}", "Min Cr"])
            rows.append(["value_range", f"max_cr={rules.get('max_project_value_cr', 150)}", "Max Cr"])
            rem = rules.get("do_not_bid_remarks", {})
            for kw in sorted(rules.get("do_not_bid", [])): rows.append(["do_not_bid", kw, rem.get(kw, "")])
            for kw in sorted(rules.get("preferred_sectors", [])): rows.append(["preferred_sector", kw, ""])
            for kw in sorted(rules.get("conditional", [])): rows.append(["conditional", kw, "Raise pre-bid query"])
            try: ws = sh.worksheet("Bid_Rules")
            except: ws = sh.add_worksheet("Bid_Rules", rows=300, cols=4)
            ws.clear()
            ws.update("A1", rows)
            result["tabs_written"].append("Bid_Rules")
        except Exception as e:
            result["tabs_skipped"].append(f"Bid_Rules ({e})")

        # Projects — tags as string for sheet
        try:
            projects = profile.get("projects", [])
            if projects:
                cols = ["Project Name", "Client", "Client Type\n(Govt/PSU/Private)", "State",
                        "Value (Rs. Cr)", "Status\n(Completed/Ongoing)",
                        "Nascent Role\n(Solo/Consortium/Sub)", "Project Category Tags",
                        "Scope Summary", "Technology & Tools", "Start Date", "End Date / Go-Live",
                        "Duration", "Tender Ref", "Client Contact Person", "Contact Number", "Email ID",
                        "Supporting Docs Available", "Description of Services Provided", "Team Size"]
                rows = [cols]
                for p in projects:
                    tags = p.get("tags", "")
                    if isinstance(tags, list):
                        tags = ", ".join(tags)
                    rows.append([
                        p.get("name", ""), p.get("client", ""), p.get("client_type", ""), p.get("state", ""),
                        p.get("value_cr", ""), p.get("status", ""), p.get("role", ""), tags,
                        p.get("scope", ""), p.get("technologies", ""), p.get("start_date", ""),
                        p.get("end_date", ""), p.get("duration", ""), p.get("tender_ref", ""),
                        p.get("contact_person", ""), p.get("contact_phone", ""), p.get("contact_email", ""),
                        p.get("docs_available", ""), p.get("description", ""), p.get("team_size", ""),
                    ])
                ws = sh.worksheet("Projects")
                ws.batch_clear(["A2:T100"])
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


def profile_to_ai_context(profile: dict) -> str:
    if not profile: return "Profile data unavailable."
    co    = profile.get("company", {})
    fin   = profile.get("finance", {})
    emp   = profile.get("employees", {})
    certs = profile.get("certifications", {})
    proj  = profile.get("projects", [])
    tv    = fin.get("turnover_by_year", {})
    tv_lines = "\n".join(f"  FY {fy}: ₹{cr} Cr" for fy, cr in sorted(tv.items())[-4:]) if tv else "  Not available"
    cert_lines = "\n".join(
        f"  {c.get('standard', c.get('level', k))} — valid till {c.get('valid_to', c.get('valid_till', '?'))}"
        for k, c in certs.items() if isinstance(c, dict)
    ) if certs else "  CMMI V2.0 L3, ISO 9001, ISO 27001, ISO 20000"
    proj_lines = "".join(
        f"  {i}. {p.get('name', '')} | {p.get('client', '')} | ₹{p.get('value_cr', '')} Cr"
        f" | {p.get('status', '')} | {str(p.get('scope', ''))[:80]}\n"
        for i, p in enumerate(proj[:12], 1)
    )
    return f"""NASCENT INFO TECHNOLOGIES — LIVE PROFILE:
{co.get('name', '')} | CIN: {co.get('cin', '')} | MSME: {co.get('udyam', co.get('msme_udyam', ''))}
PAN: {co.get('pan', '')} | GSTIN: {co.get('gstin', '')}
Employees: {emp.get('total', co.get('total_employees', 67))} (GIS: {emp.get('gis_staff', emp.get('gis_specialists', 11))}, IT: {emp.get('it_dev_staff', 21)})
Signatory: {co.get('authorised_signatory', 'Hitesh Patel')} | POA: {co.get('poa_validity', '')}

FINANCIALS:
{tv_lines}
  Avg (3yr): ₹{fin.get('avg_turnover_last_3_fy', fin.get('avg_turnover_cr', '17.18'))} Cr | Net Worth: ₹{fin.get('net_worth_cr', '26.09')} Cr

CERTIFICATIONS:
{cert_lines}

PROJECTS ({len(proj)} total):
{proj_lines}"""
