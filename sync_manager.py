"""
sync_manager.py — Live Master Sheet bridge
Reads Nascent profile from Google Sheets (Nascent_Tender_Master_v4).
Falls back to nascent_profile.json if Sheet is unavailable.
Security: google_key.json is loaded from GDRIVE_CREDENTIALS env var on Render.
"""
import json, os
from pathlib import Path

SHEET_ID = "1lgq44BOQaOpQFuAb-yyPcEGfQME1Wa6jvJKMg1_C_Y8"
_profile_cache = None  # In-memory cache — refreshed on startup

def _get_credentials():
    """
    Load service account credentials safely:
    1. Try GDRIVE_CREDENTIALS env var (Render production)
    2. Try google_key.json local file (development only)
    """
    # Option 1: Render Secret / Env Var (production)
    creds_json = os.environ.get("GDRIVE_CREDENTIALS", "").strip()
    if creds_json:
        try:
            return json.loads(creds_json)
        except json.JSONDecodeError:
            pass

    # Option 2: Local file (development only — never commit this)
    local_key = Path(__file__).parent / "google_key.json"
    if local_key.exists():
        try:
            return json.loads(local_key.read_text())
        except Exception:
            pass

    return None


def load_combined_profile(force_refresh=False):
    """
    Returns Nascent profile dict.
    Tries Google Sheet first, falls back to nascent_profile.json.
    """
    global _profile_cache
    if _profile_cache and not force_refresh:
        return _profile_cache

    creds_data = _get_credentials()
    if creds_data:
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ]
            creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(SHEET_ID)

            identity_rows   = sh.worksheet("Firm_Identity").get_all_records()
            financial_rows  = sh.worksheet("Financial_Credentials").get_all_records()
            project_rows    = sh.worksheet("Project_Experience").get_all_records()

            id_data = identity_rows[0] if identity_rows else {}

            # Build turnover dict + compute average
            turnover = {str(r.get("Year","")): float(str(r.get("Turnover_INR",0)).replace(",","") or 0)
                        for r in financial_rows if r.get("Year")}
            avg_turnover = round(sum(turnover.values()) / len(turnover), 2) if turnover else 0

            profile = {
                "source": "google_sheet",
                "firm_identity": {
                    "legal_name":  id_data.get("Legal_Name", "Nascent Info Technologies Pvt. Ltd."),
                    "cin":         id_data.get("CIN", "U72200GJ2006PTC048723"),
                    "pan":         id_data.get("PAN_Number", "AACCN3670J"),
                    "gstin":       id_data.get("GST_Number", "24AACCN3670J1ZG"),
                    "udyam":       id_data.get("UDYAM", "UDYAM-GJ-01-0007420"),
                    "address":     id_data.get("Registered_Address", "Ahmedabad, Gujarat"),
                    "employees":   id_data.get("Employee_Count", 67),
                    "signatory":   id_data.get("Signatory", "Hitesh Patel"),
                },
                "financial_strength": {
                    "turnover":         turnover,
                    "average_turnover": avg_turnover,
                    "net_worth":        id_data.get("Net_Worth", "26.09 Cr"),
                },
                "technical_credentials": {
                    "projects": project_rows,
                    "active_projects": sum(1 for p in project_rows if str(p.get("Status","")).lower() == "ongoing"),
                },
            }

            _profile_cache = profile
            print(f"✅ Live Sheet loaded — {len(project_rows)} projects, avg turnover ₹{avg_turnover} Cr")
            return profile

        except Exception as e:
            print(f"⚠️ Sheet sync failed: {e} — using local fallback")

    # Fallback to local JSON
    try:
        local_json = Path(__file__).parent / "nascent_profile.json"
        with open(local_json, "r") as f:
            profile = json.load(f)
        profile["source"] = "local_json"
        _profile_cache = profile
        print("📁 Using nascent_profile.json (local fallback)")
        return profile
    except Exception as e:
        print(f"❌ Local profile also failed: {e}")
        return {}


def profile_to_ai_context(profile: dict) -> str:
    """Convert profile dict to the text block the AI prompt needs."""
    if not profile:
        return "Profile data unavailable."

    fi = profile.get("firm_identity", {})
    fs = profile.get("financial_strength", {})
    tc = profile.get("technical_credentials", {})
    projects = tc.get("projects", [])

    # Turnover lines
    tv = fs.get("turnover", {})
    tv_lines = "\n".join([f"  - FY {yr}: ₹{val} Cr" for yr, val in sorted(tv.items())]) if tv else "  Not available"
    avg = fs.get("average_turnover", 0)

    # Project lines
    proj_lines = ""
    for i, p in enumerate(projects[:12], 1):
        name   = p.get("Project_Name") or p.get("project_name") or p.get("name","")
        client = p.get("Client") or p.get("client","")
        value  = p.get("Value_Cr") or p.get("value","")
        status = p.get("Status") or p.get("status","")
        scope  = p.get("Scope") or p.get("scope","")
        proj_lines += f"{i}. {name} | {client} | ₹{value} Cr | {status} | {scope}\n"

    return f"""NASCENT INFO TECHNOLOGIES PVT. LTD. — LIVE PROFILE (source: {profile.get('source','unknown')}):

BASICS:
- Legal Name: {fi.get('legal_name')} | CIN: {fi.get('cin')}
- MSME/UDYAM: {fi.get('udyam')} | PAN: {fi.get('pan')} | GSTIN: {fi.get('gstin')}
- Employees: {fi.get('employees')} | Signatory: {fi.get('signatory')}
- Address: {fi.get('address')}

FINANCIALS:
{tv_lines}
- Average (3yr): ₹{avg} Cr | Net Worth: {fs.get('net_worth','')}

KEY PROJECTS ({len(projects)} total, {tc.get('active_projects',0)} active):
{proj_lines}"""
