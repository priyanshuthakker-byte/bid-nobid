"""
AI Analyzer v5.3 — Fixed model names + corrigendum diff support
March 2026 correct models:
- gemini-2.5-flash (primary, free tier, 500/day)
- gemini-2.5-flash-lite (fallback)
- gemini-2.0-flash (fallback, retiring June 2026)
- gemini-2.0-flash-lite (last resort)
REMOVED: gemini-2.5-flash-preview-05-20 (retired), gemini-2.0-flash (retired),
         gemini-2.5-flash-preview-04-17 (wrong name), gemini-2.0-flash-thinking-exp (wrong name)
"""

import json, re, urllib.request, urllib.error, logging, os
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).parent / "config.json"
PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"

# ── CORRECT MODEL NAMES (March 2026) ─────────────────────────
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "mixtral-8x7b-32768",
]


def load_config() -> Dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Environment variables ALWAYS override config.json (Render env vars survive restarts)
    if os.environ.get("GEMINI_API_KEY"):
        cfg["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
    if os.environ.get("GROQ_API_KEY"):
        cfg["groq_api_key"] = os.environ["GROQ_API_KEY"]
    # T247 credentials from env vars (if set in Render)
    if os.environ.get("T247_USERNAME"):
        cfg["t247_username"] = os.environ["T247_USERNAME"]
    if os.environ.get("T247_PASSWORD"):
        cfg["t247_password"] = os.environ["T247_PASSWORD"]
    # Collect all extra Gemini keys
    extra = []
    for i in range(2, 8):
        k = os.environ.get(f"GEMINI_API_KEY_{i}")
        if k:
            extra.append(k)
    if extra:
        existing = cfg.get("gemini_api_keys", [])
        cfg["gemini_api_keys"] = list(dict.fromkeys(existing + extra))  # dedup preserving order
    return cfg


def save_config(config: Dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_api_key() -> str:
    return load_config().get("gemini_api_key", "")


def get_all_api_keys() -> list:
    config = load_config()
    keys = list(config.get("gemini_api_keys", []))
    primary = config.get("gemini_api_key", "")
    if primary and primary not in keys:
        keys.insert(0, primary)
    return [k for k in keys if k and len(k.strip()) > 20]


def get_groq_key() -> str:
    return load_config().get("groq_api_key", "")


def build_nascent_context() -> str:
    try:
        if PROFILE_PATH.exists():
            p = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            company = p.get("company", {})
            finance = p.get("finance", {})
            certs = p.get("certifications", {})
            employees = p.get("employees", {})
            projects = p.get("projects", [])
            emp_total = employees.get("total_confirmed", 67)
            turnover_avg = finance.get("avg_turnover_last_3_fy", 17.18)
            proj_lines = "\n".join([
                f"{i}. {pr.get('name','')} | {pr.get('client','')} | Rs.{pr.get('value_cr','')} Cr | {pr.get('status','')}"
                for i, pr in enumerate(projects[:9], 1)
            ])
            return f"""NASCENT INFO TECHNOLOGIES PVT. LTD.:
- Pvt Ltd | CIN: {company.get('cin','U72200GJ2006PTC048723')} | Incorporated: {company.get('incorporated','23 June 2006')} | {company.get('years_in_operation',19)} yrs | Ahmedabad, Gujarat
- MSME: {company.get('msme_udyam','UDYAM-GJ-01-0007420')} | PAN: {company.get('pan','AACCN3670J')} | GSTIN: 24AACCN3670J1ZG | Not blacklisted
- Turnover: FY22-23 Rs.{finance.get('fy2223_cr',16.36)} Cr | FY23-24 Rs.{finance.get('fy2324_cr',16.36)} Cr | FY24-25 Rs.{finance.get('fy2425_cr',18.83)} Cr | Avg Rs.{turnover_avg} Cr | Net Worth Rs.{finance.get('net_worth_cr',26.09)} Cr
- CMMI {certs.get('cmmi_level','V2.0 Level 3')} till {certs.get('cmmi_valid','19-Dec-2026')} | ISO 9001/27001/20000 till Sep-2028 | CERT-In: NO | STQC: NO
- Employees: {emp_total} total ({employees.get('gis_staff',11)} GIS, {employees.get('it_dev_staff',21)} IT/Dev, rest QA/PM/BA)
- EMPLOYEE RULE: if tender needs >{emp_total} → CONDITIONAL (NEVER mark Met)
- PROJECTS:\n{proj_lines}
- CONDITIONAL: >{emp_total} employees | turnover >{turnover_avg} Cr | CERT-In | SAP/Oracle mandatory | specific state office
- MET: GIS | Web/Mobile portal | Municipal/ULB | CMMI L3 | ISO certs | Blacklisting | Years in operation"""
    except Exception as e:
        logger.warning(f"Profile load failed: {e}")
    return "NASCENT: Pvt Ltd, 19 yrs, CMMI L3, ISO 9001/27001/20000, 67 employees, avg turnover Rs.17.18 Cr, MSME, Ahmedabad. RULE: >67 employees = CONDITIONAL."


def call_gemini(prompt: str, api_key: str) -> str:
    last_error = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192}
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                logger.info(f"Gemini success: {model}")
                return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:100]
            logger.warning(f"Model {model} ({e.code}) — trying next. Body: {body}")
            last_error = f"{e.code}: {body}"
            continue
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Model {model} error: {e} — trying next")
            continue
    raise Exception(f"All Gemini models exhausted. Last error: {last_error}")


