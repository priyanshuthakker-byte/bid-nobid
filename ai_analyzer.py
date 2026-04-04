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
# ── NASCENT PROFILE — loaded dynamically from Sheet or local JSON ──
def _load_nascent_context() -> str:
    """Load profile from Google Sheet (live) or nascent_profile.json (fallback)."""
    try:
        from sync_manager import load_combined_profile, profile_to_ai_context
        profile = load_combined_profile()
        if profile:
            return profile_to_ai_context(profile)
    except Exception as e:
        print(f"⚠️ sync_manager unavailable: {e}")
    # Hard fallback — static context
    return NASCENT_CONTEXT_STATIC

NASCENT_CONTEXT_STATIC = """NASCENT INFO TECHNOLOGIES PVT. LTD. — STATIC PROFILE (fallback):
- CIN: U72200GJ2006PTC048723 | MSME: UDYAM-GJ-01-0007420
- PAN: AACCN3670J | GSTIN: 24AACCN3670J1ZG
- Employees: 67 | Signatory: Hitesh Patel (CAO)
- FY 2022-23: ₹16.36 Cr | FY 2023-24: ₹16.36 Cr | FY 2024-25: ₹18.83 Cr | Avg: ₹17.18 Cr
- CMMI V2.0 L3 | ISO 9001 | ISO 27001 | ISO 20000
- Stack: Java/Spring Boot, Python, React, Flutter, QGIS, ArcGIS, GeoServer, PostgreSQL
- Projects: AMC GIS, PCSCL Smart City, KVIC Geo Portal, TCGL, JuMC GIS, VMC GIS+ERP, BMC Mobile GIS
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
                "maxOutputTokens": 16384,
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
    """Strip markdown fences and parse JSON. Recovers partial JSON if truncated."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find a complete JSON object
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Partial recovery: JSON was truncated — fix it
    # Find the opening brace
    start = text.find('{')
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)

    partial = text[start:]

    # Try progressively shorter versions until we get valid JSON
    # Strategy: truncate at the last complete key-value pair
    for cut_pattern in [
        # Cut at last complete array item ending with }
        r',\s*\{[^{}]*$',
        # Cut at last complete string value
        r',\s*"[^"]*":\s*"[^"]*$',
        # Cut at last complete key
        r',\s*"[^"]*":\s*\[$',
        r',\s*"[^"]*":\s*$',
        r',\s*"[^"]*$',
    ]:
        truncated = re.sub(cut_pattern, '', partial, flags=re.DOTALL)
        # Close any open arrays and the object
        open_brackets = truncated.count('[') - truncated.count(']')
        open_braces   = truncated.count('{') - truncated.count('}')
        fixed = truncated
        fixed += ']' * max(0, open_brackets)
        fixed += '}' * max(0, open_braces)
        try:
            result = json.loads(fixed)
            logger.warning(f"Recovered partial JSON ({len(result)} top-level keys)")
            return result
        except json.JSONDecodeError:
            continue

    # Last resort: extract whatever valid fields we can
    result = {}
    for field_match in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', partial):
        result[field_match.group(1)] = field_match.group(2)
    if result:
        logger.warning(f"Extracted {len(result)} fields from broken JSON via regex")
        return result

    raise json.JSONDecodeError("Could not recover JSON", text, 0)


