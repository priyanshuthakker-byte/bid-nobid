"""
AI Analyzer v5 - CLEAN VERSION
- No duplicate functions
- Reads GEMINI_API_KEY from environment variables (for Render)
- Multi-key fallback with Groq backup
"""

import json
import re
import os
import urllib.request
import urllib.error
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.json"

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro-latest",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
]

# ─────────────────────────────────────────
# CONFIG — reads from env vars AND config.json
# ─────────────────────────────────────────

def load_config() -> Dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Environment variables OVERRIDE config.json (for Render deployment)
    if os.environ.get("GEMINI_API_KEY"):
        cfg["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
    if os.environ.get("GROQ_API_KEY"):
        cfg["groq_api_key"] = os.environ["GROQ_API_KEY"]
    # Additional Gemini keys from env
    extra_keys = []
    for i in range(2, 6):
        k = os.environ.get(f"GEMINI_API_KEY_{i}")
        if k and k.strip():
            extra_keys.append(k.strip())
    if extra_keys:
        existing = cfg.get("gemini_api_keys", [])
        cfg["gemini_api_keys"] = list(set(existing + extra_keys))
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
    return [k for k in keys if k and k.strip() and len(k.strip()) > 20]

# ─────────────────────────────────────────
# NASCENT PROFILE
# ─────────────────────────────────────────

NASCENT_CONTEXT = """
NASCENT INFO TECHNOLOGIES PVT. LTD. — COMPLETE PROFILE:
- Private Limited Company | Incorporated: 23 June 2006 | CIN: U72200GJ2006PTC048723
- 19 years | HQ: Ahmedabad, Gujarat | MSME: UDYAM-GJ-01-0007420
- PAN: AACCN3670J | GSTIN: 24AACCN3670J1ZG
- Authorised Signatory: Hitesh Patel (CAO) | MD: Maulik Bhagat

FINANCIALS:
- FY 2022-23: Rs.16.36 Cr | FY 2023-24: Rs.16.36 Cr | FY 2024-25: Rs.18.83 Cr
- Average last 3 FY: Rs.17.18 Cr | Net Worth: Rs.26.09 Cr

CERTIFICATIONS:
- CMMI V2.0 Level 3 (valid till 19-Dec-2026)
- ISO 9001:2015 (valid till 08-Sep-2028)
- ISO/IEC 27001:2022 (valid till 08-Sep-2028)
- ISO/IEC 20000-1:2018 (valid till 08-Sep-2028)
- CERT-In: NOT HELD | STQC: NOT HELD | SAP: NOT PARTNER

EMPLOYEES: 67 total

KEY PROJECTS:
1. AMC GIS | Ahmedabad MC | Rs.10.55 Cr | Completed
2. PCSCL Smart City GIS+ERP | Pimpri-Chinchwad | Rs.61.19 Cr | Ongoing
3. KVIC Geo Portal | KVIC | Rs.5.15 Cr | Completed
4. TCGL Tourism Portal | Tourism Corp Gujarat | Rs.9.31 Cr | Completed
5. JuMC GIS | Junagadh MC | Rs.9.78 Cr | Ongoing
6. VMC GIS+ERP | Vadodara MC | Rs.20.5 Cr | Completed
7. BMC GIS Mobile App | Bhavnagar MC | Rs.4.2 Cr | Completed
8. AMC Heritage App | Ahmedabad MC | Rs.4.72 Cr | Completed
9. CEICED eGov Portal | Gujarat State | Rs.3.59 Cr | Ongoing
"""

# ─────────────────────────────────────────
# GEMINI API CALL
# ─────────────────────────────────────────

def call_gemini(prompt: str, api_key: str) -> str:
    last_error = None
    for model in GEMINI_MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048}
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
                logger.warning(f"Model {model} failed ({e.code}) — trying next")
                last_error = f"HTTP {e.code}"
                continue
            raise e
        except Exception as e:
            last_error = str(e)
            continue
    raise Exception(f"All Gemini models failed. Last error: {last_error}")


def call_groq(prompt: str, groq_key: str) -> str:
    for model in GROQ_MODELS:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": 0.1,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + groq_key},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code in [429, 503]:
                continue
            raise e
        except Exception:
            continue
    raise Exception("All Groq models failed")


def clean_json(text: str) -> Dict:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    return json.loads(text.strip())


def smart_chunk(full_text: str) -> str:
    if len(full_text) <= 8000:
        return full_text
    parts = [full_text[:4000]]
    for kw in ["Pre-Qualification Criteria", "Eligibility Criteria", "Qualifying Criteria"]:
        idx = full_text.find(kw)
        if idx != -1:
            parts.append(full_text[max(0, idx-100):idx+3000])
            break
    for kw in ["Technical Evaluation", "Evaluation Criteria", "Technical Score"]:
        idx = full_text.find(kw)
        if idx != -1:
            parts.append(full_text[max(0, idx-100):idx+2500])
            break
    for kw in ["Scope of Work", "Scope of Services"]:
        idx = full_text.find(kw)
        if idx != -1:
            parts.append(full_text[max(0, idx-100):idx+3000])
            break
    parts.append(full_text[-1500:])
    combined = "\n\n[...]\n\n".join(parts)
    return combined[:10000]