def call_groq(prompt: str, groq_key: str) -> str:
    for model in GROQ_MODELS:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8192, "temperature": 0.1
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + groq_key}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"Groq {model}: {e}")
            continue
    raise Exception("All Groq models failed")


def clean_json(text: str) -> Dict:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    return json.loads(text.strip())


def build_prompt(text_chunk: str, prebid_passed: bool) -> str:
    import datetime
    today = datetime.date.today().strftime("%d/%m/%Y")
    nascent_context = build_nascent_context()
    prebid_note = (
        "PRE-BID DEADLINE NOT PASSED. Write exact pre-bid query text for every CONDITIONAL item."
        if not prebid_passed else
        "PRE-BID DEADLINE PASSED. Flag gaps directly."
    )
    return f"""You are a senior bid analyst at Nascent Info Technologies Pvt. Ltd.
Today: {today}
{nascent_context}
{prebid_note}

TENDER DOCUMENT:
{text_chunk}

Return ONLY valid JSON. No markdown. No preamble. Just the JSON object.

RULES:
1. Extract EXACT tender number from document
2. bid_submission_date: include time + physical deadline if different
3. estimated_cost: if not found → "Not specified in tender — verify from portal"
4. scope_items: ALL features, complete, no truncation
5. pq_criteria: EVERY row, WORD-FOR-WORD from the PQ table
6. jv_allowed: check BOTH NIT section AND Terms & Conditions section
7. Employee rule: if tender needs X > Nascent count → CONDITIONAL with exact pre-bid query text
8. Missing field → "Not specified in tender — verify from portal"
9. submission_checklist: Extract EVERY document/item from the RFP's checklist table or Annexure. If RFP has no explicit checklist, infer from scope and PQ requirements.
10. prebid_queries: Generate formal pre-bid queries for EVERY conditional or unclear item. Use exact GFR/MSME guideline if applicable.
11. verdict: Must be exactly "BID", "NO-BID", "CONDITIONAL", or "REVIEW"
12. concerns: List specific issues — wrong tech stack, turnover gap, manpower requirement, local office, etc.

{{
  "tender_no": "",
  "org_name": "",
  "tender_name": "",
  "portal": "",
  "bid_start_date": "",
  "bid_submission_date": "",
  "bid_opening_date": "",
  "commercial_opening_date": "",
  "prebid_meeting": "",
  "prebid_query_date": "",
  "estimated_cost": "",
  "tender_fee": "",
  "emd": "",
  "emd_exemption": "",
  "performance_security": "",
  "contract_period": "",
  "bid_validity": "",
  "location": "",
  "contact": "",
  "jv_allowed": "",
  "mode_of_selection": "",
  "tender_type": "",
  "post_implementation": "",
  "technology_mandatory": "",
  "scope_items": [],
  "pq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause X Page Y",
      "criteria": "EXACT WORD-FOR-WORD text",
      "details": "documents required",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_remark": "specific remark. If CONDITIONAL write exact pre-bid query."
    }}
  ],
  "tq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause X",
      "criteria": "EXACT text from TQ table",
      "details": "Max Marks: X | Nascent Estimated: Y/X",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_remark": "score justification"
    }}
  ],
  "payment_terms": [
    {{"milestone": "", "activity": "", "timeline": "", "payment_percent": "", "notes": ""}}
  ],
  "penalty_clauses": [
    {{"type": "", "condition": "", "penalty": "", "max_cap": "", "clause_ref": ""}}
  ],
  "manpower_obligations": [
    {{"role": "", "count": "", "type": "", "deployment_timeline": "", "duration": "", "min_qualification": "", "penalty_for_absence": ""}}
  ],
  "existing_infrastructure": {{
    "gis_software": "", "backend": "", "database": "", "server_specs": "", "gis_layers": ""
  }},
  "submission_checklist": [
    {{
      "sr_no": "1",
      "document": "Exact document name from RFP checklist/Annexure",
      "description": "Brief description of what is needed",
      "source": "rfp",
      "mandatory": true,
      "generated_by_app": false
    }}
  ],
  "prebid_queries": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause X",
      "issue": "What is the concern",
      "query": "Exact formal query text to send to client",
      "guideline_ref": "GFR/MSME/other rule if applicable"
    }}
  ],
  "overall_recommendation": "BID / NO_BID / CONDITIONAL",
  "recommendation_reason": "",
  "verdict": "BID",
  "reason": "One-line summary reason",
  "concerns": ["concern 1", "concern 2"],
  "notes": []
}}"""