# ─────────────────────────────────────────
# SMART TEXT CHUNKING
# ─────────────────────────────────────────
def smart_chunk(full_text: str) -> str:
    """Select most important sections for very long tenders."""
    if len(full_text) <= 10000:
        return full_text

    parts = []
    # Part 1: Beginning — NIT, dates, amounts (first 4000 chars)
    parts.append(full_text[:4000])

    def find_section(text, keywords, min_content_after=200, skip_toc=True):
        """Find section that has real content, not just a TOC entry."""
        best_idx = -1
        for kw in keywords:
            start = 0
            while True:
                idx = text.find(kw, start)
                if idx == -1:
                    break
                # Check if this is a real section (has content after it, not just dots or page numbers)
                after = text[idx:idx+300].replace(kw, '').strip()
                is_toc = bool(re.search(r'^[\s\.]{10,}', after) or
                              re.search(r'^\s*\d+\s*$', after[:30]) or
                              re.search(r'\.{5,}', after[:50]))
                if skip_toc and is_toc:
                    start = idx + 1
                    continue
                # Real content found
                if best_idx == -1 or idx < best_idx:
                    best_idx = idx
                break
        return best_idx

    # Part 2: PQ/Eligibility section — skip TOC, get actual criteria
    pq_keywords = [
        "Pre-Qualification Criteria\n", "Pre-Qualification Criteria \n",
        "Eligibility Criteria\n", "Eligibility Criteria \n",
        "Qualifying Criteria\n",
        "A Bidder must meet", "The Bidder must have",
        "bidder interested in being considered",
        "fulfill the following minimum eligibility",
        "minimum eligibility criteria",
        "Eligibility and Qualification", "5.1 Pre-Qualification",
        "Minimum Eligibility", "Technical Eligibility",
        "4. Eligibility", "4.  Eligibility",
    ]
    pq_idx = find_section(full_text, pq_keywords)
    if pq_idx != -1:
        parts.append(full_text[max(0, pq_idx-100):pq_idx+4000])

    # Part 3: TQ/Technical scoring — skip TOC
    tq_keywords = [
        "Technical Score Criteria\n", "Technical Evaluation Criteria\n",
        "Marking Scheme\n", "Technical Score system",
        "Sr. No.  Marking", "Marks  Sub-Marks",
        "5.2  Technical Score", "Technical Qualification Criteria",
    ]
    tq_idx = find_section(full_text, tq_keywords)
    if tq_idx != -1:
        parts.append(full_text[max(0, tq_idx-100):tq_idx+3000])

    # Part 4: Scope of work — skip TOC
    scope_keywords = [
        "Scope of Work\n", "Scope of Services\n",
        "SCOPE OF WORK\n", "Work to be Done",
        "1.  Scope", "2.  Scope", "Phase 1:",
        "Functionalities Required", "Key Deliverables",
    ]
    scope_idx = find_section(full_text, scope_keywords)
    if scope_idx != -1:
        parts.append(full_text[max(0, scope_idx-100):scope_idx+3000])

    # Part 5: Payment terms
    pay_keywords = ["Payment Terms\n", "Payment Schedule\n",
                    "PAYMENT TO THE AGENCY", "Payment Milestones",
                    "7. Payment", "Schedule of Payments"]
    pay_idx = find_section(full_text, pay_keywords)
    if pay_idx != -1:
        parts.append(full_text[max(0, pay_idx-100):pay_idx+2000])

    # Part 6: Schedule of dates / key dates table
    dates_keywords = ["Schedule of Bidding", "Schedule of Activities",
                      "Key Dates", "Important Dates", "1.6 Schedule"]
    dates_idx = find_section(full_text, dates_keywords)
    if dates_idx != -1:
        parts.append(full_text[max(0, dates_idx-50):dates_idx+1500])

    # Part 7: Last 1500 chars
    parts.append(full_text[-1500:])

    combined = "\n\n[...]\n\n".join(parts)
    return combined[:20000]


