"""
AI Analyzer v7 — 10-Segment Pipeline
World-class tender analysis for Nascent Info Technologies.

Segments:
  1  Snapshot          — all header fields with clause+page refs
  2  Corrigendum       — date overrides from amendments
  3  Scope of Work     — detailed prose with section refs
  4  PQ Criteria       — RFP replica + Nascent assessment + evidence + math
  5  TQ Criteria       — RFP replica + marks + slab calculations
  5B Work Schedule     — T0 timeline table with LD refs
  6  Payment Schedule  — milestone table with amounts
  7  Payment Terms     — PBG, LD, SLA, IP, exit clauses
  8  Assessment        — verdict + project matches + disqualifiers
  9  Pre-bid Queries   — gaps only, generic language, guidelines cited
  10 Checklist + Notes — grouped submission checklist
"""

import json, re, os, urllib.request, urllib.error, logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).parent / "config.json"

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
]

GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

NASCENT = """NASCENT INFO TECHNOLOGIES PVT. LTD. — PROFILE FOR INTERNAL USE ONLY
Do NOT mention Nascent by name in any query text — always phrase as "the bidder" or "bidders in general".

Identity: CIN U72200GJ2006PTC048723 | MSME UDYAM-GJ-01-0007420 | PAN AACCN3670J | GSTIN 24AACCN3670J1ZG
Founded: 2006 (19 years in operation) | HQ: Ahmedabad, Gujarat
Employees: 67 total (21 IT/Dev, 11 GIS specialists, rest QA/PM/BA/Support) — ALL are IT/ITeS employees
Turnover: FY22-23 Rs.16.36Cr | FY23-24 Rs.16.36Cr | FY24-25 Rs.18.83Cr
Avg 2yr: Rs.17.60Cr | Avg 3yr: Rs.17.18Cr | Net Worth: Rs.26.09Cr
Certifications:
  CMMI V2.0 Level 3 — valid till 19-Dec-2026
  ISO 9001:2015 — valid till 08-Sep-2028
  ISO/IEC 27001:2022 — valid till 08-Sep-2028
  ISO/IEC 20000-1:2018 — valid till 08-Sep-2028
  NOT held: CERT-In empanelment, STQC, SAP/Oracle partner, NIC empanelment
Tech: Java/Spring Boot, Python/FastAPI, React.js, Angular, Flutter, Android, PostgreSQL, MySQL, MongoDB
      GIS: QGIS, ArcGIS, GeoServer, PostGIS, CityLayers 2.0, OpenLayers, MapLibre
Projects:
  P1: AMC GIS — Ahmedabad Municipal Corporation — Rs.10.55Cr — Ongoing — GIS, Web, Mobile, AMC
  P2: JuMC GIS — Junagadh Municipal Corporation — Rs.9.78Cr — Ongoing — GIS, Survey, Web
  P3: BMC GIS Mobile — Bhavnagar Municipal Corporation — Rs.4.2Cr — Completed — Android+iOS+GeoServer
  P4: VMC GIS+ERP — Vadodara Municipal Corporation — Rs.20.5Cr (consortium) — Completed — GIS+ERP+ULB
  P5: KVIC Geo Portal — KVIC Central PSU — Rs.5.15Cr — Completed — Mobile GIS, Geo-tagging, Offline, PAN India
  P6: PCSCL Smart City — Pimpri-Chinchwad — Rs.61.19Cr (consortium) — Ongoing — Smart City, GIS, ERP, IoT
  P7: TCGL Tourism — Tourism Corp Gujarat — Rs.9.31Cr — Completed — Web+Mobile, Tourism, GIS, Analytics
  P8: NSO Survey — National Statistics Office — Rs.8.4Cr — Completed — Mobile survey, PAN India, Central Govt
  P9: NP Lalganj GIS — Nagar Panchayat UP — Rs.1.2Cr — Completed — GIS, Property, ULB
  P10: AMC Heritage App — Ahmedabad Smart City — Rs.3.8Cr — Completed — AR, Mobile, Tourism
  P11: CEICED — Chief Electrical Inspector Gujarat — Rs.3.59Cr — Ongoing — eGov, Web, Mobile
Signatory: Hitesh Patel (CAO) | POA: EXPIRED 31-Mar-2026 — MUST RENEW BEFORE ANY SUBMISSION
MD: Maulik Bhagat | Email: nascent.tender@nascentinfo.com | Ph: +91-79-40200400"""


def _load_nascent_profile_text() -> str:
    """
    Prefer profile from nascent_profile.json so AI prompts stay aligned with current company data.
    Falls back to bundled NASCENT constant if file is missing/invalid.
    """
    p = Path(__file__).parent / "nascent_profile.json"
    if not p.exists():
        return NASCENT
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return NASCENT

    parts = []
    company = data.get("company", {})
    finance = data.get("finance", {})
    certs = data.get("certifications", {})
    employees = data.get("employees", {})
    projects = data.get("projects", [])

    name = company.get("name", "Company")
    parts.append(f"{name} — PROFILE FOR INTERNAL USE ONLY")
    parts.append("Do NOT mention company name in pre-bid query text.")
    parts.append("")
    parts.append(
        f"Identity: CIN {company.get('cin','—')} | MSME {company.get('udyam','—')} | "
        f"PAN {company.get('pan','—')} | GSTIN {company.get('gstin','—')}"
    )
    parts.append(
        f"Founded: {company.get('year_of_incorporation','—')} | "
        f"Years in operation: {company.get('years_in_operation','—')}"
    )
    parts.append(
        f"Employees: total {employees.get('total_confirmed','—')} | "
        f"IT/Dev {employees.get('it_dev_staff','—')} | GIS {employees.get('gis_staff','—')}"
    )
    parts.append(
        f"Turnover avg 2yr: Rs.{finance.get('avg_turnover_last_2_fy','—')} Cr | "
        f"avg 3yr: Rs.{finance.get('avg_turnover_last_3_fy','—')} Cr | "
        f"net worth: Rs.{finance.get('net_worth_cr','—')} Cr"
    )

    cmmi = certs.get("cmmi", {})
    parts.append(
        f"CMMI: Level {cmmi.get('level','—')} {cmmi.get('version','')} valid till {cmmi.get('valid_to','—')}"
    )
    for k in ["iso_9001", "iso_27001", "iso_20000"]:
        c = certs.get(k, {})
        if c:
            parts.append(f"{c.get('standard',k)} valid till {c.get('valid_to','—')}")

    if projects:
        parts.append("Projects:")
        for i, pr in enumerate(projects[:12], 1):
            parts.append(
                f"  P{i}: {pr.get('client','Project')} | Rs.{pr.get('val','—')} Cr | "
                f"{pr.get('status','—')} | {', '.join(pr.get('tags', [])[:5])}"
            )

    rendered = "\n".join(parts).strip()
    return rendered if len(rendered) > 200 else NASCENT


# ═══════════════════════════════════════════════════════
# CONFIG + API
# ═══════════════════════════════════════════════════════

def load_config() -> Dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    for key in ["GEMINI_API_KEY", "GROQ_API_KEY"]:
        v = os.environ.get(key)
        if v:
            cfg[key.lower()] = v
    for key in ["T247_USERNAME", "T247_PASSWORD"]:
        v = os.environ.get(key)
        if v:
            cfg[key.lower()] = v
    extra = []
    for i in range(2, 6):
        k = os.environ.get(f"GEMINI_API_KEY_{i}")
        if k:
            extra.append(k)
    if extra:
        existing = cfg.get("gemini_api_keys", [])
        cfg["gemini_api_keys"] = list(set(existing + extra))
    return cfg

def save_config(config: Dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")

def get_api_key() -> str:
    return load_config().get("gemini_api_key", "")

def get_all_api_keys() -> List[str]:
    config = load_config()
    keys = list(config.get("gemini_api_keys", []))
    primary = config.get("gemini_api_key", "")
    if primary and primary not in keys:
        keys.insert(0, primary)
    return [k for k in keys if k and k.strip() and len(k.strip()) > 20]

def call_gemini(prompt: str, api_key: str, max_tokens: int = 8192) -> str:
    import time as _time
    last_error = None

    def _try_model(model, key):
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}"
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens},
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["candidates"][0]["content"]["parts"][0]["text"]

    # Pass 1: try all models once
    all_429 = True
    for model in GEMINI_MODELS:
        try:
            text = _try_model(model, api_key)
            logger.info(f"Gemini OK: {model}")
            return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                logger.warning(f"{model} 429 — trying next")
                last_error = "HTTP 429"
                continue
            elif e.code in [503, 500]:
                logger.warning(f"{model} {e.code} — trying next")
                last_error = f"HTTP {e.code}"
                all_429 = False
                continue
            else:
                raise Exception(f"Gemini HTTP {e.code}: {body[:200]}")
        except Exception as e:
            logger.warning(f"{model} failed: {e}")
            last_error = str(e)
            all_429 = False
            continue

    # Legacy single-key path: fail fast (pool handles cross-key rotation)
    raise Exception(f"All Gemini models failed: {last_error}")

def call_groq(prompt: str, groq_key: str) -> str:
    last_error = None
    for model in GROQ_MODELS:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096, "temperature": 0.1,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + groq_key},
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

def _call_single_model(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=45) as resp:  # 45s — if no response by then, give up
        result = json.loads(resp.read().decode("utf-8"))
        return result["candidates"][0]["content"]["parts"][0]["text"]