def smart_chunk(full_text: str) -> str:
    if len(full_text) <= 12000:
        return full_text
    parts = [full_text[:4000]]
    sections = [
        (["Eligibility Criteria", "Pre-Qualification", "Clause 4", "Section 4"], 4000),
        (["Technical Evaluation", "Technical Qualification", "Marking Scheme"], 2500),
        (["Scope of Work", "Scope of Services", "Features", "Functionalities"], 5000),
        (["Payment Terms", "Payment Schedule", "Milestone Activity"], 2500),
        (["Penalty", "Liquidated Damages", "SLA"], 2000),
        (["Manpower", "Dedicated", "Resource Deployment"], 2000),
        (["Consortium", "Joint Venture", "Terms and Conditions"], 1500),
    ]
    for kws, size in sections:
        for kw in kws:
            idx = full_text.find(kw)
            if idx != -1:
                parts.append(full_text[max(0, idx-100):idx+size])
                break
    parts.append(full_text[-2000:])
    return "\n\n[...]\n\n".join(parts)[:18000]


def normalize_status(status_text: str) -> tuple:
    s = str(status_text).lower()
    if "not met" in s or "critical" in s:
        return "Not Met", "RED"
    elif "conditional" in s or "partial" in s or "pending" in s:
        return "Conditional", "AMBER"
    elif "met" in s:
        return "Met", "GREEN"
    return "Review", "BLUE"