# ─────────────────────────────────────────
# MAIN ANALYSIS PROMPT
# ─────────────────────────────────────────
def build_prompt(text_chunk: str, prebid_passed: bool) -> str:
    prebid_note = (
        "PRE-BID DEADLINE HAS NOT PASSED. For every gap or conditional item, "
        "draft the EXACT pre-bid query text to send — cite exact clause number and page. "
        "Be specific, professional, cite guidelines where possible. Every blockage has a window — find it."
        if not prebid_passed else
        "PRE-BID DEADLINE HAS PASSED. Note gaps directly. Document what was missing and why."
    )
    return f"""You are a senior bid analyst at Nascent Info Technologies Pvt. Ltd.
Read this entire tender document word by word — every section, every clause, every table, every footnote.
Extract ALL information with 100% accuracy. Do not skip anything. Do not assume.

{_load_nascent_context()}

{prebid_note}

NASCENT BID PHILOSOPHY — apply this intelligence:
- Never auto-reject on geography: "We will establish office within X days of award"
- Never auto-reject on employee count: "Project needs X Java devs, Y GIS — here is actual team breakdown"  
- Never auto-reject on turnover: "Request MSME/CMMI exception per procurement guidelines"
- ISO old version demanded: "We hold current superseding version — request acceptance"
- OEM authorization missing: "Request exception or submit MAF from qualifying OEM"
- JV/Consortium allowed: Nascent does software/GIS, partner does rest — always check if JV path exists
- Pure supply (no development): Only true NO-BID. If any software/GIS development is in scope → BID path exists
- Portal vs RFP discrepancies: Flag every mismatch — these are pre-bid queries
- EVERY blockage has a window. Find it and draft the query.

TENDER DOCUMENT:
{text_chunk}

Return ONLY a valid JSON object. No markdown fences. No explanation. Just the JSON.

CRITICAL RULES:
1. overall_recommendation must be exactly: "BID", "NO-BID", or "CONDITIONAL"
2. PQ CRITERIA EXTRACTION — THIS IS THE MOST IMPORTANT PART:
   - Look for section titled "Eligibility Criteria" or "Pre-Qualification Criteria" — this is ALWAYS a numbered table with Sr.No. | Description | Proof Required columns
   - Extract EVERY numbered row from that table as a separate pq_criteria item
   - The Index/TOC also mentions "Eligibility Criteria" with a page number — IGNORE the TOC entry, use only the actual section content
   - The actual criteria start with "The bidder should..." or "Bidder must..." or similar
   - Common criteria in Indian tenders: company registration, turnover, GST/PAN, certifications (CMMI, ISO), experience projects, employee count, EMD, solvency, non-blacklisting
   - Each Sr. No. in the eligibility table = one pq_criteria item
3. Extract ALL TQ criteria with exact marks/weightage
4. nascent_status must be exactly: "Met", "Not Met", or "Conditional"
5. For every "Not Met" or "Conditional" — ALWAYS write a pre-bid query draft in nascent_remark
6. Check for portal vs RFP discrepancies (dates, amounts, period of work, EMD, conditions)
7. Extract JV/consortium conditions as a separate dedicated section
8. Generate action_items with specific target dates based on bid deadline
9. For payment terms — look for milestone table with Sr.No. | Activity | Timeline | Payment % columns
10. Extract EMD amount, Bid Fee, Bid Validity, Performance Security — these are always in Notice Inviting Bid or Key Events section
11. Use "—" for genuinely missing fields, never null or empty string
12. PRE-BID QUERIES: Only raise queries for REAL GAPS that affect Nascent's eligibility or ability to bid. Do NOT raise queries for: dates that are clearly stated, procedural questions about submission process, general information requests, or items that are already clear in the document. Maximum 5-6 focused queries. Each query must have a specific purpose — either removing a blockage or seeking genuine clarification on ambiguous criteria.

{{
  "tender_no": "exact tender reference number from document",
  "tender_id": "portal tender ID if different from tender_no",
  "org_name": "full official organization name",
  "dept_name": "department or sub-department if mentioned",
  "tender_name": "complete project title word for word",
  "portal": "exact portal URL from document",
  "bid_start_date": "date and time",
  "bid_submission_date": "deadline date and time — CRITICAL",
  "bid_opening_date": "technical bid opening date and time",
  "commercial_opening_date": "commercial/financial opening date or when intimated",
  "prebid_meeting": "date, time, mode (physical/online), venue",
  "prebid_query_date": "deadline for submitting pre-bid queries",
  "estimated_cost": "amount with Rs. prefix — if not stated say Not mentioned in document",
  "tender_fee": "amount and payment mode and payable to",
  "emd": "full EMD amount and payment modes accepted",
  "emd_exemption": "MSME/startup exemption text exactly as in document",
  "performance_security": "percentage and form and validity",
  "contract_period": "total duration broken into phases if mentioned",
  "bid_validity": "number of days",
  "location": "project location/site",
  "contact": "name, designation, email, phone of contact officer",
  "jv_allowed": "full JV/consortium permission text from document",
  "mode_of_selection": "L1/QCBS/quality-cum-cost with exact weightage if given",
  "tender_type": "lump sum/rate contract/turnkey/EPC etc",
  "post_implementation": "AMC period and support obligations",
  "technology_mandatory": "any mandatory technology stack mentioned",
  "no_of_covers": "number of bid envelopes/covers",
  "portal_vs_rfp_discrepancies": [
    {{
      "field": "what field has discrepancy",
      "portal_says": "what portal shows",
      "rfp_says": "what RFP document states",
      "action": "pre-bid query text to resolve this discrepancy"
    }}
  ],
  "jv_conditions": [
    "Each JV condition word-for-word from document — max members, liability, lead partner rules, individual vs pooled criteria"
  ],
  "scope_items": [
    "Scope point 1 — with actual deliverable names, quantities, technologies from document",
    "Include all phases, modules, features, integrations mentioned"
  ],
  "pq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Section 4 / Eligibility Criteria / Sr. No. 1",
      "criteria": "EXACT WORD-FOR-WORD criteria text from PQ table — do not shorten",
      "details": "Documents required as stated in document",
      "nascent_status": "Met",
      "nascent_remark": "What Nascent has. If gap: what is missing AND draft pre-bid query with exact clause ref and professional reasoning"
    }}
  ],
  "tq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause reference",
      "criteria": "EXACT text from TQ table",
      "details": "Max Marks: X | Nascent Estimated Score: Y",
      "nascent_status": "Met",
      "nascent_remark": "Score justification citing specific Nascent projects and certifications"
    }}
  ],
  "payment_terms": [
    "Milestone 1: exact trigger condition and payment percentage or amount"
  ],
  "penalty_clauses": [
    {{
      "type": "LD/Penalty/Blacklisting etc",
      "condition": "what triggers it",
      "penalty": "amount or percentage",
      "max_cap": "maximum cap if stated",
      "clause_ref": "clause reference"
    }}
  ],
  "prebid_queries": [
    {{
      "clause": "exact clause ref and page number",
      "rfp_text": "the exact clause text that needs clarification",
      "query": "professional query text — specific, well-reasoned, cites guidelines where applicable"
    }}
  ],
  "overall_recommendation": "BID",
  "recommendation_reason": "3-5 specific reasons — cite actual gaps, strengths, and JV path if applicable",
  "key_reasons": [
    "Reason 1 with specific detail",
    "Reason 2 with specific detail"
  ],
  "action_items": [
    {{
      "action": "specific action to take",
      "responsible": "who does it — Bid Team / CA / Signatory / External",
      "target_date": "target date based on bid deadline",
      "priority": "URGENT / HIGH / MEDIUM"
    }}
  ],
  "notes": [
    "Important observation 1 — portal discrepancies, corrigendum status, local supplier, integrity pact requirements etc"
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

    # NEW: JV conditions
    if ai_data.get("jv_conditions"):
        result["jv_conditions"] = ai_data["jv_conditions"]

    # NEW: Portal vs RFP discrepancies
    if ai_data.get("portal_vs_rfp_discrepancies"):
        result["portal_vs_rfp_discrepancies"] = ai_data["portal_vs_rfp_discrepancies"]

    # NEW: Penalty clauses
    if ai_data.get("penalty_clauses"):
        result["penalty_clauses"] = ai_data["penalty_clauses"]

    # NEW: Action items with dates
    if ai_data.get("action_items"):
        result["action_items"] = ai_data["action_items"]

    # NEW: Key reasons
    if ai_data.get("key_reasons"):
        result["key_reasons"] = ai_data["key_reasons"]

    # Extra snapshot fields
    for field in ["tender_id", "dept_name", "no_of_covers"]:
        val = ai_data.get(field)
        if val and str(val).strip() not in EMPTY:
            result[field] = str(val).strip()

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
            # Try next key — different key may get a cleaner response
            continue

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
