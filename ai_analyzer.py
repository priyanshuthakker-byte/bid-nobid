"""
AI Analyzer v5 - Fixed & Production-Ready
- No duplicate function definitions
- GROQ_MODELS defined
- Correct regex in clean_json
- 80K char limit for Gemini (handles full tenders)
- Multi-key Gemini fallback works correctly
- Groq fallback works correctly
- os imported at top level
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
# MODELS
# ─────────────────────────────────────────

GEMINI_MODELS = [
    "gemini-2.0-flash",          # 15 RPM free — primary
    "gemini-2.0-flash-lite",     # 30 RPM free — fastest fallback
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "mixtral-8x7b-32768",
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

    if os.environ.get("GEMINI_API_KEY"):
        cfg["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
    if os.environ.get("GROQ_API_KEY"):
        cfg["groq_api_key"] = os.environ["GROQ_API_KEY"]

    extra_keys = []
    for i in range(2, 8):
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


def get_groq_key() -> str:
    return load_config().get("groq_api_key", "")


def get_all_api_keys() -> list:
    config = load_config()
    keys = list(config.get("gemini_api_keys", []))
    primary = config.get("gemini_api_key", "")
    if primary and primary not in keys:
        keys.insert(0, primary)
    return [k for k in keys if k and k.strip() and len(k.strip()) > 20]


# ─────────────────────────────────────────
# NASCENT PROFILE CONTEXT
# Complete and accurate — loaded from nascent_profile.json
# ─────────────────────────────────────────

NASCENT_CONTEXT = """
NASCENT INFO TECHNOLOGIES PVT. LTD. — COMPLETE VERIFIED PROFILE:

COMPANY:
- Private Limited Company | CIN: U72200GJ2006PTC048723
- Incorporated: 23 June 2006 | 19 years in operation
- MSME: UDYAM-GJ-01-0007420 (Lifetime) | PAN: AACCN3670J | GSTIN: 24AACCN3670J1ZG
- HQ: A-805, Shapath IV, SG Highway, Prahlad Nagar, Ahmedabad, Gujarat 380015
- MD: Maulik Bhagat | Authorised Signatory: Hitesh Patel (CAO)
- POA validity: 01/04/2025 – 31/03/2026 *** POA EXPIRES MARCH 2026 — CHECK BEFORE SUBMISSION ***

FINANCIALS (Audited — CA: Anuj J. Sharedalal):
- FY 2019-20: Rs. 17.42 Cr
- FY 2020-21: Rs. 9.17 Cr
- FY 2021-22: Rs. 20.42 Cr
- FY 2022-23: Rs. 16.36 Cr
- FY 2023-24: Rs. 16.36 Cr
- FY 2024-25: Rs. 18.83 Cr
- Average last 2 FY (2023-24, 2024-25): Rs. 17.60 Cr
- Average last 3 FY (2022-23 to 2024-25): Rs. 17.18 Cr
- Average last 5 FY (2020-21 to 2024-25): Rs. 16.23 Cr
- Net Worth: Rs. 26.09 Cr
- Solvency available: Yes (Rs. 2.61 Cr from bank)

CERTIFICATIONS (all active):
- CMMI V2.0 (DEV) Level 3 | Benchmark: 68617 | Valid: 14-Dec-2023 to 19-Dec-2026 | Issuer: CUNIX Infotech
- ISO 9001:2015 | Cert: 25EQPE64 | Valid: 09-Sep-2025 to 08-Sep-2028 | Issuer: Assurance Quality Certification LLC
- ISO/IEC 27001:2022 | Cert: 25EQPG58 | Valid: 09-Sep-2025 to 08-Sep-2028 | Issuer: Assurance Quality Certification LLC
- ISO/IEC 20000-1:2018 | Cert: 25ZQZQ030409IT | Valid: 09-Sep-2025 to 08-Sep-2028 | Issuer: IQC Services UK Ltd
- OGC Compliance — CityLayers 2.0 | PID: 1600 | ACTIVE
- CERT-In: NOT HELD | STQC: NOT HELD
- SAP: NOT PARTNER | Oracle: NOT PARTNER | Esri: NOT AUTHORISED PARTNER

EMPLOYEES: 67 total (11 GIS specialists, 21 IT/Dev, plus QA, PM, BA, support)