def _call(prompt: str, api_key: str, all_keys: List[str],
          groq_key: str = "", max_tokens: int = 8192) -> str:
    """
    Pool-aware call. Uses KeyPool from core.api_pool when available so
    concurrent analysts share keys without stepping on each other.
    Falls back to legacy sequential retry if pool unavailable.
    """
    try:
        from core.api_pool import get_pool, refresh_pool
        refresh_pool()
        pool = get_pool()
        if pool.size() > 0:
            last_err = None
            attempts = max(pool.size(), 2)
            for _ in range(attempts):
                key = pool.acquire(timeout=30.0)  # 30s max wait for a key
                if not key:
                    break
                rate_limited = False
                try:
                    for model in GEMINI_MODELS:
                        try:
                            text = _call_single_model(prompt, key, model, max_tokens)
                            pool.release(key, success=True)
                            return text
                        except urllib.error.HTTPError as e:
                            body = e.read().decode("utf-8", errors="ignore")
                            if e.code == 429:
                                rate_limited = True
                                last_err = f"HTTP 429 {model}"
                                continue
                            if e.code in (500, 503):
                                last_err = f"HTTP {e.code} {model}"
                                continue
                            raise Exception(f"Gemini HTTP {e.code}: {body[:160]}")
                        except Exception as e:
                            last_err = f"{model}: {str(e)[:120]}"
                            continue
                    pool.release(key, success=False, rate_limited=rate_limited)
                except Exception as outer:
                    pool.release(key, success=False, rate_limited=rate_limited)
                    last_err = str(outer)
            if groq_key:
                try:
                    return call_groq(prompt, groq_key)
                except Exception as e:
                    last_err = f"groq: {e}"
            raise Exception(f"Pool exhausted: {last_err}")
    except ImportError:
        pass

    last_err = None
    for key in all_keys:
        try:
            return call_gemini(prompt, key, max_tokens)
        except Exception as e:
            last_err = str(e)
            continue
    if groq_key:
        try:
            return call_groq(prompt, groq_key)
        except Exception as e:
            last_err = str(e)
    raise Exception(f"All API keys exhausted: {last_err}")

def clean_json(text: str) -> Dict:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    start = text.find('{')
    if start == -1:
        raise json.JSONDecodeError("No JSON", text, 0)
    partial = text[start:]
    for pat in [r',\s*\{[^{}]*$', r',\s*"[^"]*":\s*"[^"]*$',
                r',\s*"[^"]*":\s*\[$', r',\s*"[^"]*":\s*$', r',\s*"[^"]*$']:
        t = re.sub(pat, '', partial, flags=re.DOTALL)
        t += ']' * max(0, t.count('[') - t.count(']'))
        t += '}' * max(0, t.count('{') - t.count('}'))
        try:
            r = json.loads(t)
            logger.warning(f"Partial JSON recovered: {len(r)} keys")
            return r
        except json.JSONDecodeError:
            continue
    result = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', partial):
        result[m.group(1)] = m.group(2)
    if result:
        return result
    raise json.JSONDecodeError("Cannot recover JSON", text, 0)


# ═══════════════════════════════════════════════════════
# TEXT HELPERS
# ═══════════════════════════════════════════════════════

def extract_section(text: str, keywords: List[str], chars: int = 8000) -> str:
    best = -1
    for kw in keywords:
        start = 0
        while True:
            idx = text.find(kw, start)
            if idx == -1:
                break
            after = text[idx:idx+300].replace(kw, '').strip()
            is_toc = bool(
                re.search(r'\.{5,}', after[:60]) or
                re.search(r'^\s*\d{1,3}\s*$', after[:20]) or
                re.search(r'^[\s\.]{15,}', after)
            )
            if not is_toc:
                if best == -1 or idx < best:
                    best = idx
                break
            start = idx + 1
    if best == -1:
        return ""
    return text[max(0, best-100):best+chars]

def get_first_n(text: str, n: int = 12000) -> str:
    return text[:n]

def get_last_n(text: str, n: int = 3000) -> str:
    return text[-n:]


# ═══════════════════════════════════════════════════════
# STEP 1 — SNAPSHOT WITH CLAUSE + PAGE REFS
# ═══════════════════════════════════════════════════════

SNAPSHOT_PROMPT = """You are extracting tender header information from an Indian government tender/RFP document.
Read ALL text carefully. Key fields like EMD, Tender Fee, JV rules, Bid Validity are often buried in body sections.

CRITICAL: For EVERY field, also extract:
- clause_ref: The clause/section number where this was found (e.g. "Cl. 5.2", "Section 3", "Schedule I")
- page_no: The page number where this was found (e.g. "Pg. 4", "Pg. 12")
If you cannot find the page/clause, use "—" for that sub-field.

Return ONLY valid JSON, no markdown:

{{
  "tender_no":               {{"value": "exact tender/NIT number", "clause_ref": "—", "page_no": "—"}},
  "org_name":                {{"value": "full organization name", "clause_ref": "—", "page_no": "—"}},
  "dept_name":               {{"value": "department name", "clause_ref": "—", "page_no": "—"}},
  "tender_name":             {{"value": "full tender title", "clause_ref": "—", "page_no": "—"}},
  "portal":                  {{"value": "portal URL for bid submission", "clause_ref": "—", "page_no": "—"}},
  "tender_type":             {{"value": "Item Rate / Lumpsum / QCBS / etc", "clause_ref": "—", "page_no": "—"}},
  "mode_of_selection":       {{"value": "L1 / QCBS / LCS / etc", "clause_ref": "—", "page_no": "—"}},
  "no_of_covers":            {{"value": "number of bid covers/envelopes", "clause_ref": "—", "page_no": "—"}},
  "bid_start_date":          {{"value": "bid availability/start date", "clause_ref": "—", "page_no": "—"}},
  "bid_submission_date":     {{"value": "LAST DATE for submission with time", "clause_ref": "—", "page_no": "—"}},
  "bid_opening_date":        {{"value": "technical bid opening date", "clause_ref": "—", "page_no": "—"}},
  "commercial_opening_date": {{"value": "financial bid opening date", "clause_ref": "—", "page_no": "—"}},
  "prebid_meeting":          {{"value": "pre-bid meeting date+time+mode+email", "clause_ref": "—", "page_no": "—"}},
  "prebid_query_date":       {{"value": "last date for pre-bid queries with time", "clause_ref": "—", "page_no": "—"}},
  "prebid_query_format":     {{"value": "Does RFP specify a FORMAT for submitting queries? Annexure/Format name or 'Not specified'", "clause_ref": "—", "page_no": "—"}},
  "estimated_cost":          {{"value": "tender/estimated value with Rs.", "clause_ref": "—", "page_no": "—"}},
  "tender_fee":              {{"value": "document fee — amount + GST + payment mode", "clause_ref": "—", "page_no": "—"}},
  "emd":                     {{"value": "EMD/bid security — full amount + payment mode", "clause_ref": "—", "page_no": "—"}},
  "emd_exemption":           {{"value": "EMD exemption for MSME/Startup — exact clause", "clause_ref": "—", "page_no": "—"}},
  "performance_security":    {{"value": "PBG — % of contract value + conditions", "clause_ref": "—", "page_no": "—"}},
  "contract_period":         {{"value": "Phase A (dev) + Phase B (O&M) separately", "clause_ref": "—", "page_no": "—"}},
  "bid_validity":            {{"value": "bid validity in days", "clause_ref": "—", "page_no": "—"}},
  "jv_allowed":              {{"value": "JV/Consortium — allowed or NOT allowed, exact clause", "clause_ref": "—", "page_no": "—"}},
  "location":                {{"value": "project location / city / state", "clause_ref": "—", "page_no": "—"}},
  "contact":                 {{"value": "contact officer name + email + phone", "clause_ref": "—", "page_no": "—"}},
  "technology_mandatory":    {{"value": "any mandatory technology/platform", "clause_ref": "—", "page_no": "—"}}
}}

Rules:
- Use "—" ONLY if truly not found after reading entire text
- For amounts: include Rs., figures AND words as written
- For JV: quote the EXACT clause text (often says "NOT allowed")
- prebid_query_format: look for any Annexure or Format number mentioned for query submission

Text:
{text}
"""