def build_prompt(text_chunk: str, prebid_passed: bool) -> str:
    prebid_note = (
        "PRE-BID DEADLINE HAS NOT PASSED. For every gap, write exact pre-bid query text."
        if not prebid_passed else
        "PRE-BID DEADLINE HAS PASSED. Note gaps directly."
    )
    return f"""You are a senior bid analyst at Nascent Info Technologies Pvt. Ltd.

{NASCENT_CONTEXT}

{prebid_note}

TENDER DOCUMENT:
{text_chunk}

Return ONLY a valid JSON object. No markdown. No explanation. Just JSON.

{{
  "tender_no": "exact tender number",
  "org_name": "full organization name",
  "tender_name": "complete project title",
  "portal": "exact URL from document",
  "bid_start_date": "start date with time",
  "bid_submission_date": "ONLINE deadline",
  "bid_opening_date": "opening date",
  "prebid_query_date": "query deadline",
  "estimated_cost": "exact amount with Rs.",
  "tender_fee": "exact amount",
  "emd": "exact amount",
  "emd_exemption": "MSME exemption clause",
  "performance_security": "percentage",
  "contract_period": "duration",
  "location": "project location",
  "contact": "email and phone",
  "jv_allowed": "JV/consortium text",
  "mode_of_selection": "QCBS/L1/etc",
  "scope_items": ["detailed scope point 1", "scope point 2"],
  "pq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause X",
      "criteria": "EXACT WORD-FOR-WORD text",
      "details": "supporting documents required",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_remark": "specific remark or pre-bid query text"
    }}
  ],
  "tq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Clause X",
      "criteria": "EXACT text",
      "details": "Max Marks: X | Nascent Estimated: Y",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_remark": "score justification"
    }}
  ],
  "payment_terms": ["milestone 1: trigger and amount"],
  "overall_recommendation": "BID / NO_BID / CONDITIONAL",
  "recommendation_reason": "specific reason",
  "notes": ["critical action item 1"]
}}"""


def normalize_status(status_text: str) -> tuple:
    s = str(status_text).lower()
    if "not met" in s or "critical" in s:
        return "Not Met", "RED"
    elif "conditional" in s or "partial" in s:
        return "Conditional", "AMBER"
    elif "met" in s:
        return "Met", "GREEN"
    return "Review", "BLUE"


def merge_results(regex_data: Dict, ai_data: Dict, prebid_passed: bool = False) -> Dict:
    if "error" in ai_data or not ai_data:
        return regex_data
    result = dict(regex_data)
    EMPTY = {"—", "Not mentioned", "Not specified", "To be confirmed", "Refer document", "", None}
    for key in ["tender_no","org_name","tender_name","portal","bid_submission_date",
                "bid_opening_date","bid_start_date","prebid_query_date","estimated_cost",
                "tender_fee","emd","emd_exemption","performance_security","contract_period",
                "location","contact","jv_allowed","mode_of_selection","tender_type",
                "post_implementation","bid_validity","commercial_opening_date"]:
        val = ai_data.get(key)
        if val and str(val).strip() not in EMPTY:
            result[key] = str(val).strip()
    if ai_data.get("scope_items"):
        result["scope_items"] = ai_data["scope_items"]
    if ai_data.get("pq_criteria"):
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
    if ai_data.get("tq_criteria"):
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
    if ai_data.get("payment_terms"):
        result["payment_terms"] = ai_data["payment_terms"]
    if ai_data.get("notes"):
        result["notes"] = ai_data["notes"]
    rec = ai_data.get("overall_recommendation", "")
    reason = ai_data.get("recommendation_reason", "")
    if rec:
        pq = result.get("pq_criteria", [])
        green = sum(1 for p in pq if p.get("nascent_color") == "GREEN")
        amber = sum(1 for p in pq if p.get("nascent_color") == "AMBER")
        red   = sum(1 for p in pq if p.get("nascent_color") == "RED")
        rec_lower = rec.lower()
        if "no_bid" in rec_lower or "no bid" in rec_lower:
            verdict, color = "NO-BID RECOMMENDED", "RED"
        elif "conditional" in rec_lower:
            verdict, color = "CONDITIONAL BID", "AMBER"
        else:
            verdict, color = "BID RECOMMENDED", "GREEN"
        result["overall_verdict"] = {
            "verdict": verdict, "reason": reason,
            "color": color, "green": green, "amber": amber, "red": red,
        }
    return result


# ─────────────────────────────────────────
# MAIN ENTRY POINT — single clean version
# ─────────────────────────────────────────

def analyze_with_gemini(full_text: str, prebid_passed: bool = False) -> Dict[str, Any]:
    all_keys = get_all_api_keys()
    if not all_keys:
        return {"error": "No Gemini API key configured. Go to Settings to add it."}

    text_chunk = smart_chunk(full_text)
    prompt = build_prompt(text_chunk, prebid_passed)

    # Try all Gemini keys
    for key_idx, api_key in enumerate(all_keys):
        logger.info(f"Trying Gemini key {key_idx+1}/{len(all_keys)}")
        try:
            response_text = call_gemini(prompt, api_key)
            result = clean_json(response_text)
            logger.info(f"Gemini SUCCESS with key {key_idx+1}")
            return result
        except json.JSONDecodeError as e:
            try:
                match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass
            return {"error": f"Gemini returned invalid JSON: {str(e)[:100]}"}
        except Exception as e:
            if "quota" in str(e).lower() or "429" in str(e) or "All Gemini" in str(e):
                logger.warning(f"Key {key_idx+1} quota exceeded — trying backup")
                continue
            return {"error": str(e)[:200]}

    # All Gemini keys exhausted — try Groq fallback
    cfg = load_config()
    groq_key = cfg.get("groq_api_key", "")
    if groq_key:
        try:
            logger.info("Trying Groq as fallback...")
            response_text = call_groq(prompt, groq_key)
            result = clean_json(response_text)
            logger.info("Groq fallback SUCCESS")
            return result
        except Exception as e:
            logger.error(f"Groq fallback failed: {e}")

    return {
        "error": (
            "All API keys quota exceeded. "
            "Add a new Gemini key at aistudio.google.com, "
            "or add free Groq key at console.groq.com (14,400/day free)."
        )
    }