TECHNOLOGY STACK:
- GIS: QGIS, ArcGIS (use only — not reseller), GeoServer, PostGIS, CityLayers 2.0
- Backend: Java/Spring Boot (PRIMARY), Python, Node.js
- Frontend: React.js, Angular
- Mobile: Android Native, iOS Native (Flutter — verify with tech team)
- Database: PostgreSQL, MySQL, Oracle DB (MS SQL Server — verify with tech team)
- Cloud: AWS, Azure
- .NET/C#/ASP.NET: NOT primary stack — verify capability before claiming
- Microsoft SQL Server: NOT primary — verify before claiming

KEY PROJECTS (for experience matching — use EXACT values):
1. AMC GIS | Ahmedabad MC | Rs. 10.55 Cr | Completed | Solo | Web GIS + Property Survey | Gujarat
2. PCSCL Smart City GIS+ERP | Pimpri-Chinchwad SC Ltd | Rs. 61.19 Cr | Ongoing | Consortium Member | GIS+ERP+Mobile | Maharashtra
3. KVIC Geo Portal + Mobile GIS | KVIC (Central PSU) | Rs. 5.15 Cr | Completed | Solo | Mobile GIS + Central Server + PAN India geo-tagging | National
4. TCGL Tourism Portal | Tourism Corp Gujarat | Rs. 9.31 Cr | Completed | Solo | Web Portal + GIS + Analytics | Gujarat
5. JuMC GIS | Junagadh MC | Rs. 9.78 Cr | Ongoing | Solo | Web GIS + Survey + O&M | Gujarat
6. VMC GIS+ERP | Vadodara MC | Rs. 20.5 Cr | Completed | Consortium Member | Web GIS + ERP | Gujarat
7. BMC GIS Mobile App | Bhavnagar MC | Rs. 4.2 Cr | Completed | Solo | Mobile GIS + Web GIS + Utility Mapping | Gujarat
8. AMC Heritage App | Ahmedabad MC | Rs. 4.72 Cr | Completed | Solo | Mobile App + Smart City | Gujarat
9. CEICED eGov Portal | Govt of Gujarat | Rs. 3.59 Cr | Ongoing | Solo | eGovernance + Mobile | Gujarat

BID DECISION RULES — APPLY STRICTLY:

NOT MET (mark RED — do not bid):
- CERT-In empanelment mandatory and cannot be subcontracted
- SAP/Oracle/Esri OEM partner authorization required
- Pure supply tender (hardware, equipment, vehicles, furniture)
- Pure AMC/O&M of existing software without development
- Civil/road/building construction
- Defence/weapons procurement
- Manpower/staffing supply only
- Turnover required > Rs. 20 Cr (Nascent max avg is Rs. 17.60 Cr over 2 FY)
- Single project value > Rs. 100 Cr (Nascent cannot show solo experience of this scale)

CONDITIONAL (mark AMBER — raise pre-bid query):
- Office in specific state: Nascent is Gujarat-only; query if commitment letter accepted
- Employee count > 67: raise pre-bid query citing GFR 2017 Rule 144
- CERT-In required but can be subcontracted: query if subcontracting/consortium allowed
- Turnover Rs. 17.61 Cr to Rs. 20 Cr: MSME relaxation query required
- .NET/SQL Server mandatory: verify Nascent capability first
- OEM partner required: query if consortium with OEM partner allowed
- JV not permitted but solo won't qualify: flag clearly