def merge_results(regex_data: Dict, ai_data: Dict, prebid_passed: bool = False) -> Dict:
    if "error" in ai_data or not ai_data:
        return regex_data
    result = dict(regex_data)
    EMPTY = {"—", "Not mentioned", "Not specified", "To be confirmed", "", "As per tender", None}
    for key in ["tender_no", "org_name", "tender_name", "portal", "bid_submission_date",
                "bid_opening_date", "bid_start_date", "commercial_opening_date",
                "prebid_meeting", "prebid_query_date", "estimated_cost", "tender_fee",
                "emd", "emd_exemption", "performance_security", "contract_period",
                "location", "contact", "jv_allowed", "mode_of_selection", "tender_type",
                "post_implementation", "bid_validity", "technology_mandatory"]:
        v = ai_data.get(key)
        if v and str(v).strip() not in EMPTY:
            result[key] = str(v).strip()
    for key in ["scope_items", "payment_terms", "penalty_clauses", "manpower_obligations", "notes"]:
        if ai_data.get(key):
            result[key] = ai_data[key]
    if ai_data.get("existing_infrastructure"):
        result["existing_infrastructure"] = ai_data["existing_infrastructure"]
    for crit_key in ["pq_criteria", "tq_criteria"]:
        if ai_data.get(crit_key):
            lst = []
            for item in ai_data[crit_key]:
                status, color = normalize_status(item.get("nascent_status", "Review"))
                lst.append({**item, "nascent_status": status, "nascent_color": color})
            result[crit_key] = lst
    rec = ai_data.get("overall_recommendation", "")
    if rec:
        pq = result.get("pq_criteria", [])
        green = sum(1 for p in pq if p.get("nascent_color") == "GREEN")
        amber = sum(1 for p in pq if p.get("nascent_color") == "AMBER")
        red = sum(1 for p in pq if p.get("nascent_color") == "RED")
        rl = rec.lower()
        if "no_bid" in rl or ("no" in rl and "bid" in rl):
            verdict, color = "NO-BID RECOMMENDED", "RED"
        elif "conditional" in rl:
            verdict, color = "CONDITIONAL BID", "AMBER"
        else:
            verdict, color = "BID RECOMMENDED", "GREEN"
        result["overall_verdict"] = {
            "verdict": verdict,
            "reason": ai_data.get("recommendation_reason", ""),
            "color": color, "green": green, "amber": amber, "red": red
        }
    return result


def analyze_with_gemini(full_text: str, prebid_passed: bool = False) -> Dict[str, Any]:
    all_keys = get_all_api_keys()
    if not all_keys:
        return {"error": "No Gemini API key configured. Go to Settings."}
    text_chunk = smart_chunk(full_text)
    prompt = build_prompt(text_chunk, prebid_passed)
    response_text = ""
    for key_idx, api_key in enumerate(all_keys):
        logger.info(f"Trying Gemini key {key_idx+1}/{len(all_keys)}")
        try:
            response_text = call_gemini(prompt, api_key)
            result = clean_json(response_text)
            logger.info(f"Gemini success with key {key_idx+1}")
            return result
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            try:
                m = re.search(r'\{.*\}', response_text, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
            except Exception:
                pass
            return {"error": f"Gemini returned invalid JSON: {str(e)[:100]}"}
        except urllib.error.HTTPError as e:
            if e.code in [429, 503]:
                logger.warning(f"Key {key_idx+1} rate limited — trying next")
                continue
            return {"error": f"Gemini HTTP {e.code}"}
        except Exception as e:
            err = str(e)
            if "quota" in err.lower() or "429" in err or "exhausted" in err.lower():
                logger.warning(f"Key {key_idx+1} quota — trying next")
                continue
            return {"error": err[:200]}
    groq_key = get_groq_key()
    if groq_key:
        try:
            logger.info("Trying Groq fallback...")
            response_text = call_groq(prompt, groq_key)
            result = clean_json(response_text)
            logger.info("Groq fallback success")
            return result
        except json.JSONDecodeError:
            try:
                m = re.search(r'\{.*\}', response_text, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Groq failed: {e}")
    return {"error": "All API keys exhausted. Add a new key at aistudio.google.com/apikey (free)."}
