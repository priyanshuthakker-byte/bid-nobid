"""
AI Analyzer v5 - Fixed
- Removed duplicate functions (normalize_status, merge_results, analyze_with_gemini)
- Defined GROQ_MODELS list
- Fixed verdict format (NO-BID not NO_BID)
- Multi-key rotation actually works now
- Better error messages
"""
import json
import re
import os
import urllib.request
import urllib.error
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.json"

# ─────────────────────────────────────────
# GROQ MODELS LIST (was missing — caused NameError crash)
# ─────────────────────────────────────────
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
]

# Free Gemini models — tried in order if one hits quota
GEMINI_MODELS = [
    "gemini-2.5-flash-lite",       # fastest free model (March 2026)
    "gemini-2.5-flash",             # best free model
    "gemini-2.0-flash",             # fallback
    "gemini-2.0-flash-lite",        # last fallback
    # NOTE: gemini-1.5-* and gemini-1.0-* are SHUT DOWN — return 404
]

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
def load_config() -> Dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Environment variables override config.json (for Render)
    if os.environ.get("GEMINI_API_KEY"):
        cfg["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
    if os.environ.get("GROQ_API_KEY"):
        cfg["groq_api_key"] = os.environ["GROQ_API_KEY"]
    # Multiple Gemini keys from env
    extra_keys = []
    for i in range(2, 6):
        k = os.environ.get(f"GEMINI_API_KEY_{i}")
        if k:
            extra_keys.append(k)
    if extra_keys:
        existing = cfg.get("gemini_api_keys", [])
        cfg["gemini_api_keys"] = list(set(existing + extra_keys))
    return cfg


def save_config(config: Dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_api_key() -> str:
    return load_config().get("gemini_api_key", "")


def get_all_api_keys() -> List[str]:
    """Return all configured Gemini API keys, deduped, no empty strings."""
    config = load_config()
    keys = list(config.get("gemini_api_keys", []))
    primary = config.get("gemini_api_key", "")
    if primary and primary not in keys:
        keys.insert(0, primary)
    return [k for k in keys if k and k.strip() and len(k.strip()) > 20]


# ─────────────────────────────────────────
# NASCENT PROFILE CONTEXT
# ─────────────────────────────────────────
NASCENT_CONTEXT = """
NASCENT INFO TECHNOLOGIES PVT. LTD. — COMPLETE PROFILE:

BASICS:
- Private Limited Company | Incorporated: 23 June 2006 | CIN: U72200GJ2006PTC048723
- 19 years in operation | HQ: Ahmedabad, Gujarat | No branch offices in other states
- MSME: UDYAM-GJ-01-0007420 (Lifetime) | PAN: AACCN3670J | GSTIN: 24AACCN3670J1ZG
- Authorised Signatory: Hitesh Patel (CAO) | MD: Maulik Bhagat

FINANCIALS:
- FY 2022-23: Rs.16.36 Cr | FY 2023-24: Rs.16.36 Cr | FY 2024-25: Rs.18.83 Cr
- Average last 3 FY: Rs.17.18 Cr | Net Worth: Rs.26.09 Cr

CERTIFICATIONS (all active):
- CMMI V2.0 Level 3 | Valid till 19-Dec-2026
- ISO 9001:2015 | Valid till 08-Sep-2028
- ISO/IEC 27001:2022 | Valid till 08-Sep-2028
- ISO/IEC 20000-1:2018 | Valid till 08-Sep-2028
- CERT-In: NOT HELD | STQC: NOT HELD | SAP: NOT PARTNER | Oracle: NOT PARTNER

EMPLOYEES: 67 total (11 GIS, 21 IT/Dev, rest PM/QA/BA/support)

TECHNOLOGY STACK:
- GIS: QGIS, ArcGIS, GeoServer, PostGIS, CityLayers 2.0 (OGC compliant)
- Backend: Java/Spring Boot (PRIMARY), Python, Node.js
- Frontend: React.js, Angular
- Mobile: Android Native, Flutter
- Database: PostgreSQL, MySQL, Oracle
- Cloud: AWS, Azure
- NOT primary: .NET/C#, MS SQL Server, SAP, Oracle ERP

KEY PROJECTS:
1. AMC GIS | Ahmedabad MC | Rs.10.55 Cr | Completed | Web GIS + Property Survey
2. PCSCL Smart City GIS+ERP | Pimpri-Chinchwad | Rs.61.19 Cr | Ongoing | Consortium
3. KVIC Geo Portal | KVIC Central PSU | Rs.5.15 Cr | Completed | Mobile GIS + PAN India
4. TCGL Tourism Portal | Tourism Corp Gujarat | Rs.9.31 Cr | Completed | Web Portal + GIS
5. JuMC GIS | Junagadh MC | Rs.9.78 Cr | Ongoing | Web GIS + Survey
6. VMC GIS+ERP | Vadodara MC | Rs.20.5 Cr | Completed | Consortium | GIS + ERP
7. BMC Mobile GIS | Bhavnagar MC | Rs.4.2 Cr | Completed | Mobile + Web GIS
8. AMC Heritage App | Ahmedabad MC | Rs.4.72 Cr | Completed | Mobile + AR/QR
9. CEICED eGov | Gujarat State | Rs.3.59 Cr | Ongoing | Web Portal + Mobile

BID DECISION RULES:
DO NOT BID: supply only, amc only, hardware procurement, civil construction, manpower outsourcing,
  defense procurement, CERT-In/STQC required without exemption, .NET-only stack
CONDITIONAL (raise pre-bid): office in specific state, 100+ employees, OEM authorization,
  turnover >50Cr, CERT-In preferred, specific ERP OEM
BID: GIS, web portal, mobile app, eGov, Smart City, ULB/Municipal, IT services
"""

# ─────────────────────────────────────────
# GEMINI API CALL — tries all models
# ─────────────────────────────────────────
def call_gemini(prompt: str, api_key: str) -> str:
    """Try each Gemini model in order. Returns text or raises."""
    last_error = None
    for model in GEMINI_MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
            }
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                logger.info(f"Gemini success: {model}")
                return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code in [429, 503, 500, 404]:
                logger.warning(f"Model {model} error {e.code} — trying next")
                last_error = f"HTTP {e.code}: {body[:80]}"
                continue
            raise Exception(f"Gemini HTTP {e.code}: {body[:100]}")
        except Exception as e:
            logger.warning(f"Model {model} failed: {e}")
            last_error = str(e)
            continue
    raise Exception(
        f"All Gemini models failed. Last error: {last_error}. "
        f"Add a new API key at aistudio.google.com/apikey"
    )


# ─────────────────────────────────────────
# GROQ FALLBACK
# ─────────────────────────────────────────
def call_groq(prompt: str, groq_key: str) -> str:
    """Call Groq API — free tier, 14,400 req/day."""
    last_error = None
    for model in GROQ_MODELS:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
            "temperature": 0.1,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + groq_key
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code in [429, 503]:
                last_error = f"HTTP {e.code}"
                continue
            raise Exception(f"Groq HTTP {e.code}: {body[:100]}")
        except Exception as e:
            last_error = str(e)
            continue
    raise Exception(f"All Groq models failed: {last_error}")


# ─────────────────────────────────────────
# JSON CLEANER
# ─────────────────────────────────────────
def clean_json(text: str) -> Dict:
    """Strip markdown fences and parse JSON."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object inside text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


# ─────────────────────────────────────────
# SMART TEXT CHUNKING
# ─────────────────────────────────────────
def smart_chunk(full_text: str) -> str:
    """Select most important sections for very long tenders."""
    if len(full_text) <= 10000:
        return full_text

    parts = []
    # Part 1: Beginning — NIT, dates, amounts
    parts.append(full_text[:4000])

    # Part 2: PQ/Eligibility section
    for kw in ["Pre-Qualification Criteria", "Eligibility Criteria",
               "Qualifying Criteria", "Section 2", "2.1 Pre"]:
        idx = full_text.find(kw)
        if idx != -1:
            parts.append(full_text[max(0, idx-100):idx+3000])
            break

    # Part 3: TQ/Technical evaluation
    for kw in ["Technical Evaluation Criteria", "Evaluation Criteria",
               "Technical Score", "Marking Scheme", "2.2 Technical"]:
        idx = full_text.find(kw)
        if idx != -1:
            parts.append(full_text[max(0, idx-100):idx+2500])
            break

    # Part 4: Scope of work
    for kw in ["Scope of Work", "Scope of Services", "Section 6",
               "Technology Stack", "Functionalities Required"]:
        idx = full_text.find(kw)
        if idx != -1:
            parts.append(full_text[max(0, idx-100):idx+3000])
            break

    # Part 5: Payment terms
    for kw in ["Payment Terms", "Payment Schedule", "7. Payment"]:
        idx = full_text.find(kw)
        if idx != -1:
            parts.append(full_text[max(0, idx-100):idx+2000])
            break

    # Part 6: Last 1500 chars
    parts.append(full_text[-1500:])

    combined = "\n\n[...]\n\n".join(parts)
    return combined[:12000]


# ─────────────────────────────────────────
# MAIN ANALYSIS PROMPT
# ─────────────────────────────────────────
def build_prompt(text_chunk: str, prebid_passed: bool) -> str:
    prebid_note = (
        "PRE-BID DEADLINE HAS NOT PASSED. For every gap or conditional item, "
        "write the EXACT pre-bid query text to send — cite exact clause number."
        if not prebid_passed else
        "PRE-BID DEADLINE HAS PASSED. Note gaps directly, no pre-bid queries needed."
    )
    return f"""You are a senior bid analyst at Nascent Info Technologies Pvt. Ltd.
Read this entire tender document carefully — every section, every clause, every table.
Extract ALL information with 100% accuracy. Do not skip anything. Do not assume.

{NASCENT_CONTEXT}

{prebid_note}

TENDER DOCUMENT:
{text_chunk}

Return ONLY a valid JSON object. No markdown fences. No explanation. Just the JSON.

CRITICAL RULES:
1. overall_recommendation must be exactly one of: "BID", "NO-BID", "CONDITIONAL" (use hyphen, no underscore)
2. Extract ALL PQ criteria word-for-word from the document — every row of the PQ table
3. Extract ALL TQ criteria with exact marks
4. nascent_status must be exactly: "Met", "Not Met", or "Conditional"
5. If no data found for a field, use "—" not null or empty string

{{
  "tender_no": "exact tender number",
  "org_name": "full organization name",
  "tender_name": "complete project title",
  "portal": "exact URL from document",
  "bid_start_date": "start date with time",
  "bid_submission_date": "deadline with time",
  "bid_opening_date": "opening date",
  "commercial_opening_date": "commercial opening or when intimated",
  "prebid_meeting": "date and mode or Not Applicable",
  "prebid_query_date": "deadline for queries",
  "estimated_cost": "amount with Rs.",
  "tender_fee": "amount and payment mode",
  "emd": "amount and payment mode",
  "emd_exemption": "MSME exemption clause as stated",
  "performance_security": "percentage and conditions",
  "contract_period": "duration with phases",
  "bid_validity": "days",
  "location": "project location",
  "contact": "email and phone",
  "jv_allowed": "JV/consortium text from document",
  "mode_of_selection": "QCBS/L1/etc with weightage",
  "tender_type": "contract type",
  "post_implementation": "AMC period",
  "technology_mandatory": "mandatory tech stack if any",
  "scope_items": [
    "Detailed scope point 1 with actual content from document",
    "Include phases, deliverables, technology, quantities, timelines"
  ],
  "pq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause number and page",
      "criteria": "EXACT WORD-FOR-WORD text from PQ table",
      "details": "supporting documents required",
      "nascent_status": "Met",
      "nascent_remark": "What Nascent has or lacks. If pre-bid needed, write exact query text."
    }}
  ],
  "tq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause number",
      "criteria": "EXACT text from TQ scoring table",
      "details": "Max Marks: X | Nascent Estimated: Y",
      "nascent_status": "Met",
      "nascent_remark": "Score justification with evidence from Nascent portfolio"
    }}
  ],
  "payment_terms": [
    "Milestone 1: trigger and percentage"
  ],
  "prebid_queries": [
    {{
      "clause": "clause reference",
      "query": "exact query text to send to the authority"
    }}
  ],
  "overall_recommendation": "BID",
  "recommendation_reason": "Specific reason with gaps and strengths",
  "notes": [
    "Critical action item 1"
  ]
}}"""


# ─────────────────────────────────────────
# STATUS NORMALIZER
# ─────────────────────────────────────────
def normalize_status(status_text: str) -> tuple:
    """Returns (display_status, color)"""
    s = str(status_text).lower()
    if "not met" in s or "critical" in s:
        return "Not Met", "RED"
    elif "conditional" in s or "partial" in s or "pending" in s:
        return "Conditional", "AMBER"
    elif "met" in s:
        return "Met", "GREEN"
    return "Review", "BLUE"


# ─────────────────────────────────────────
# VERDICT NORMALIZER — fixes NO_BID → NO-BID etc.
# ─────────────────────────────────────────
def normalize_verdict(rec: str) -> tuple:
    """Returns (verdict_string, color) — always uses hyphen format."""
    r = rec.lower().strip()
    if "no" in r and ("bid" in r):
        return "NO-BID", "RED"
    elif "conditional" in r:
        return "CONDITIONAL", "AMBER"
    elif "bid" in r:
        return "BID", "GREEN"
    return "REVIEW", "BLUE"


# ─────────────────────────────────────────
# MERGE AI RESULTS WITH REGEX FALLBACK
# ─────────────────────────────────────────
def merge_results(regex_data: Dict, ai_data: Dict,
                  prebid_passed: bool = False) -> Dict:
    """
    Merge AI results into regex_data.
    AI wins on every field where it has a real non-empty value.
    """
    if not ai_data or "error" in ai_data:
        return regex_data

    result = dict(regex_data)
    EMPTY = {"—", "Not mentioned", "Not specified", "To be confirmed",
             "Refer document", "", "As per tender", None}

    # Simple string fields — AI overrides
    FIELD_MAP = {
        "tender_no", "org_name", "tender_name", "portal",
        "bid_submission_date", "bid_opening_date", "bid_start_date",
        "commercial_opening_date", "prebid_meeting", "prebid_query_date",
        "estimated_cost", "tender_fee", "emd", "emd_exemption",
        "performance_security", "contract_period", "location", "contact",
        "jv_allowed", "mode_of_selection", "tender_type", "post_implementation",
        "bid_validity", "technology_mandatory",
    }
    for key in FIELD_MAP:
        val = ai_data.get(key)
        if val and str(val).strip() not in EMPTY:
            result[key] = str(val).strip()

    # Scope
    if ai_data.get("scope_items"):
        result["scope_items"] = ai_data["scope_items"]

    # PQ criteria
    if ai_data.get("pq_criteria"):
        pq_list = []
        for item in ai_data["pq_criteria"]:
            if not isinstance(item, dict):
                continue
            status, color = normalize_status(item.get("nascent_status", "Review"))
            pq_list.append({
                "sl_no": item.get("sl_no", ""),
                "clause_ref": item.get("clause_ref", "—"),
                "criteria": item.get("criteria", ""),
                "details": item.get("details", ""),
                "nascent_status": status,
                "nascent_color": color,
                "nascent_remark": item.get("nascent_remark", ""),
            })
        if pq_list:
            result["pq_criteria"] = pq_list

    # TQ criteria
    if ai_data.get("tq_criteria"):
        tq_list = []
        for item in ai_data["tq_criteria"]:
            if not isinstance(item, dict):
                continue
            status, color = normalize_status(item.get("nascent_status", "Review"))
            tq_list.append({
                "sl_no": item.get("sl_no", ""),
                "clause_ref": item.get("clause_ref", "—"),
                "criteria": item.get("criteria", ""),
                "details": item.get("details", ""),
                "nascent_status": status,
                "nascent_color": color,
                "nascent_remark": item.get("nascent_remark", ""),
            })
        if tq_list:
            result["tq_criteria"] = tq_list

    # Payment terms
    if ai_data.get("payment_terms"):
        result["payment_terms"] = ai_data["payment_terms"]

    # Pre-bid queries
    if ai_data.get("prebid_queries"):
        result["prebid_queries"] = ai_data["prebid_queries"]

    # Notes
    if ai_data.get("notes"):
        result["notes"] = ai_data["notes"]

    # Overall verdict — CRITICAL: normalize to hyphen format
    rec = ai_data.get("overall_recommendation", "")
    reason = ai_data.get("recommendation_reason", "")
    if rec:
        pq = result.get("pq_criteria", [])
        green = sum(1 for p in pq if p.get("nascent_color") == "GREEN")
        amber = sum(1 for p in pq if p.get("nascent_color") == "AMBER")
        red = sum(1 for p in pq if p.get("nascent_color") == "RED")
        verdict, color = normalize_verdict(rec)
        result["overall_verdict"] = {
            "verdict": verdict,
            "reason": reason,
            "color": color,
            "green": green,
            "amber": amber,
            "red": red,
        }
        # Also set top-level verdict for dashboard display
        result["verdict"] = verdict
        result["reason"] = reason

    return result


# ─────────────────────────────────────────
# MAIN ENTRY POINT — multi-key rotation + Groq fallback
# ─────────────────────────────────────────
def analyze_with_gemini(full_text: str,
                        prebid_passed: bool = False) -> Dict[str, Any]:
    """
    Try all Gemini API keys in rotation.
    Fall back to Groq if all Gemini keys exhausted.
    Returns the parsed AI result dict, or {"error": "..."} on full failure.
    """
    all_keys = get_all_api_keys()
    if not all_keys:
        return {
            "error": (
                "No Gemini API key configured. "
                "Go to Settings and add your key from aistudio.google.com/apikey"
            )
        }

    text_chunk = smart_chunk(full_text)
    prompt = build_prompt(text_chunk, prebid_passed)

    # Try each Gemini key
    for key_idx, api_key in enumerate(all_keys):
        logger.info(f"Trying Gemini key {key_idx+1}/{len(all_keys)}")
        try:
            response_text = call_gemini(prompt, api_key)
            result = clean_json(response_text)
            logger.info(f"Gemini success with key {key_idx+1}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error on key {key_idx+1}: {e}")
            # Don't try next key for JSON errors — it's a prompt issue
            return {
                "error": (
                    f"AI returned invalid JSON. "
                    f"Try uploading a cleaner PDF. Details: {str(e)[:80]}"
                )
            }

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"HTTP {e.code} on key {key_idx+1}: {body[:100]}")
            if e.code in [429, 503]:
                logger.warning(f"Key {key_idx+1} quota exceeded — trying next key")
                continue  # Try next key
            return {"error": f"Gemini API error {e.code}: {body[:100]}"}

        except Exception as e:
            err_str = str(e).lower()
            logger.error(f"Key {key_idx+1} failed: {e}")
            if "quota" in err_str or "429" in err_str or "exhausted" in err_str:
                logger.warning(f"Key {key_idx+1} quota — trying next key")
                continue  # Try next key
            # For other errors, try next key anyway
            continue

    # All Gemini keys exhausted — try Groq
    cfg = load_config()
    groq_key = (cfg.get("groq_api_key") or
                cfg.get("groq_key") or
                cfg.get("GROQ_API_KEY") or "")

    if groq_key and groq_key.strip():
        try:
            logger.info("All Gemini keys quota exceeded — trying Groq fallback")
            response_text = call_groq(prompt, groq_key.strip())
            result = clean_json(response_text)
            logger.info("Groq fallback succeeded")
            return result
        except json.JSONDecodeError:
            # Try regex extraction
            try:
                match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass
            return {"error": "Groq returned invalid JSON."}
        except Exception as e:
            logger.error(f"Groq fallback failed: {e}")

    return {
        "error": (
            "All API keys quota exceeded for today. "
            "Options:\n"
            "1. Add a new free Gemini key at aistudio.google.com/apikey\n"
            "2. Add a free Groq key at console.groq.com (14,400 req/day)\n"
            "3. Wait until tomorrow — quota resets at midnight IST"
        )
    }