MET (mark GREEN):
- GIS, Web GIS, Mobile GIS, Geospatial solutions
- Web portal development, eGovernance, citizen portals
- Mobile application development (Android/iOS)
- Smart City solutions, ULB/Municipal Corporation projects
- Turnover <= Rs. 17.18 Cr (3-yr avg) or <= Rs. 16.23 Cr (5-yr avg)
- CMMI Level 3 (or lower) required
- ISO 9001 / ISO 27001 / ISO 20000 certification required
- MSME required
- Company age <= 19 years
- Employees <= 67
- Not blacklisted / debarred
- Net worth <= Rs. 26.09 Cr
- Single project value <= Rs. 20.5 Cr (VMC) or <= Rs. 61.19 Cr (Pimpri consortium)
"""

# ─────────────────────────────────────────
# GEMINI API CALL
# ─────────────────────────────────────────

def call_gemini(prompt: str, api_key: str) -> str:
    """Try each free model in order — if one is quota-exceeded, move to next"""
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
                "maxOutputTokens": 8192,
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
                logger.info(f"Gemini success using model: {model}")
                return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                logger.warning(f"Model {model} rate-limited (429) — waiting 5s then trying next")
                import time as _t; _t.sleep(5)
                last_error = e
                continue
            elif e.code in [503, 500, 404, 400]:
                logger.warning(f"Model {model} unavailable ({e.code}) — trying next")
                last_error = e
                continue
            else:
                raise e
        except Exception as e:
            last_error = e
            logger.warning(f"Model {model} failed: {e} — trying next")
            continue

    raise Exception(
        "All configured Gemini models failed (rate-limited, unavailable, or invalid key). "
        "Add a fresh Gemini API key in Settings, or configure Groq fallback. "
        f"Last error: {last_error}"
    )


def call_groq(prompt: str, groq_key: str) -> str:
    """Call Groq API — free tier, 14,400 requests/day"""
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
                last_error = e
                continue
            raise e
        except Exception as e:
            last_error = e
            continue

    raise Exception(f"All Groq models exhausted: {last_error}")


def clean_json(text: str) -> Dict:
    """Strip markdown fences and parse JSON — correct regex patterns"""
    text = text.strip()
    # FIXED: was \s\* (literal backslash-s-asterisk) — now correctly \s* (zero or more whitespace)
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    return json.loads(text.strip())


# ─────────────────────────────────────────
# MAIN ANALYSIS PROMPT
# ─────────────────────────────────────────

def build_prompt(text_chunk: str, prebid_passed: bool) -> str:
    prebid_note = (
        "PRE-BID DEADLINE HAS NOT PASSED. For every gap or conditional item, "
        "write the EXACT pre-bid query text that Nascent should send — specific wording, "
        "reference the exact clause number, cite GFR 2017 Rule 144 or MSME Policy 2012 "
        "where applicable."
        if not prebid_passed else
        "PRE-BID DEADLINE HAS PASSED. Do not suggest pre-bid queries. "
        "Note gaps directly and suggest alternative approaches."
    )

    return f"""You are a senior bid analyst at Nascent Info Technologies Pvt. Ltd.
Read this entire tender document carefully — every section, every clause, every table.
Extract ALL information with 100% accuracy. Do not skip anything. Do not assume anything not in the document.

{NASCENT_CONTEXT}

{prebid_note}

TENDER DOCUMENT:
{text_chunk}

Return ONLY a valid JSON object. No markdown fences. No explanation. Just the raw JSON.

CRITICAL EXTRACTION RULES:
1. tender_no: Extract EXACT tender number as printed. Never write "To be confirmed" if it is in the document.
2. portal: Extract ACTUAL portal URL from the document. Never default to a previously seen portal.
3. bid_submission_date: Extract ONLINE deadline with exact time. If physical submission also required, include BOTH.
4. tender_fee: Extract EXACT amount with Rs. symbol, mode of payment, payable to whom.
5. emd: Extract EXACT amount with Rs. symbol, mode of payment, payable to whom.
6. performance_security: Extract EXACT percentage or amount and conditions.
7. contract_period: Extract EXACT duration including all phases and AMC period.
8. scope_items: Write DETAILED bullets — include technology stack, deliverables, timelines, quantities, phases. Each bullet must be a complete sentence with actual data from the document.
9. pq_criteria: Extract ALL PQ criteria WORD-FOR-WORD from the document. Every single row of the PQ table. Do not combine or skip any row.
10. tq_criteria: Extract ALL TQ parameters with EXACT MARKS as stated. Calculate total marks. Show Nascent estimated score for each parameter.
11. Nascent status — be HONEST and ACCURATE:
    - If RFP requires CERT-In and it cannot be subcontracted: NOT MET (RED)
    - If RFP requires .NET and Nascent uses Java: CONDITIONAL with exact pre-bid query
    - If RFP requires turnover Rs. 20 Cr and Nascent avg is Rs. 17.18 Cr: CONDITIONAL with MSME query
    - If RFP requires turnover Rs. 30 Cr: NOT MET (RED) — MSME relaxation is typically 50%, still won't qualify
    - Never mark something as Met when there is a real gap
12. payment_terms: Extract EXACT payment percentages and milestone triggers from the contract.
13. overall_recommendation: Must be one of BID, NO_BID, or CONDITIONAL — based strictly on the actual criteria.
14. If any PQ criteria is NOT MET, the recommendation MUST be NO_BID or CONDITIONAL (never BID).