def step1_snapshot(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    chunk = get_first_n(text, 12000) + "\n\n[...]\n\n" + get_last_n(text, 3000)
    prompt = SNAPSHOT_PROMPT.format(text=chunk)
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=4096)
        result = clean_json(raw)
        # Flatten: each field is {value, clause_ref, page_no}
        # Keep as structured dict — frontend handles display
        logger.info(f"[Step1-Snapshot] {len(result)} fields with refs")
        return result
    except Exception as e:
        logger.error(f"[Step1-Snapshot] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# STEP 2 — CORRIGENDUM (unchanged, works well)
# ═══════════════════════════════════════════════════════

CORRIGENDUM_PROMPT = """Extract date extension information from this corrigendum/amendment.
Return ONLY valid JSON — only include fields that CHANGED:
{{
  "bid_submission_date": "new deadline if changed, else null",
  "bid_opening_date": "new opening date if changed, else null",
  "prebid_meeting": "new pre-bid details if changed, else null",
  "prebid_query_date": "new query deadline if changed, else null",
  "corrigendum_note": "one sentence: what changed"
}}

Text:
{text}
"""

def step2_corrigendums(corrigendum_texts: List[str], api_key: str,
                       all_keys: List[str], groq_key: str) -> Dict:
    if not corrigendum_texts:
        return {}
    combined = "\n\n---\n\n".join(corrigendum_texts)
    prompt = CORRIGENDUM_PROMPT.format(text=combined[:6000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=1024)
        result = clean_json(raw)
        return {k: v for k, v in result.items() if v and v != "null"}
    except Exception as e:
        logger.error(f"[Step2-Corrigendum] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# STEP 3 — SCOPE OF WORK (DETAILED PROSE)
# ═══════════════════════════════════════════════════════

SCOPE_PROMPT = """You are a bid analyst. Extract the Scope of Work from this Indian government tender.

Return ONLY valid JSON — no markdown, no preamble:
{{
  "scope_background": "1-2 sentences: what the client wants built and why",
  "scope_sections": [
    {{
      "section_no": "6.1",
      "section_title": "Component name",
      "page_no": "Pg. 12",
      "prose": "2-3 sentence description. Include actual system/module names from document.",
      "deliverables": ["SRS", "Source code", "UAT sign-off"],
      "tech_specified": "Tech if stated, else —",
      "phase": "Phase A / Phase B / Both / —"
    }}
  ],
  "key_integrations": [
    {{"system": "System name", "type": "API/Portal/DB", "purpose": "brief purpose", "section_ref": "Cl. X"}}
  ],
  "scale_requirements": "Users/uptime if stated, else —",
  "deployment_requirement": "Cloud/On-prem/Hybrid if stated, else —"
}}

Text:
{text}
"""

def step3_scope(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    scope_text = extract_section(text, [
        "Scope of Work\n", "SCOPE OF WORK\n", "Scope of Services\n",
        "Work to be Done", "Phase A", "Phase 1", "Scope:", "6. Scope",
        "3. Scope", "4. Scope", "Work Components", "Deliverables",
        "Functional Requirements", "Technical Requirements"
    ], chars=6000)
    if not scope_text:
        scope_text = text[4000:10000]
    prompt = SCOPE_PROMPT.format(text=scope_text[:6000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=4096)
        result = clean_json(raw)
        logger.info(f"[Step3-Scope] {len(result.get('scope_sections',[]))} sections")
        return result
    except Exception as e:
        logger.error(f"[Step3-Scope] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# STEP 4 — PQ CRITERIA WITH REFS + EVIDENCE + MATH
# ═══════════════════════════════════════════════════════

PQ_PROMPT = """You are a bid analyst extracting Pre-Qualification / Eligibility criteria from an Indian government tender.

RULE 1 — COPY WORD-FOR-WORD. NO PARAPHRASING EVER.
The "criteria" field = EXACT TEXT from the Specific Requirements column. Every word. Every sentence. Every condition. No shortening.

RULE 2 — EXTRACT EVERY ROW. Do not skip or merge any row.

RULE 3 — INCLUDE PAGE AND CLAUSE REFERENCES for each criterion.

RULE 4 — EVIDENCE: For each GREEN criterion, cite WHICH SPECIFIC Nascent project/cert/figure proves it.
For AMBER: state EXACTLY what pre-bid query is needed.
For RED: state EXACTLY what is missing.

RULE 5 — SHOW MATH for numeric criteria:
If tender says "turnover > Rs.10 Cr" → show: "Required: Rs.10 Cr | Nascent 3yr avg: Rs.17.18 Cr | MEETS ✓"
If tender says "minimum 100 employees" → show: "Required: 100 | Nascent: 67 | SHORTFALL of 33 ✗"

RULE 6 — PROJECT EVIDENCE: Match each experience/work criterion to specific Nascent project:
"Similar GIS work > Rs.5 Cr" → "P1: AMC GIS Rs.10.55 Cr (Ongoing) ✓ | P3: BMC GIS Rs.4.2 Cr ✗ (below) | P2: JuMC GIS Rs.9.78 Cr ✓"

KEY RULE — Nascent is pure IT/ITeS:
- All turnover = IT/ITeS turnover
- All 67 employees = IT/ITeS employees
- EMD exemption = MSME-eligible (always raise query for written confirmation)
- CMMI L3 = strong certification advantage

Nascent profile (INTERNAL — never mention by name in queries):
{nascent}

Text containing PQ/Eligibility criteria:
{text}

Return ONLY valid JSON:
{{
  "pq_criteria": [
    {{
      "sl_no": "i",
      "clause_ref": "Cl. 5.1-i or Section heading",
      "clause_header": "Basic Requirement label (e.g. Legal Entity, Annual Turnover)",
      "page_no": "Pg. 8",
      "criteria": "EXACT WORD-FOR-WORD text of the Specific Requirements column. Full sentences. Nothing missing.",
      "documents_required": "EXACT WORD-FOR-WORD text of Documents Required column. Every document. Every annexure.",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_color": "GREEN / RED / AMBER",
      "nascent_remark": "Specific evidence — cite cert name+validity, project name+value, exact figures. For Not Met: what exactly is missing. For Conditional: exact pre-bid query needed.",
      "calculation_shown": "For numeric criteria show the math: Required X | Nascent Y | Result. For non-numeric: —",
      "evidence_projects": "For experience criteria: list matching Nascent projects with values. For others: —",
      "raises_query": "YES — Query #N / NO"
    }}
  ]
}}

IMPORTANT: If no PQ/Eligibility section found, return {{"pq_criteria": []}}
"""

def step4_pq(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    pq_text = extract_section(text, [
        "Pre-Qualification Criteria\n", "Eligibility Criteria\n",
        "Eligibility Criteria \n", "minimum eligibility criteria",
        "A Bidder must meet", "Qualifying Criteria\n",
        "4. Eligibility", "5.1 Pre-Qualification",
        "ELIGIBILITY CRITERIA", "Eligibility:", "Minimum Eligibility",
        "Sr.\nNo.\nDescription", "S.no.\nParameter",
    ], chars=12000)
    if not pq_text:
        pq_text = text[2000:14000]
    prompt = PQ_PROMPT.format(nascent=NASCENT, text=pq_text[:13000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=5000)
        result = clean_json(raw)
        pq = result.get("pq_criteria", [])
        logger.info(f"[Step4-PQ] {len(pq)} criteria with refs+evidence")
        return result
    except Exception as e:
        logger.error(f"[Step4-PQ] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# STEP 5 — TQ CRITERIA WITH SLAB MATH
# ═══════════════════════════════════════════════════════

TQ_PROMPT = """You are extracting Technical Qualification / Technical Evaluation criteria from an Indian government tender.

RULE 1 — COPY WORD-FOR-WORD. The "criteria" and "eval_criteria" fields must be EXACT RFP text.
For eval_criteria: copy EVERY SLAB — every threshold, every mark allocation, exactly as written.
Example: "INR =7 Cr: 4 Marks | INR 7 Cr and <=10 Cr: 6 Marks | INR >10 Cr: 8 Marks" — copy this exactly.

RULE 2 — EXTRACT EVERY ROW.

RULE 3 — SLAB CALCULATION: Show explicit calculation for Nascent's score:
"Slab: <7Cr=4, 7-10Cr=6, >10Cr=8 | Nascent 3yr avg = Rs.17.18 Cr → falls in >10Cr slab → 8/8 marks"
"Slab: CMMI L3=5, L2=3, None=0 | Nascent: CMMI V2.0 L3 valid Dec-2026 → 5/5 marks"
"Slab: 3+ projects=10, 2 projects=7, 1 project=4 | Nascent qualifying projects: P1(10.55), P2(9.78), P3(4.2) → 3 projects → 10/10"

RULE 4 — PAGE + CLAUSE REFS for each row.

Nascent profile (INTERNAL):
{nascent}

Text:
{text}

Return ONLY valid JSON:
{{
  "tq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Annexure-II or Section ref",
      "page_no": "Pg. 15",
      "criteria": "EXACT WORD-FOR-WORD text of Criteria column",
      "eval_criteria": "EXACT WORD-FOR-WORD text of Evaluation Criteria column — every slab, every threshold, every mark allocation",
      "documents_required": "EXACT text of Documents Required column",
      "max_marks": "maximum marks as stated",
      "nascent_score": "Nascent estimated score (number)",
      "slab_calculation": "Show the slab math explicitly: Slab: X=N, Y=M | Nascent value = Z → falls in X slab → N/max marks",
      "nascent_status": "Met / Conditional / Not Met",
      "nascent_color": "GREEN / AMBER / RED",
      "nascent_remark": "Which slab, why, specific evidence",
      "raises_query": "YES — Query #N / NO"
    }}
  ],
  "tq_min_qualifying_score": "minimum qualifying score if stated",
  "tq_total_marks": "total marks if stated",
  "tq_nascent_estimated_total": "sum of all nascent_score values",
  "key_personnel": [
    {{
      "role": "role name as in RFP",
      "qualification": "exact qualification required",
      "experience": "exact experience required",
      "max_marks": "marks",
      "page_no": "Pg. X",
      "nascent_remark": "who in Nascent team qualifies and why"
    }}
  ]
}}
"""

def step5_tq(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    tq_text = extract_section(text, [
        "Technical Qualification", "Technical Evaluation", "TQ Criteria",
        "Annexure- II", "Annexure II", "Marking Scheme", "Max\nMarks",
        "Max Marks", "Evaluation Criteria", "S. No.\nCriteria",
    ], chars=10000)
    if not tq_text:
        return {}
    prompt = TQ_PROMPT.format(nascent=NASCENT, text=tq_text[:11000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=3500)
        result = clean_json(raw)
        tq_list = result.get("tq_criteria", [])
        for item in tq_list:
            if not isinstance(item, dict):
                continue
            # Build details field for backward compatibility with doc_generator
            eval_cr = item.get("eval_criteria", "") or item.get("details", "")
            docs_req = item.get("documents_required", "")
            max_marks = item.get("max_marks", "")
            slab = item.get("slab_calculation", "")
            parts = []
            if max_marks:
                parts.append(f"Max Marks: {max_marks}")
            if slab:
                parts.append(f"Score: {slab}")
            if eval_cr:
                parts.append(eval_cr)
            if docs_req:
                parts.append(f"Documents: {docs_req}")
            item["details"] = " | ".join(parts) if parts else ""
        logger.info(f"[Step5-TQ] {len(tq_list)} criteria with slab math")
        return result
    except Exception as e:
        logger.error(f"[Step5-TQ] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# STEP 5B — WORK SCHEDULE (NEW)
# ═══════════════════════════════════════════════════════

WORK_SCHEDULE_PROMPT = """Extract the work schedule / implementation timeline from this tender document.
Government tenders typically express timelines as T0 (contract signing date) + N days/months.

Return ONLY valid JSON:
{{
  "work_schedule": [
    {{
      "milestone_no": "M1",
      "milestone_name": "Name of milestone or deliverable",
      "activity": "What must be done",
      "timeline": "T0+30 days / Month 3 / Within 6 months etc.",
      "deliverable": "Document / System / Report to be submitted",
      "clause_ref": "Cl. X or Schedule ref",
      "page_no": "Pg. Y",
      "ld_applicable": "YES / NO — whether LD applies for delay of this milestone",
      "nascent_note": "Any capacity/risk concern for Nascent or —"
    }}
  ],
  "total_project_duration": "Total duration of Phase A + Phase B",
  "phase_a_duration": "Development/implementation phase duration",
  "phase_b_duration": "O&M/AMC phase duration",
  "ld_rate": "Liquidated damages rate for delay (e.g. 0.5% per week, max 10%) [Cl. X, Pg. Y]",
  "go_live_deadline": "When system must go live [Cl. X, Pg. Y]"
}}

Rules:
- Extract EVERY milestone from the timeline/schedule table
- Use T0 notation where timeline is relative to contract signing
- If phases have sub-milestones, list each separately
- nascent_note: flag if timeline seems aggressive or requires early mobilisation

Text:
{text}
"""

def step5b_work_schedule(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    sched_text = extract_section(text, [
        "Work Schedule", "Implementation Schedule", "Project Schedule",
        "Timeline", "Milestones", "Deliverables and Timeline",
        "Schedule of Work", "Phase A", "T+", "T0+",
    ], chars=8000)
    if not sched_text:
        # Try payment section which often has timeline too
        sched_text = extract_section(text, [
            "Payment Terms", "Payment Schedule", "Milestone Payment"
        ], chars=5000)
    if not sched_text:
        return {}
    prompt = WORK_SCHEDULE_PROMPT.format(text=sched_text[:9000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=4096)
        result = clean_json(raw)
        logger.info(f"[Step5B-WorkSchedule] {len(result.get('work_schedule',[]))} milestones")
        return result
    except Exception as e:
        logger.error(f"[Step5B-WorkSchedule] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# STEP 6 — PAYMENT SCHEDULE WITH AMOUNTS
# ═══════════════════════════════════════════════════════

PAYMENT_SCHEDULE_PROMPT = """Extract payment schedule and terms from this tender document.

Return ONLY valid JSON:
{{
  "payment_schedule": [
    {{
      "milestone_no": "M1",
      "milestone_name": "Milestone name",
      "trigger_activity": "What triggers this payment",
      "timeline": "When (T0+30 / On go-live / Quarterly etc.)",
      "payment_percent": "Percentage of contract value (e.g. 20%)",
      "clause_ref": "Cl. X",
      "page_no": "Pg. Y",
      "phase": "Phase A / Phase B"
    }}
  ],
  "phase_a_total_percent": "Total % payable during Phase A (development)",
  "phase_b_total_percent": "Total % payable during Phase B (O&M/AMC)",
  "advance_payment": "Advance payment if any — % and conditions [Cl. X, Pg. Y]",
  "retention_money": "Retention % — when withheld, when released [Cl. X, Pg. Y]",
  "penalty_clauses": [
    {{
      "type": "LD / SLA Penalty / Blacklisting / etc",
      "condition": "What triggers it",
      "penalty": "Amount or percentage",
      "max_cap": "Maximum cap if stated",
      "clause_ref": "Cl. X",
      "page_no": "Pg. Y"
    }}
  ],
  "pbg_details": "Performance Bank Guarantee — %, when required, release conditions [Cl. X, Pg. Y]",
  "ip_ownership": "Who owns source code, data, IP [Cl. X, Pg. Y]",
  "exit_clause": "Handover/transition requirements on contract end [Cl. X, Pg. Y]"
}}

Rules:
- Extract EVERY milestone row from the payment table
- Include O&M/AMC payment terms separately with their own milestones
- For penalty: include LD, SLA penalties, blacklisting clauses
- All clause+page refs mandatory

Text:
{text}
"""

def step6_payment(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    pay_text = extract_section(text, [
        "Payment Terms", "Payment Schedule", "PAYMENT TO THE AGENCY",
        "Milestone Payment", "Schedule of Payment",
        "Deliverables and Timeline", "Payment Terms, Deliverables"
    ], chars=7000)
    if not pay_text:
        return {}
    penalty_text = extract_section(text, [
        "Liquidated Damages", "Penalty", "Performance Bank Guarantee",
        "Retention", "SLA", "Service Level Agreement",
        "Termination", "Exit", "IP", "Intellectual Property"
    ], chars=4000)
    combined = pay_text + "\n\n" + penalty_text
    prompt = PAYMENT_SCHEDULE_PROMPT.format(text=combined[:11000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=4096)
        result = clean_json(raw)
        # Backward compat
        if "payment_schedule" in result and "payment_terms" not in result:
            result["payment_terms"] = result["payment_schedule"]
        if "penalty_clauses" in result:
            result["penalty_clauses"] = result["penalty_clauses"]
        logger.info(f"[Step6-Payment] {len(result.get('payment_schedule',[]))} milestones")
        return result
    except Exception as e:
        logger.error(f"[Step6-Payment] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# STEP 7 — ASSESSMENT + VERDICT + PROJECT MATCHES
# ═══════════════════════════════════════════════════════

ASSESSMENT_PROMPT = """You are a senior bid manager at a GIS/IT company making a Bid/No-Bid decision.
Do NOT name the company anywhere in your response.

Company profile (INTERNAL ONLY):
{nascent}

Tender: {tender_name}
Organization: {org_name}
Estimated Value: {estimated_cost}
Mode of Selection: {mode_of_selection}
Contract Period: {contract_period}
Bid Deadline: {bid_deadline}

PQ Assessment:
{pq_summary}

TQ Assessment:
{tq_summary}

Scope:
{scope_summary}

Return ONLY valid JSON:
{{
  "overall_recommendation": "BID / NO-BID / CONDITIONAL",
  "confidence_level": "HIGH / MEDIUM / LOW",
  "confidence_reason": "Why HIGH/MEDIUM/LOW — based on completeness of extraction",
  "hard_disqualifiers": [
    {{
      "criterion": "Criterion description",
      "clause_ref": "Cl. X, Pg. Y",
      "reason": "Why this disqualifies the bidder",
      "queryable": "Can this be resolved via pre-bid query? YES/NO",
      "query_suggestion": "If YES: what query would resolve this"
    }}
  ],
  "recommendation_reason": "3-5 specific reasons with evidence",
  "key_strengths": [
    "Strength 1 with specific evidence (cite project/cert/figure)"
  ],
  "key_risks": [
    "Risk 1 with specific detail"
  ],
  "project_matches": [
    {{
      "tender_requirement": "What tender needs (e.g. GIS mobile app with offline sync)",
      "matching_project": "Project name from profile",
      "project_value": "Rs. X Cr",
      "relevance": "Why this project qualifies — specific overlap with tender requirement",
      "strength": "STRONG / MODERATE / WEAK"
    }}
  ],
  "action_items": [
    {{
      "action": "Specific action (not generic — state WHICH document, WHICH person)",
      "responsible": "Bid Team / Finance / HR / MD",
      "target_date": "Specific date calculated from bid deadline: {bid_deadline}",
      "priority": "URGENT / HIGH / MEDIUM"
    }}
  ]
}}

Rules for verdict:
- BID: Meets all PQ criteria, good TQ score, strong domain match
- NO-BID: Hard disqualifier exists that CANNOT be resolved via query
- CONDITIONAL: Meets most criteria but 1-2 gaps resolvable via pre-bid query

Rules for project_matches:
- Only list projects that genuinely match what tender requires
- Be specific about WHY they match (shared technology, same client type, similar scope)
- Do NOT list projects that only marginally relate

Rules for action_items:
- First action: pre-bid queries (if deadline not passed)
- Include POA renewal warning if relevant (POA expired 31-Mar-2026)
- Be specific — not "prepare documents" but "prepare CA turnover certificate for FY22-23, FY23-24, FY24-25"
"""

def step7_assessment(snapshot: Dict, pq: Dict, tq: Dict, scope: Dict,
                     api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    def _val(d):
        """Extract value from {value, clause_ref, page_no} structure or plain string."""
        if isinstance(d, dict):
            return str(d.get("value", "") or "")
        return str(d or "")

    pq_items = pq.get("pq_criteria", [])
    pq_summary = "\n".join([
        f"- {item.get('clause_header','')} [{item.get('clause_ref','')} {item.get('page_no','')}]: {str(item.get('criteria','') or '')[:80]} → {item.get('nascent_status','')} | {str(item.get('nascent_remark','') or '')[:100]} | Calc: {str(item.get('calculation_shown','—') or '—')[:60]}"
        for item in pq_items if isinstance(item, dict)
    ]) or "No PQ criteria extracted"

    tq_items = tq.get("tq_criteria", [])
    tq_summary = "\n".join([
        f"- [{item.get('clause_ref','')} {item.get('page_no','')}] {str(item.get('criteria','') or '')[:60]} → Score: {item.get('nascent_score','?')}/{item.get('max_marks','?')}: {str(item.get('slab_calculation','') or '')[:80]}"
        for item in tq_items if isinstance(item, dict)
    ]) or "No TQ criteria extracted"

    scope_sections = scope.get("scope_sections", [])
    scope_summary = _val(scope.get("scope_background","")) + "\n" + "\n".join([
        f"- [{s.get('section_no','')}] {s.get('section_title','')}: {str(s.get('prose','') or '')[:100]}"
        for s in scope_sections[:5] if isinstance(s, dict)
    ])

    prompt = ASSESSMENT_PROMPT.format(
        nascent=NASCENT,
        tender_name=_val(snapshot.get("tender_name","Unknown")),
        org_name=_val(snapshot.get("org_name","Unknown")),
        estimated_cost=_val(snapshot.get("estimated_cost","Not stated")),
        mode_of_selection=_val(snapshot.get("mode_of_selection","—")),
        contract_period=_val(snapshot.get("contract_period","—")),
        bid_deadline=_val(snapshot.get("bid_submission_date","—")),
        pq_summary=pq_summary,
        tq_summary=tq_summary,
        scope_summary=scope_summary,
    )
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=3500)
        result = clean_json(raw)
        logger.info(f"[Step7-Assessment] verdict={result.get('overall_recommendation')}, confidence={result.get('confidence_level')}")
        return result
    except Exception as e:
        logger.error(f"[Step7-Assessment] failed: {e}")
        return {"overall_recommendation": "CONDITIONAL", "key_strengths": [str(e)]}


# ═══════════════════════════════════════════════════════
# STEP 8 — NOTES + CHECKLIST
# ═══════════════════════════════════════════════════════

NOTES_PROMPT = """Generate bid notes and submission checklist for this tender.

Tender details:
{tender_summary}

PQ criteria list:
{pq_list}

Return ONLY valid JSON:
{{
  "notes": [
    {{
      "title": "Short title (e.g. EMD Exemption, Reverse Auction, Physical Submission)",
      "detail": "Specific detail relevant to bid strategy — include clause ref and page if known",
      "priority": "HIGH / MEDIUM / INFO"
    }}
  ],
  "submission_checklist": [
    {{
      "category": "EMD & Fees / Legal / Financial / Technical / Certifications / Declarations / Proposal",
      "document": "Document name",
      "annexure": "Annexure/Form reference from RFP or —",
      "clause_ref": "Cl. X",
      "status": "Prepare / Compile / Ready / CRITICAL-verify",
      "nascent_note": "Any specific note for Nascent (e.g. POA expired, need renewal)"
    }}
  ]
}}

For notes: cover selection method, physical submission requirements, unusual clauses,
MSME exemption status, reverse auction, JV restrictions, key risks, important deadlines.

For checklist: include ALL documents from PQ + standard bid documents:
Standard: Cover letter, EMD/MSME exemption, Tender fee proof, PoA, Company registration,
PAN, GST, CA turnover certificate, Balance sheets (3yr), Work orders, Completion certs,
CMMI cert, ISO certs, Employee declaration, CVs for key personnel, Technical proposal,
Non-blacklisting declaration, MII declaration, No litigation declaration.
ALWAYS add POA renewal note if it appears in any checklist.
"""

def step8_notes_checklist(snapshot: Dict, pq: Dict,
                           api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    def _val(d):
        if isinstance(d, dict):
            return str(d.get("value", "") or "")
        return str(d or "")

    tender_summary = "\n".join([
        f"{k}: {_val(v)}" for k, v in snapshot.items()
        if _val(v) and _val(v) not in ("—", "Not specified", "")
        and k in ["org_name","tender_name","mode_of_selection","emd","emd_exemption",
                  "contract_period","jv_allowed","bid_submission_date","performance_security",
                  "prebid_query_date","estimated_cost"]
    ])
    pq_list = "\n".join([
        f"- {item.get('clause_header','Sr.'+ str(item.get('sl_no','?')))} [{item.get('clause_ref','')} {item.get('page_no','')}]: {str(item.get('criteria','') or '')[:80]} — docs: {str(item.get('documents_required','') or '')[:60]}"
        for item in pq.get("pq_criteria", []) if isinstance(item, dict)
    ]) or "See tender document"

    prompt = NOTES_PROMPT.format(
        tender_summary=tender_summary,
        pq_list=pq_list
    )
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=4096)
        result = clean_json(raw)
        # Always add POA warning to checklist
        checklist = result.get("submission_checklist", [])
        has_poa = any("poa" in str(item.get("document","")).lower() or "power of attorney" in str(item.get("document","")).lower() for item in checklist)
        if has_poa:
            for item in checklist:
                doc = str(item.get("document","")).lower()
                if "poa" in doc or "power of attorney" in doc:
                    item["status"] = "CRITICAL-verify"
                    item["nascent_note"] = "⚠ POA of Hitesh Patel EXPIRED 31-Mar-2026 — MUST RENEW before submission"
        else:
            checklist.append({
                "category": "Legal",
                "document": "Power of Attorney — Hitesh Patel (CAO)",
                "annexure": "—",
                "clause_ref": "—",
                "status": "CRITICAL-verify",
                "nascent_note": "⚠ POA EXPIRED 31-Mar-2026 — MUST RENEW before ANY bid submission"
            })
        result["submission_checklist"] = checklist
        logger.info(f"[Step8-Notes] {len(result.get('notes',[]))} notes, {len(checklist)} checklist items")
        return result
    except Exception as e:
        logger.error(f"[Step8-Notes] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# STEP 9 — PRE-BID QUERIES (GAPS ONLY, GENERIC, WITH GUIDELINES)
# ═══════════════════════════════════════════════════════

PREBID_PROMPT = """You are drafting pre-bid queries for submission to a government tender authority.

MANDATORY RULES:
1. NEVER name the bidder company in any query. Use "the bidder", "bidding firms", "IT/ITeS companies".
2. ONLY raise queries where there is a genuine gap or ambiguity — do NOT raise queries for clear criteria.
3. Where applicable, cite government guidelines:
   - GFR Rule 161 / Public Procurement Policy for MSMEs Order 2012 (for MSME/EMD exemption)
   - CVC guidelines on technology neutrality (for mandatory technology/platform)
   - DPIIT Startup notification (for startup exemptions)
   - MeitY guidelines (for IT procurement)
   - GeM guidelines (for GeM-specific queries)
4. Check if the RFP specifies a FORMAT for queries — if yes, structure queries in that exact format.
5. Each query must quote the EXACT RFP text being questioned.
6. Each query must state what specific written clarification is being sought.

RFP Query Format specified: {query_format}
Tender: {tender_name} | Org: {org_name}
Pre-bid query deadline: {query_deadline}

Criteria with gaps (AMBER/RED status):
{gaps_summary}

Return ONLY valid JSON:
{{
  "query_format_used": "RFP-specified format name / Standard letter format",
  "queries": [
    {{
      "query_no": "Q1",
      "priority": "HIGH / MEDIUM",
      "clause_ref": "Cl. X.Y",
      "page_no": "Pg. Z",
      "rfp_text": "EXACT verbatim text from RFP being questioned — quote the complete relevant sentence",
      "query": "Professional, specific question. Never mention company name. Frame as general policy question or bidder eligibility question. Cite applicable guideline if relevant.",
      "guideline_cited": "GFR Rule 161 / CVC circular / MeitY guideline / DPIIT notification / None",
      "clarification_sought": "Specific written response required — what must the authority confirm in writing",
      "gap_addressed": "Which PQ/TQ criterion this resolves (internal reference)"
    }}
  ],
  "email_subject": "Pre-Bid Queries — [Tender Ref] — [Brief Tender Name]",
  "total_queries": 0
}}
"""

def step9_prebid_queries(snapshot: Dict, pq: Dict, tq: Dict,
                          api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    def _val(d):
        if isinstance(d, dict):
            return str(d.get("value", "") or "")
        return str(d or "")

    # Collect ALL gaps (AMBER + RED)
    gaps = []
    query_no = 1

    for item in pq.get("pq_criteria", []):
        if not isinstance(item, dict):
            continue
        status = item.get("nascent_status", "")
        if status in ("Conditional", "Not Met"):
            gaps.append({
                "type": "PQ",
                "sl_no": item.get("sl_no",""),
                "clause_ref": item.get("clause_ref","—"),
                "page_no": item.get("page_no","—"),
                "clause_header": item.get("clause_header",""),
                "criteria": str(item.get("criteria","") or "")[:200],
                "remark": str(item.get("nascent_remark","") or "")[:150],
                "status": status,
                "calc": str(item.get("calculation_shown","") or "")[:100],
            })

    for item in tq.get("tq_criteria", []):
        if not isinstance(item, dict):
            continue
        status = item.get("nascent_status", "")
        if status in ("Conditional",):
            gaps.append({
                "type": "TQ",
                "sl_no": item.get("sl_no",""),
                "clause_ref": item.get("clause_ref","—"),
                "page_no": item.get("page_no","—"),
                "clause_header": item.get("criteria","")[:50],
                "criteria": str(item.get("eval_criteria","") or "")[:200],
                "remark": str(item.get("nascent_remark","") or "")[:150],
                "status": status,
                "calc": str(item.get("slab_calculation","") or "")[:100],
            })

    if not gaps:
        logger.info("[Step9-PreBid] No gaps found — no queries needed")
        return {"queries": [], "total_queries": 0, "query_format_used": "N/A"}

    gaps_summary = "\n".join([
        f"[{g['type']} Cl.{g['clause_ref']} {g['page_no']}] {g['clause_header']}: {g['criteria']} | Status: {g['status']} | Remark: {g['remark']} | Math: {g['calc']}"
        for g in gaps
    ])

    query_format = _val(snapshot.get("prebid_query_format", "Not specified"))

    prompt = PREBID_PROMPT.format(
        query_format=query_format,
        tender_name=_val(snapshot.get("tender_name","Tender")),
        org_name=_val(snapshot.get("org_name","Authority")),
        query_deadline=_val(snapshot.get("prebid_query_date","—")),
        gaps_summary=gaps_summary[:8000],
    )
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=3500)
        result = clean_json(raw)
        queries = result.get("queries", [])
        result["total_queries"] = len(queries)
        logger.info(f"[Step9-PreBid] {len(queries)} queries generated from {len(gaps)} gaps")
        return result
    except Exception as e:
        logger.error(f"[Step9-PreBid] failed: {e}")
        return {"queries": [], "total_queries": 0}


# ═══════════════════════════════════════════════════════
# NORMALIZE HELPERS
# ═══════════════════════════════════════════════════════

def normalize_verdict(rec: str):
    rec = str(rec).upper().strip()
    if any(x in rec for x in ["NO-BID","NO_BID","NOBID","NOT BID","REJECT"]):
        return "NO-BID", "RED"
    if any(x in rec for x in ["CONDITIONAL","CONDITION","AMBER","CAUTION"]):
        return "CONDITIONAL", "AMBER"
    if any(x in rec for x in ["BID","YES","PROCEED","GO"]):
        return "BID", "GREEN"
    return "CONDITIONAL", "AMBER"

def normalize_status(s: str):
    s = str(s).upper()
    if "NOT MET" in s or "NO" == s.strip() or "RED" in s:
        return "Not Met", "RED"
    if "CONDITIONAL" in s or "AMBER" in s or "PARTIAL" in s:
        return "Conditional", "AMBER"
    if "MET" in s or "YES" == s.strip() or "GREEN" in s:
        return "Met", "GREEN"
    return "Review", "BLUE"

def prebid_passed(date_str: str) -> bool:
    import re as _re
    from datetime import date
    if not date_str or date_str in ("—", "Not specified", ""):
        return False
    nums = _re.findall(r'\d+', str(date_str))
    if len(nums) >= 3:
        try:
            parts = [int(x) for x in nums[:3]]
            if parts[0] > 1000:
                d = date(parts[0], parts[1], parts[2])
            elif parts[2] > 1000:
                d = date(parts[2], parts[1], parts[0])
            else:
                d = date(2026, parts[1] if parts[1] <= 12 else parts[0], parts[0])
            return d < date.today()
        except Exception:
            pass
    return False


# ═══════════════════════════════════════════════════════
# TEXT CORPUS BUILDER
# ═══════════════════════════════════════════════════════

def build_text_corpus(all_text: str) -> tuple:
    pattern = r'=== FILE: ([^=\n]+) ===\n'
    markers = list(re.finditer(pattern, all_text))
    if not markers:
        return all_text, []

    main_parts = []
    corrigendum_parts = []

    for i, marker in enumerate(markers):
        fname = marker.group(1).strip().lower()
        start = marker.end()
        end = markers[i+1].start() if i+1 < len(markers) else len(all_text)
        content = all_text[start:end].strip()
        if not content:
            continue
        is_corr = (
            "corrigendum" in fname or "addendum" in fname or "amendment" in fname or
            "corrigendum" in content[:200].lower() or
            "bid extended to" in content[:500].lower() or
            bool(re.search(r'extended to \d{4}-\d{2}-\d{2}', content[:500]))
        )
        if is_corr:
            corrigendum_parts.append(content)
        else:
            main_parts.append(content)

    return "\n\n".join(main_parts), corrigendum_parts


# ═══════════════════════════════════════════════════════
# REGEX FALLBACK SNAPSHOT
# ═══════════════════════════════════════════════════════

def regex_extract_snapshot(text: str) -> Dict:
    """Fast regex baseline — AI overrides these with richer data."""
    result = {}

    def first_match(patterns, txt=text):
        for p in patterns:
            m = re.search(p, txt, re.IGNORECASE | re.MULTILINE)
            if m:
                try:
                    val = m.group(1).strip().rstrip('/-')
                except IndexError:
                    val = m.group(0).strip()
                if val and len(val) < 300:
                    return {"value": val, "clause_ref": "—", "page_no": "—"}
        return {"value": "—", "clause_ref": "—", "page_no": "—"}

    result["tender_no"] = first_match([
        r'(DC/[A-Z ]+/\d+/\d{4}-\d{2,4})',
        r'(GEM/\d{4}/[A-Z]/\d+)',
        r'(?:NIT|Tender)\s*No\.?[:\s]+([A-Z0-9/_\-\.]{4,40})',
    ])
    result["org_name"] = first_match([
        r'(?:Organisation Name|Organization)[:\s]+([^\n]+)',
        r'(?:Issued by|Inviting Authority)[:\s]+([^\n]+)',
    ])
    result["bid_submission_date"] = first_match([
        r'(?:Bid End Date|Last Date.*Submission|Online Bid End Date)[:/\s]+([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}[^\n]{0,20})',
    ])
    result["estimated_cost"] = first_match([
        r'(?:Estimated Cost|NIT Value)[:\s]+((?:Rs\.?|INR|₹)\s*[\d,]+[^\n]{0,30})',
    ])
    result["emd"] = first_match([
        r'EMD[^\n]*?((?:Rs\.?|INR|₹)\s*[\d,]+)',
        r'Earnest Money[^\n]*?((?:Rs\.?|INR|₹)\s*[\d,]+)',
    ])
    result["tender_fee"] = first_match([
        r'(?:Tender Fee|Bid Fee|Document Fee)[^\n]*?((?:Rs\.?|INR|₹)\s*[\d,]+[^\n]{0,50})',
    ])
    result["prebid_query_date"] = first_match([
        r'(?:Pre-Bid Quer|Last Date.*Quer)[^\n]*?([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}[^\n]{0,20})',
    ])
    result["contact"] = first_match([
        r'(?:Contact|email)[:\s]+([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
    ])
    result["portal"] = first_match([
        r'(https?://[a-zA-Z0-9./\-]+(?:tender|gem|eproc|nprocure|bid)[a-zA-Z0-9./\-]*)',
    ])

    return result


# ═══════════════════════════════════════════════════════
# MASTER PIPELINE
# ═══════════════════════════════════════════════════════

def analyze_with_gemini(full_text: str, prebid_passed_flag: bool = False) -> Dict[str, Any]:
    """
    10-Segment pipeline. Returns complete analysis dict.
    Falls back to regex if all API keys exhausted.
    """
    # Refresh profile text at runtime from nascent_profile.json if available
    global NASCENT
    NASCENT = _load_nascent_profile_text()

    all_keys = get_all_api_keys()
    if not all_keys:
        return {"error": "No Gemini API key configured. Go to Settings → Gemini AI Keys."}

    cfg = load_config()
    groq_key = cfg.get("groq_api_key", cfg.get("groq_key", ""))
    api_key = all_keys[0]

    print(f"[AI Pipeline v7] Starting — {len(full_text)} chars, {len(all_keys)} API keys")

    main_text, corrigendum_texts = build_text_corpus(full_text)
    print(f"[AI Pipeline v7] Main: {len(main_text)} chars | Corrigendums: {len(corrigendum_texts)}")

    # Dedup if repeated content
    if len(main_text) > 50000:
        quarter = len(main_text) // 4
        if main_text[:500] == main_text[quarter:quarter+500]:
            main_text = main_text[:quarter]
            print(f"[AI Pipeline v7] Deduplicated — using {len(main_text)} chars")

    # Hard cap — Render free tier has 512MB RAM; keep corpus under 300KB
    MAX_MAIN = 300_000
    if len(main_text) > MAX_MAIN:
        keep_head = int(MAX_MAIN * 0.8)
        keep_tail = MAX_MAIN - keep_head
        main_text = main_text[:keep_head] + "\n\n[... TRUNCATED FOR MEMORY ...]\n\n" + main_text[-keep_tail:]
        print(f"[AI Pipeline v7] Corpus capped at {MAX_MAIN} chars for memory safety")

    # Regex baseline
    result = regex_extract_snapshot(full_text)
    print(f"[AI Pipeline v7] Regex baseline: {len(result)} fields")

    any_ai_success = False
    api_quota_exhausted = False

    # ── SEGMENT 1: SNAPSHOT ──────────────────────────────────
    print("[AI Pipeline v7] Segment 1: Snapshot...")
    try:
        snapshot = step1_snapshot(main_text, api_key, all_keys, groq_key)
        if snapshot and len(snapshot) > 3:
            for k, v in snapshot.items():
                val = v.get("value","") if isinstance(v, dict) else str(v or "")
                if val and str(val).strip() not in ("—","null","None",""):
                    result[k] = v  # Keep full {value, clause_ref, page_no} structure
            any_ai_success = True
            print(f"[AI Pipeline v7] Segment 1 OK: {len(snapshot)} fields with refs")
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            api_quota_exhausted = True
        print(f"[AI Pipeline v7] Segment 1 failed: {str(e)[:80]}")

    # ── SEGMENT 2: CORRIGENDUMS ──────────────────────────────
    print("[AI Pipeline v7] Segment 2: Corrigendums...")
    if corrigendum_texts and not api_quota_exhausted:
        try:
            corr = step2_corrigendums(corrigendum_texts, api_key, all_keys, groq_key)
            if corr:
                for field in ["bid_submission_date","bid_opening_date","prebid_meeting","prebid_query_date"]:
                    if corr.get(field) and corr[field] not in ("null","—",""):
                        old_val = result.get(field, {})
                        old_str = old_val.get("value","") if isinstance(old_val, dict) else str(old_val)
                        result[field] = {"value": corr[field], "clause_ref": "Corrigendum", "page_no": "—"}
                        print(f"[AI Pipeline v7] Date override: {field}: {old_str} → {corr[field]}")
                if corr.get("corrigendum_note"):
                    result["corrigendum_note"] = corr["corrigendum_note"]
                result["has_corrigendum"] = True
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                api_quota_exhausted = True
            print(f"[AI Pipeline v7] Segment 2 failed: {str(e)[:60]}")

    if api_quota_exhausted:
        print("[AI Pipeline v7] API quota exhausted — returning regex baseline")
        result["ai_warning"] = "API quota exhausted — basic extraction only. Add Gemini key in Settings or try tomorrow."
        result["verdict"] = "CONDITIONAL"
        result["overall_verdict"] = {"verdict":"CONDITIONAL","color":"BLUE","reason":"API quota exhausted — manual review required","green":0,"amber":0,"red":0}
        return result

    # ── SEGMENT 3: SCOPE ─────────────────────────────────────
    print("[AI Pipeline v7] Segment 3: Scope of Work...")
    scope = {}
    try:
        scope = step3_scope(main_text, api_key, all_keys, groq_key)
        if scope.get("scope_background"):
            result["scope_background"] = scope["scope_background"]
        if scope.get("scope_sections"):
            result["scope_sections"] = scope["scope_sections"]
            # Also set scope_items for backward compat
            result["scope_items"] = [{"title": s.get("section_title",""), "description": s.get("prose",""), "section_no": s.get("section_no",""), "page_no": s.get("page_no",""), "deliverables": s.get("deliverables",[]), "tech_specified": s.get("tech_specified","—"), "phase": s.get("phase","—")} for s in scope.get("scope_sections",[]) if isinstance(s, dict)]
        if scope.get("key_integrations"):
            result["key_integrations"] = scope["key_integrations"]
        any_ai_success = True
    except Exception as e:
        print(f"[AI Pipeline v7] Segment 3 failed: {str(e)[:60]}")

    # ── SEGMENT 4: PQ ────────────────────────────────────────
    print("[AI Pipeline v7] Segment 4: PQ Criteria...")
    pq = {}
    try:
        pq = step4_pq(main_text, api_key, all_keys, groq_key)
        if pq.get("pq_criteria"):
            normalized = []
            for item in pq["pq_criteria"]:
                if not isinstance(item, dict):
                    continue
                status, color = normalize_status(item.get("nascent_status","Review"))
                normalized.append({
                    "sl_no":           str(item.get("sl_no","") or ""),
                    "clause_ref":      str(item.get("clause_ref","—") or "—"),
                    "clause_header":   str(item.get("clause_header","") or ""),
                    "page_no":         str(item.get("page_no","—") or "—"),
                    "criteria":        str(item.get("criteria","") or ""),
                    "details":         str(item.get("documents_required","") or ""),
                    "documents_required": str(item.get("documents_required","") or ""),
                    "nascent_status":  status,
                    "nascent_color":   color,
                    "nascent_remark":  str(item.get("nascent_remark","") or ""),
                    "calculation_shown": str(item.get("calculation_shown","—") or "—"),
                    "evidence_projects": str(item.get("evidence_projects","—") or "—"),
                    "raises_query":    str(item.get("raises_query","NO") or "NO"),
                })
            result["pq_criteria"] = normalized
            pq["pq_criteria"] = normalized
            any_ai_success = True
    except Exception as e:
        print(f"[AI Pipeline v7] Segment 4 failed: {str(e)[:60]}")

    # ── SEGMENT 5: TQ ────────────────────────────────────────
    print("[AI Pipeline v7] Segment 5: TQ Criteria...")
    tq = {}
    try:
        tq = step5_tq(main_text, api_key, all_keys, groq_key)
        if tq.get("tq_criteria"):
            normalized_tq = []
            for item in tq["tq_criteria"]:
                if not isinstance(item, dict):
                    continue
                status, color = normalize_status(item.get("nascent_status","Review"))
                normalized_tq.append({
                    "sl_no":          str(item.get("sl_no","") or ""),
                    "clause_ref":     str(item.get("clause_ref","—") or "—"),
                    "page_no":        str(item.get("page_no","—") or "—"),
                    "criteria":       str(item.get("criteria","") or ""),
                    "eval_criteria":  str(item.get("eval_criteria","") or ""),
                    "details":        str(item.get("details","") or ""),
                    "max_marks":      str(item.get("max_marks","") or ""),
                    "nascent_score":  str(item.get("nascent_score","") or ""),
                    "slab_calculation": str(item.get("slab_calculation","") or ""),
                    "documents_required": str(item.get("documents_required","") or ""),
                    "nascent_status": status,
                    "nascent_color":  color,
                    "nascent_remark": str(item.get("nascent_remark","") or ""),
                    "raises_query":   str(item.get("raises_query","NO") or "NO"),
                })
            result["tq_criteria"] = normalized_tq
            result["tq_min_qualifying_score"] = tq.get("tq_min_qualifying_score","")
            result["tq_total_marks"] = tq.get("tq_total_marks","")
            result["tq_nascent_estimated_total"] = tq.get("tq_nascent_estimated_total","")
            result["key_personnel"] = tq.get("key_personnel",[])
            tq["tq_criteria"] = normalized_tq
    except Exception as e:
        print(f"[AI Pipeline v7] Segment 5 failed: {str(e)[:60]}")

    # ── SEGMENT 5B: WORK SCHEDULE ────────────────────────────
    print("[AI Pipeline v7] Segment 5B: Work Schedule...")
    try:
        work_sched = step5b_work_schedule(main_text, api_key, all_keys, groq_key)
        if work_sched.get("work_schedule"):
            result["work_schedule"] = work_sched["work_schedule"]
            result["total_project_duration"] = work_sched.get("total_project_duration","")
            result["phase_a_duration"] = work_sched.get("phase_a_duration","")
            result["phase_b_duration"] = work_sched.get("phase_b_duration","")
            result["ld_rate"] = work_sched.get("ld_rate","")
            result["go_live_deadline"] = work_sched.get("go_live_deadline","")
    except Exception as e:
        print(f"[AI Pipeline v7] Segment 5B failed: {str(e)[:60]}")

    # ── SEGMENT 6: PAYMENT ───────────────────────────────────
    print("[AI Pipeline v7] Segment 6: Payment Schedule...")
    try:
        payment = step6_payment(main_text, api_key, all_keys, groq_key)
        if payment.get("payment_schedule"):
            result["payment_schedule"] = payment["payment_schedule"]
            result["payment_terms"] = payment["payment_schedule"]  # backward compat
        if payment.get("penalty_clauses"):
            result["penalty_clauses"] = payment["penalty_clauses"]
        for f in ["advance_payment","retention_money","pbg_details","ip_ownership","exit_clause","phase_a_total_percent","phase_b_total_percent"]:
            if payment.get(f):
                result[f] = payment[f]
    except Exception as e:
        print(f"[AI Pipeline v7] Segment 6 failed: {str(e)[:60]}")

    # ── SEGMENT 7: ASSESSMENT ────────────────────────────────
    print("[AI Pipeline v7] Segment 7: Bid/No-Bid Assessment...")
    assessment = {}
    try:
        assessment = step7_assessment(result, pq, tq, scope, api_key, all_keys, groq_key)
        for f in ["project_matches","action_items","key_strengths","key_risks","hard_disqualifiers","confidence_level","confidence_reason"]:
            if assessment.get(f):
                result[f] = assessment[f]
    except Exception as e:
        print(f"[AI Pipeline v7] Segment 7 failed: {str(e)[:60]}")

    # Build overall_verdict
    rec = assessment.get("overall_recommendation","CONDITIONAL") if assessment else "CONDITIONAL"
    reason = assessment.get("recommendation_reason","") if assessment else ""
    verdict, color = normalize_verdict(rec)
    result["verdict"] = verdict
    pq_list = result.get("pq_criteria",[])
    result["overall_verdict"] = {
        "verdict": verdict,
        "reason": reason,
        "color": color,
        "green": sum(1 for p in pq_list if p.get("nascent_color") == "GREEN"),
        "amber": sum(1 for p in pq_list if p.get("nascent_color") == "AMBER"),
        "red":   sum(1 for p in pq_list if p.get("nascent_color") == "RED"),
    }

    # ── SEGMENT 8: NOTES + CHECKLIST ─────────────────────────
    print("[AI Pipeline v7] Segment 8: Notes + Checklist...")
    try:
        notes = step8_notes_checklist(result, pq, api_key, all_keys, groq_key)
        if notes.get("notes"):
            result["notes"] = notes["notes"]
        if notes.get("submission_checklist"):
            result["submission_checklist"] = notes["submission_checklist"]
    except Exception as e:
        print(f"[AI Pipeline v7] Segment 8 failed: {str(e)[:60]}")

    # ── SEGMENT 9: PRE-BID QUERIES ───────────────────────────
    print("[AI Pipeline v7] Segment 9: Pre-bid Queries...")
    try:
        prebid = step9_prebid_queries(result, pq, tq, api_key, all_keys, groq_key)
        if prebid.get("queries"):
            result["prebid_queries"] = prebid["queries"]
            result["prebid_query_count"] = prebid.get("total_queries", len(prebid["queries"]))
            result["prebid_email_subject"] = prebid.get("email_subject","")
            result["prebid_query_format_used"] = prebid.get("query_format_used","Standard letter format")
    except Exception as e:
        print(f"[AI Pipeline v7] Segment 9 failed: {str(e)[:60]}")

    print(f"[AI Pipeline v7] Complete — verdict={verdict}, PQ={len(pq_list)}, confidence={result.get('confidence_level','—')}")
    return result


def merge_results(regex_data: Dict, ai_data: Dict, prebid_passed: bool = False) -> Dict:
    """Backward compat — merge called from main.py."""
    if ai_data and "error" not in ai_data:
        # AI result is already complete — just return it
        return ai_data
    return regex_data


# ═══════════════════════════════════════════════════════
# PARALLEL PIPELINE — 10+ tenders/day throughput
# Runs independent segments concurrently via ThreadPool.
# Pool handles per-key RPM, so no two threads hit same key.
# ═══════════════════════════════════════════════════════

def analyze_with_gemini_parallel(full_text: str, prebid_passed_flag: bool = False,
                                  progress_cb=None) -> Dict[str, Any]:
    """
    Parallel 9-segment pipeline. Drop-in replacement for analyze_with_gemini.
    Independent segments (1,2,3,4,5,5B,6) run concurrently.
    Dependent segments (7,8,9) run sequentially after.

    progress_cb: optional callable(stage_name:str, done:int, total:int)
    """
    import concurrent.futures as _cf

    global NASCENT
    NASCENT = _load_nascent_profile_text()

    all_keys = get_all_api_keys()
    if not all_keys:
        return {"error": "No Gemini API key configured. Go to Settings → Gemini AI Keys."}

    try:
        from core.api_pool import refresh_pool
        refresh_pool()
    except Exception:
        pass

    cfg = load_config()
    groq_key = cfg.get("groq_api_key", cfg.get("groq_key", ""))
    api_key = all_keys[0]

    print(f"[AI-Parallel] start — text={len(full_text)} keys={len(all_keys)}")

    main_text, corrigendum_texts = build_text_corpus(full_text)

    if len(main_text) > 50000:
        quarter = len(main_text) // 4
        if main_text[:500] == main_text[quarter:quarter+500]:
            main_text = main_text[:quarter]

    MAX_MAIN = 300_000
    if len(main_text) > MAX_MAIN:
        keep_head = int(MAX_MAIN * 0.8)
        keep_tail = MAX_MAIN - keep_head
        main_text = main_text[:keep_head] + "\n\n[... TRUNCATED ...]\n\n" + main_text[-keep_tail:]

    result = regex_extract_snapshot(full_text)

    def _run(label, fn, *args):
        try:
            return label, fn(*args), None
        except Exception as e:
            return label, None, str(e)[:160]

    tasks = [
        ("snapshot",  lambda: step1_snapshot(main_text, api_key, all_keys, groq_key)),
        ("scope",     lambda: step3_scope(main_text, api_key, all_keys, groq_key)),
        ("pq",        lambda: step4_pq(main_text, api_key, all_keys, groq_key)),
        ("tq",        lambda: step5_tq(main_text, api_key, all_keys, groq_key)),
        ("workshed",  lambda: step5b_work_schedule(main_text, api_key, all_keys, groq_key)),
        ("payment",   lambda: step6_payment(main_text, api_key, all_keys, groq_key)),
    ]
    if corrigendum_texts:
        tasks.append(("corrig",
                      lambda: step2_corrigendums(corrigendum_texts, api_key, all_keys, groq_key)))

    outputs: Dict[str, Dict] = {}
    errors: Dict[str, str] = {}
    total = len(tasks) + 3  # +7,+8,+9
    done_count = 0

    try:
        import os as _os
        max_workers = int(_os.environ.get("ANALYST_SEGMENT_WORKERS", "4"))
    except Exception:
        max_workers = 4

    SEG_TIMEOUT = 200  # 200s per segment — 120s HTTP + pool overhead

    with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(lambda fn=fn: fn()): label for label, fn in tasks}
        for fut in _cf.as_completed(futs):
            label = futs[fut]
            try:
                outputs[label] = fut.result(timeout=SEG_TIMEOUT) or {}
            except _cf.TimeoutError:
                errors[label] = f"segment timeout >{SEG_TIMEOUT}s"
                outputs[label] = {}
            except Exception as e:
                errors[label] = str(e)[:160]
                outputs[label] = {}
            done_count += 1
            if progress_cb:
                try:
                    progress_cb(label, done_count, total)
                except Exception:
                    pass

    # Merge snapshot
    snapshot = outputs.get("snapshot", {})
    if snapshot and len(snapshot) > 3:
        for k, v in snapshot.items():
            val = v.get("value","") if isinstance(v, dict) else str(v or "")
            if val and str(val).strip() not in ("—","null","None",""):
                result[k] = v

    # Merge corrigendum
    corr = outputs.get("corrig", {})
    if corr:
        for field in ["bid_submission_date","bid_opening_date","prebid_meeting","prebid_query_date"]:
            if corr.get(field) and corr[field] not in ("null","—",""):
                result[field] = {"value": corr[field], "clause_ref": "Corrigendum", "page_no": "—"}
        if corr.get("corrigendum_note"):
            result["corrigendum_note"] = corr["corrigendum_note"]
        result["has_corrigendum"] = True

    # Merge scope
    scope = outputs.get("scope", {})
    if scope.get("scope_background"):
        result["scope_background"] = scope["scope_background"]
    if scope.get("scope_sections"):
        result["scope_sections"] = scope["scope_sections"]
        result["scope_items"] = [{"title": s.get("section_title",""), "description": s.get("prose",""),
                                   "section_no": s.get("section_no",""), "page_no": s.get("page_no",""),
                                   "deliverables": s.get("deliverables",[]),
                                   "tech_specified": s.get("tech_specified","—"),
                                   "phase": s.get("phase","—")}
                                  for s in scope.get("scope_sections",[]) if isinstance(s, dict)]
    if scope.get("key_integrations"):
        result["key_integrations"] = scope["key_integrations"]

    # Merge PQ
    pq = outputs.get("pq", {})
    if pq.get("pq_criteria"):
        normalized = []
        for item in pq["pq_criteria"]:
            if not isinstance(item, dict):
                continue
            status, color = normalize_status(item.get("nascent_status","Review"))
            normalized.append({
                "sl_no": str(item.get("sl_no","") or ""),
                "clause_ref": str(item.get("clause_ref","—") or "—"),
                "clause_header": str(item.get("clause_header","") or ""),
                "page_no": str(item.get("page_no","—") or "—"),
                "criteria": str(item.get("criteria","") or ""),
                "details": str(item.get("documents_required","") or ""),
                "documents_required": str(item.get("documents_required","") or ""),
                "nascent_status": status,
                "nascent_color": color,
                "nascent_remark": str(item.get("nascent_remark","") or ""),
                "calculation_shown": str(item.get("calculation_shown","—") or "—"),
                "evidence_projects": str(item.get("evidence_projects","—") or "—"),
                "raises_query": str(item.get("raises_query","NO") or "NO"),
            })
        result["pq_criteria"] = normalized
        pq["pq_criteria"] = normalized

    # Merge TQ
    tq = outputs.get("tq", {})
    if tq.get("tq_criteria"):
        normalized_tq = []
        for item in tq["tq_criteria"]:
            if not isinstance(item, dict):
                continue
            status, color = normalize_status(item.get("nascent_status","Review"))
            normalized_tq.append({
                "sl_no": str(item.get("sl_no","") or ""),
                "clause_ref": str(item.get("clause_ref","—") or "—"),
                "page_no": str(item.get("page_no","—") or "—"),
                "criteria": str(item.get("criteria","") or ""),
                "eval_criteria": str(item.get("eval_criteria","") or ""),
                "details": str(item.get("details","") or ""),
                "max_marks": str(item.get("max_marks","") or ""),
                "nascent_score": str(item.get("nascent_score","") or ""),
                "slab_calculation": str(item.get("slab_calculation","") or ""),
                "documents_required": str(item.get("documents_required","") or ""),
                "nascent_status": status,
                "nascent_color": color,
                "nascent_remark": str(item.get("nascent_remark","") or ""),
                "raises_query": str(item.get("raises_query","NO") or "NO"),
            })
        result["tq_criteria"] = normalized_tq
        result["tq_min_qualifying_score"] = tq.get("tq_min_qualifying_score","")
        result["tq_total_marks"] = tq.get("tq_total_marks","")
        result["tq_nascent_estimated_total"] = tq.get("tq_nascent_estimated_total","")
        result["key_personnel"] = tq.get("key_personnel",[])
        tq["tq_criteria"] = normalized_tq

    # Merge work schedule
    ws = outputs.get("workshed", {})
    if ws.get("work_schedule"):
        result["work_schedule"] = ws["work_schedule"]
        result["total_project_duration"] = ws.get("total_project_duration","")
        result["phase_a_duration"] = ws.get("phase_a_duration","")
        result["phase_b_duration"] = ws.get("phase_b_duration","")
        result["ld_rate"] = ws.get("ld_rate","")
        result["go_live_deadline"] = ws.get("go_live_deadline","")

    # Merge payment
    payment = outputs.get("payment", {})
    if payment.get("payment_schedule"):
        result["payment_schedule"] = payment["payment_schedule"]
        result["payment_terms"] = payment["payment_schedule"]
    if payment.get("penalty_clauses"):
        result["penalty_clauses"] = payment["penalty_clauses"]
    for f in ["advance_payment","retention_money","pbg_details","ip_ownership","exit_clause",
              "phase_a_total_percent","phase_b_total_percent"]:
        if payment.get(f):
            result[f] = payment[f]

    # Segment 7: Assessment (depends on PQ+TQ+scope)
    assessment = {}
    try:
        assessment = step7_assessment(result, pq, tq, scope, api_key, all_keys, groq_key)
        for f in ["project_matches","action_items","key_strengths","key_risks",
                  "hard_disqualifiers","confidence_level","confidence_reason"]:
            if assessment.get(f):
                result[f] = assessment[f]
    except Exception as e:
        errors["assessment"] = str(e)[:160]
    done_count += 1
    if progress_cb:
        try: progress_cb("assessment", done_count, total)
        except Exception: pass

    rec = assessment.get("overall_recommendation","CONDITIONAL") if assessment else "CONDITIONAL"
    reason = assessment.get("recommendation_reason","") if assessment else ""
    verdict, color = normalize_verdict(rec)
    result["verdict"] = verdict
    pq_list = result.get("pq_criteria",[])
    result["overall_verdict"] = {
        "verdict": verdict, "reason": reason, "color": color,
        "green": sum(1 for p in pq_list if p.get("nascent_color") == "GREEN"),
        "amber": sum(1 for p in pq_list if p.get("nascent_color") == "AMBER"),
        "red":   sum(1 for p in pq_list if p.get("nascent_color") == "RED"),
    }

    # Segment 8: Notes + Checklist
    try:
        notes = step8_notes_checklist(result, pq, api_key, all_keys, groq_key)
        if notes.get("notes"):
            result["notes"] = notes["notes"]
        if notes.get("submission_checklist"):
            result["submission_checklist"] = notes["submission_checklist"]
    except Exception as e:
        errors["notes"] = str(e)[:160]
    done_count += 1
    if progress_cb:
        try: progress_cb("notes", done_count, total)
        except Exception: pass

    # Segment 9: Pre-bid queries
    try:
        prebid = step9_prebid_queries(result, pq, tq, api_key, all_keys, groq_key)
        if prebid.get("queries"):
            result["prebid_queries"] = prebid["queries"]
            result["prebid_query_count"] = prebid.get("total_queries", len(prebid["queries"]))
            result["prebid_email_subject"] = prebid.get("email_subject","")
            result["prebid_query_format_used"] = prebid.get("query_format_used","Standard letter format")
    except Exception as e:
        errors["prebid"] = str(e)[:160]
    done_count += 1
    if progress_cb:
        try: progress_cb("prebid", done_count, total)
        except Exception: pass

    if errors:
        result["ai_segment_errors"] = errors
    print(f"[AI-Parallel] done verdict={verdict} errs={list(errors.keys())}")
    return result