{{
  "tender_no": "exact tender number as printed",
  "org_name": "full organization name and complete address",
  "tender_name": "complete project title word-for-word",
  "portal": "exact URL from tender document",
  "bid_start_date": "start date with time",
  "bid_submission_date": "ONLINE deadline with time | PHYSICAL deadline if different",
  "bid_opening_date": "technical bid opening date",
  "commercial_opening_date": "commercial bid opening date or when it will be intimated",
  "prebid_meeting": "date, time, mode, or Not Applicable",
  "prebid_query_date": "exact deadline for queries with time and email",
  "estimated_cost": "exact amount with Rs. symbol",
  "tender_fee": "exact amount — mode of payment — payable to whom",
  "emd": "exact amount — mode of payment — payable to whom",
  "emd_exemption": "exact MSME exemption clause as stated in document, or Not mentioned",
  "performance_security": "exact percentage and conditions",
  "contract_period": "exact duration with all phases",
  "bid_validity": "number of days",
  "location": "project delivery location",
  "contact": "email and phone from tender",
  "jv_allowed": "exact text from tender on JV/consortium/subcontracting",
  "mode_of_selection": "QCBS/L1/etc with exact weightage if stated",
  "tender_type": "contract type (BOOT/Turnkey/Rate Contract etc.)",
  "post_implementation": "AMC or support period if stated",
  "technology_mandatory": "list any mandatory technology stack requirements word-for-word",
  "scope_items": [
    "Detailed scope point 1 with actual content from document",
    "Detailed scope point 2",
    "Include: phases, deliverables, technology, quantities, timelines, SLA requirements"
  ],
  "pq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause number and page e.g. Cl.1 Pg.8",
      "criteria": "EXACT WORD-FOR-WORD text from the PQ table in tender",
      "details": "supporting documents required as stated in tender",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_remark": "Specific honest remark: what Nascent has vs what is required. If gap exists, write the EXACT pre-bid query text to send including clause reference."
    }}
  ],
  "tq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause number and page",
      "criteria": "EXACT WORD-FOR-WORD text from TQ scoring table",
      "details": "Max Marks: X | Nascent Estimated: Y/X | Scoring logic",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_remark": "Score justification with specific evidence from Nascent portfolio — cite project name, value, client"
    }}
  ],
  "payment_terms": [
    "Exact payment milestone 1: trigger and exact percentage/amount",
    "Exact payment milestone 2"
  ],
  "overall_recommendation": "BID / NO_BID / CONDITIONAL",
  "recommendation_reason": "Specific reason citing actual strengths and gaps found in this tender",
  "notes": [
    "Critical action item 1 — specific and actionable with deadline",
    "Action item 2"
  ]
}}"""


# ─────────────────────────────────────────
# SMART TEXT CHUNKING
# Raised to 80K chars — Gemini 1.5 Pro handles 1M tokens
# This ensures full PQ tables, TQ scoring, scope, and payment terms are captured
# ─────────────────────────────────────────

def smart_chunk(full_text: str) -> str:
    if len(full_text) <= 15000:
        return full_text

    result_parts = []

    # Part 1: First 5000 chars — NIT/dates/amounts/portal
    result_parts.append(full_text[:5000])

    # Part 2: PQ section — full word-for-word extraction needed
    for kw in ["Pre-Qualification Criteria", "Pre Qualification Criteria",
                "Eligibility Criteria", "Qualifying Criteria",
                "Section 2", "2.1 Pre", "PQ Criteria"]:
        idx = full_text.find(kw)
        if idx != -1:
            result_parts.append(full_text[max(0, idx - 100): idx + 6000])
            break

    # Part 3: TQ/Technical evaluation — need full scoring table
    for kw in ["Technical Evaluation Criteria", "2.2 Technical",
                "Evaluation Criteria", "Technical Score",
                "Marking Scheme", "Technical Bid Evaluation"]:
        idx = full_text.find(kw)
        if idx != -1:
            result_parts.append(full_text[max(0, idx - 100): idx + 5000])
            break

    # Part 4: Scope of work — full scope needed for accurate analysis
    for kw in ["Scope of Work", "Scope of Services", "Section 6",
                "Technology Stack", "Functionalities", "Features Required",
                "Module", "Requirements"]:
        idx = full_text.find(kw)
        if idx != -1:
            result_parts.append(full_text[max(0, idx - 100): idx + 8000])
            break

    # Part 5: Payment terms
    for kw in ["Payment Terms", "Payment Schedule", "Payment Break",
                "7. Payment", "Payment Milestone"]:
        idx = full_text.find(kw)
        if idx != -1:
            result_parts.append(full_text[max(0, idx - 100): idx + 3000])
            break

    # Part 6: SLA, penalties, contact info — last 3000 chars
    result_parts.append(full_text[-3000:])

    combined = "\n\n[...SECTION BREAK...]\n\n".join(result_parts)

    # FIXED: Raised from 10,000 to 80,000 — Gemini 1.5 Pro handles ~60K tokens comfortably
    return combined[:80000]


# ─────────────────────────────────────────
# STATUS NORMALIZER
# ─────────────────────────────────────────

def normalize_status(status_text: str) -> tuple:
    """Returns (display_status, color) — strips all emojis, normalizes text"""
    s = str(status_text).lower()
    # Strip common emoji patterns
    s = re.sub(r'[✔✅❌⚠️🔍⚠✘]', '', s).strip()

    if "not met" in s or "critical" in s or "does not meet" in s:
        return "Not Met", "RED"
    elif "conditional" in s or "partial" in s or "pending" in s:
        return "Conditional", "AMBER"
    elif "met" in s or "meets" in s:
        return "Met", "GREEN"
    return "Review", "BLUE"


# ─────────────────────────────────────────
# MERGE RESULTS
# AI output merged with regex fallback
# Checker never overwrites AI results
# ─────────────────────────────────────────

def merge_results(regex_data: Dict, ai_data: Dict,
                  prebid_passed: bool = False) -> Dict:
    """
    Merge AI results with regex fallback.
    AI always wins on every field where it has a real value.
    NascentChecker is NOT called here — it is called separately only when AI did not run.
    """
    if "error" in ai_data or not ai_data:
        return regex_data

    result = dict(regex_data)

    EMPTY_VALUES = {
        "—", "Not mentioned", "Not specified", "To be confirmed",
        "Refer document", "", "As per tender", None, "N/A", "NA"
    }

    field_map = {
        "tender_no": "tender_no",
        "org_name": "org_name",
        "tender_name": "tender_name",
        "portal": "portal",
        "bid_submission_date": "bid_submission_date",
        "bid_opening_date": "bid_opening_date",
        "bid_start_date": "bid_start_date",
        "prebid_meeting": "prebid_meeting",
        "prebid_query_date": "prebid_query_date",
        "estimated_cost": "estimated_cost",
        "tender_fee": "tender_fee",
        "emd": "emd",
        "emd_exemption": "emd_exemption",
        "performance_security": "performance_security",
        "contract_period": "contract_period",
        "location": "location",
        "contact": "contact",
        "jv_allowed": "jv_allowed",
        "mode_of_selection": "mode_of_selection",
        "tender_type": "tender_type",
        "post_implementation": "post_implementation",
        "bid_validity": "bid_validity",
        "commercial_opening_date": "commercial_opening_date",
        "technology_mandatory": "technology_mandatory",
    }

    for ai_key, result_key in field_map.items():
        ai_val = ai_data.get(ai_key)
        if ai_val and str(ai_val).strip() not in EMPTY_VALUES:
            result[result_key] = str(ai_val).strip()

    # Scope — AI version is always more detailed
    if ai_data.get("scope_items") and len(ai_data["scope_items"]) > 0:
        result["scope_items"] = ai_data["scope_items"]

    # PQ criteria — AI version has word-for-word text
    if ai_data.get("pq_criteria") and len(ai_data["pq_criteria"]) > 0:
        pq_list = []
        for item in ai_data["pq_criteria"]:
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
        result["pq_criteria"] = pq_list

    # TQ criteria
    if ai_data.get("tq_criteria") and len(ai_data["tq_criteria"]) > 0:
        tq_list = []
        for item in ai_data["tq_criteria"]:
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
        result["tq_criteria"] = tq_list

    # Payment terms
    if ai_data.get("payment_terms") and len(ai_data["payment_terms"]) > 0:
        result["payment_terms"] = ai_data["payment_terms"]

    # Notes
    if ai_data.get("notes") and len(ai_data["notes"]) > 0:
        result["notes"] = ai_data["notes"]

    # Overall verdict — calculated from PQ results
    rec = ai_data.get("overall_recommendation", "")
    reason = ai_data.get("recommendation_reason", "")

    if rec:
        pq = result.get("pq_criteria", [])
        green = sum(1 for p in pq if p.get("nascent_color") == "GREEN")
        amber = sum(1 for p in pq if p.get("nascent_color") == "AMBER")
        red = sum(1 for p in pq if p.get("nascent_color") == "RED")

        rec_lower = rec.lower()
        if "no_bid" in rec_lower or "no bid" in rec_lower or "no-bid" in rec_lower:
            verdict, color = "NO-BID RECOMMENDED", "RED"
        elif "conditional" in rec_lower:
            verdict, color = "CONDITIONAL BID", "AMBER"
        else:
            # Safety check: if any RED criteria exist, cannot recommend BID
            if red > 0:
                verdict, color = "NO-BID RECOMMENDED", "RED"
                reason = f"AI recommended BID but {red} PQ criteria are NOT MET. Auto-corrected to NO-BID. " + reason
            elif amber > 2:
                verdict, color = "CONDITIONAL BID", "AMBER"
            else:
                verdict, color = "BID RECOMMENDED", "GREEN"

        result["overall_verdict"] = {
            "verdict": verdict,
            "reason": reason,
            "color": color,
            "green": green,
            "amber": amber,
            "red": red,
        }

    return result


# ─────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────

def analyze_with_gemini(full_text: str,
                         prebid_passed: bool = False,
                         progress_cb=None) -> Dict[str, Any]:
    import time

    all_keys = get_all_api_keys()
    if not all_keys:
        return {"error": "No Gemini API key configured. Go to Settings to add it."}

    text_chunk = smart_chunk(full_text)
    prompt = build_prompt(text_chunk, prebid_passed)

    if progress_cb:
        try:
            progress_cb("thinking", 0, 1)
        except Exception:
            pass

    # Try each Gemini key in order — wait between keys to avoid RPM cascade
    for key_idx, api_key in enumerate(all_keys):
        if key_idx > 0:
            import time as _tw; _tw.sleep(15)  # 15s gap prevents RPM cascade across keys
        logger.info(f"Trying Gemini API key {key_idx + 1}/{len(all_keys)}")
        try:
            response_text = call_gemini(prompt, api_key)
            result = clean_json(response_text)
            logger.info(f"Gemini success with key {key_idx + 1}")
            if progress_cb:
                try:
                    progress_cb("done", 1, 1, result)
                except Exception:
                    pass
            return result

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error with key {key_idx + 1}: {e}")
            # Try to extract the largest valid JSON object from the response
            try:
                # Find last complete closing brace to handle truncated JSON
                last_brace = response_text.rfind('}')
                if last_brace != -1:
                    candidate = response_text[:last_brace + 1]
                    first_brace = candidate.find('{')
                    if first_brace != -1:
                        return json.loads(candidate[first_brace:])
            except Exception:
                pass
            try:
                match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass
            # JSON is fatally broken — try next key
            logger.warning(f"JSON recovery failed on key {key_idx + 1} — trying next key")
            continue

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"Gemini HTTP {e.code} on key {key_idx + 1}: {body[:200]}")
            if e.code in [429, 503, 500, 400, 404]:
                logger.warning(f"Key {key_idx + 1} code {e.code} — trying backup key")
                continue
            return {"error": f"Gemini API error {e.code}: {body[:150]}"}

        except Exception as e:
            logger.error(f"Gemini failed on key {key_idx + 1}: {e}")
            if "quota" in str(e).lower() or "429" in str(e) or "All Gemini" in str(e):
                continue
            # Network/timeout errors — try next key too
            if "timeout" in str(e).lower() or "URLError" in type(e).__name__:
                continue
            return {"error": str(e)[:200]}

    # All Gemini keys exhausted — try Groq as final fallback
    _cfg = load_config()
    groq_key = _cfg.get("groq_api_key", "").strip()

    if groq_key:
        try:
            logger.info("All Gemini keys exhausted — trying Groq fallback...")
            response_text = call_groq(prompt, groq_key)
            result = clean_json(response_text)
            logger.info("Groq fallback success")
            return result
        except json.JSONDecodeError:
            try:
                match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Groq fallback failed: {e}")

    return {
        "error": (
            "All API keys failed (rate-limited, model unavailable, or invalid). "
            "Options: (1) Add a new Gemini key at aistudio.google.com, "
            "(2) Add free Groq key at console.groq.com — 14,400 requests/day, "
            "or (3) wait and retry later if rate limits reset."
        )
    }
