"""
AI Analyzer v6 — Multi-Call Pipeline
Each section gets its own focused prompt.
This is how a human analyst works: one job at a time, done well.

Pipeline:
  Step 1 — Extract snapshot (dates, amounts, org, contact)
  Step 2 — Extract corrigendum / date overrides
  Step 3 — Extract scope (background + rich components + integrations)
  Step 4 — Extract PQ criteria (word-for-word, every row)
  Step 5 — Extract TQ criteria (with marks)
  Step 6 — Extract payment milestones
  Step 7 — Generate Nascent assessment + pre-bid queries + verdict
  Step 8 — Generate action items + checklist + notes
"""

import json, re, os, urllib.request, urllib.error, logging
from pathlib import Path
from typing import Dict, Any, List, Optional

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

NASCENT = """NASCENT INFO TECHNOLOGIES PVT. LTD.:
- CIN: U72200GJ2006PTC048723 | MSME: UDYAM-GJ-01-0007420
- PAN: AACCN3670J | GSTIN: 24AACCN3670J1ZG
- Founded: 2006 (19 years) | Employees: 67 (11 GIS, 21 IT/Dev, rest QA/PM/BA/Support)
- Turnover: FY23=16.36Cr, FY24=16.36Cr, FY25=18.83Cr | Avg 3yr: 17.18Cr | Net Worth: 26.09Cr
- Certs: CMMI V2.0 L3 (valid Dec-2026), ISO 9001/27001/20000 (valid Sep-2028)
- Tech: Java/Spring Boot, Python/Django, React/Angular, Flutter, QGIS, ArcGIS, GeoServer, PostgreSQL, MongoDB
- Projects: AMC GIS (10.55Cr), JuMC GIS (9.78Cr), VMC GIS+ERP (20.5Cr), KVIC Mobile GIS (PAN India),
  PCSCL Smart City, TCGL, BMC Android+iOS GIS, NSO Census, NP Lalganj
- Signatory: Hitesh Patel (CAO) | POA valid: 01/04/2026-31/03/2027
- Address: A-805 Shapath IV, SG Highway, Prahlad Nagar, Ahmedabad 380015
- MD: Maulik Bhagat | nascent.tender@nascentinfo.com"""


# ═══════════════════════════════════════════════════════════════
# CONFIG + API CALLS
# ═══════════════════════════════════════════════════════════════
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
    config = load_config()
    keys = list(config.get("gemini_api_keys", []))
    primary = config.get("gemini_api_key", "")
    if primary and primary not in keys:
        keys.insert(0, primary)
    return [k for k in keys if k and k.strip() and len(k.strip()) > 20]


def call_gemini(prompt: str, api_key: str, max_tokens: int = 8192) -> str:
    last_error = None
    for model in GEMINI_MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens},
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
                logger.info(f"Gemini OK: {model}")
                return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code in [429, 503, 500, 404]:
                logger.warning(f"{model} {e.code} — next")
                last_error = f"HTTP {e.code}"; continue
            raise Exception(f"Gemini HTTP {e.code}: {body[:100]}")
        except Exception as e:
            logger.warning(f"{model} failed: {e}")
            last_error = str(e); continue
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
                last_error = f"HTTP {e.code}"; continue
            raise Exception(f"Groq HTTP {e.code}: {body[:100]}")
        except Exception as e:
            last_error = str(e); continue
    raise Exception(f"All Groq models failed: {last_error}")


def _call(prompt: str, api_key: str, all_keys: List[str],
          groq_key: str = "", max_tokens: int = 8192) -> str:
    """Try all Gemini keys, then Groq. Returns raw text."""
    last_err = None
    for key in all_keys:
        try:
            return call_gemini(prompt, key, max_tokens)
        except Exception as e:
            err = str(e).lower()
            if "quota" in err or "429" in err or "exhausted" in err:
                last_err = str(e); continue
            last_err = str(e); continue
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


# ═══════════════════════════════════════════════════════════════
# TEXT EXTRACTION HELPERS
# ═══════════════════════════════════════════════════════════════
def extract_section(text: str, keywords: List[str], chars: int = 5000) -> str:
    """Extract a section from text — skips TOC entries, finds real content."""
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
    return text[max(0, best-50):best+chars]


def get_first_n_chars(text: str, n: int = 6000) -> str:
    return text[:n]


def get_last_n_chars(text: str, n: int = 2000) -> str:
    return text[-n:]


# ═══════════════════════════════════════════════════════════════
# STEP 1 — SNAPSHOT EXTRACTION
# ═══════════════════════════════════════════════════════════════
SNAPSHOT_PROMPT = """You are extracting tender header information from Indian government tender documents.
Read the text below and extract ONLY what is explicitly stated. Never infer or guess.

Return ONLY valid JSON, no markdown, no explanation.

Text:
{text}

Return JSON:
{{
  "tender_no": "exact tender/NIT/bid number",
  "tender_id": "portal ID if different from tender_no",
  "org_name": "full organization name",
  "dept_name": "department/sub-department",
  "tender_name": "full tender title",
  "portal": "portal URL or name",
  "tender_type": "form of contract (Item Rate / Lumpsum / QCBS / etc)",
  "mode_of_selection": "evaluation method (L1 / QCBS / LCS / etc)",
  "no_of_covers": "number of bid covers/envelopes",
  "bid_start_date": "bid availability/start date",
  "bid_submission_date": "LAST DATE for bid submission with time",
  "bid_opening_date": "technical bid opening date",
  "commercial_opening_date": "financial/commercial bid opening date if different",
  "prebid_meeting": "pre-bid meeting date, time, mode, venue — combine all info",
  "prebid_query_date": "last date for pre-bid queries",
  "estimated_cost": "tender/estimated value",
  "tender_fee": "document/tender fee amount and payment details",
  "emd": "EMD/bid security amount and payment details",
  "emd_exemption": "is EMD exemption available? Yes/No and for whom",
  "performance_security": "performance bank guarantee percentage and conditions",
  "contract_period": "period/duration of work",
  "bid_validity": "bid validity period",
  "post_implementation": "O&M / AMC period after implementation if stated",
  "technology_mandatory": "any mandatory technology or platform",
  "location": "project location / state",
  "contact": "contact officer name, email, phone, address",
  "jv_allowed": "JV/Consortium allowed? exact text from document"
}}

Rules:
- Use "—" for any field not found in text
- For dates: use format as written in document
- For amounts: include currency symbol and words (e.g. Rs. 9,00,000/-)
- Do NOT guess or fill in blanks
- Contact field: combine name + email + phone into one readable string
"""


def step1_snapshot(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    # Use first 8000 chars (NIT section) + last 2000 (often has contact/terms)
    chunk = get_first_n_chars(text, 8000) + "\n\n...\n\n" + get_last_n_chars(text, 2000)
    prompt = SNAPSHOT_PROMPT.format(text=chunk)
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=4096)
        result = clean_json(raw)
        logger.info(f"[Step1-Snapshot] extracted {len(result)} fields")
        return result
    except Exception as e:
        logger.error(f"[Step1-Snapshot] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════
# STEP 2 — CORRIGENDUM DATE OVERRIDE
# ═══════════════════════════════════════════════════════════════
CORRIGENDUM_PROMPT = """Extract date extension information from this corrigendum/amendment text.
Return ONLY valid JSON, no markdown.

Text:
{text}

Return JSON with ONLY the fields that were changed:
{{
  "bid_submission_date": "new bid submission deadline if changed, else null",
  "bid_opening_date": "new bid opening date if changed, else null",
  "prebid_meeting": "new pre-bid meeting details if changed, else null",
  "prebid_query_date": "new pre-bid query deadline if changed, else null",
  "corrigendum_note": "one sentence summary of what changed"
}}
"""


def step2_corrigendums(corrigendum_texts: List[str], api_key: str,
                       all_keys: List[str], groq_key: str) -> Dict:
    """Process all corrigendums and return final overridden dates."""
    if not corrigendum_texts:
        return {}
    combined = "\n\n---\n\n".join(corrigendum_texts)
    prompt = CORRIGENDUM_PROMPT.format(text=combined[:5000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=1024)
        result = clean_json(raw)
        # Remove null values
        return {k: v for k, v in result.items() if v and v != "null"}
    except Exception as e:
        logger.error(f"[Step2-Corrigendum] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════
# STEP 3 — SCOPE EXTRACTION
# ═══════════════════════════════════════════════════════════════
SCOPE_PROMPT = """You are a senior bid analyst reading an Indian government tender.
Extract the complete Scope of Work from the text below.

Text:
{text}

Return ONLY valid JSON, no markdown:
{{
  "scope_background": "2-3 sentences: what problem the client is solving, what they currently have, why this project exists. Write plainly.",
  "scope_items": [
    {{
      "sl_no": "1",
      "title": "Component name (short, e.g. Portal Development, Mobile App, Data Collection)",
      "section_ref": "section or clause reference if stated",
      "description": "Specific description of what must be done — include actual deliverable names, feature names, module names from the document. Be specific not generic.",
      "deliverables": ["Deliverable 1", "Deliverable 2"],
      "tech_platform": "Technology/platform/software if specifically mentioned"
    }}
  ],
  "key_integrations": [
    {{
      "system": "System/platform name",
      "type": "API / Portal / Database / Webhook",
      "purpose": "What this integration does"
    }}
  ],
  "scale_requirements": "Any stated performance/scale requirements (concurrent users, transactions/day, uptime etc)"
}}

Rules:
- Extract EVERY work component mentioned — do not skip any
- Use ACTUAL names from the document (e.g. "SWAGAT", "CPGRAMS", "IGiS", not just "GIS system")
- If scope has phases (Phase A, Phase B), list them separately
- If scope has numbered items (6.1, 6.2), extract each one
- deliverables: actual artifact names (SRS, HLD, UAT sign-off, Source code, User manual etc)
- tech_platform: only fill if explicitly stated in document
"""


def step3_scope(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    # Find scope section
    scope_text = extract_section(text, [
        "Scope of Work\n", "SCOPE OF WORK\n", "Scope of Services\n",
        "Work to be Done", "Phase A", "Phase 1", "Scope:", "6. Scope",
        "3. Scope", "4. Scope", "Work Components", "Deliverables"
    ], chars=8000)
    if not scope_text:
        scope_text = text[5000:15000]  # middle section fallback
    prompt = SCOPE_PROMPT.format(text=scope_text[:10000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=8192)
        result = clean_json(raw)
        logger.info(f"[Step3-Scope] {len(result.get('scope_items',[]))} components extracted")
        return result
    except Exception as e:
        logger.error(f"[Step3-Scope] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════
# STEP 4 — PQ CRITERIA EXTRACTION
# ═══════════════════════════════════════════════════════════════
PQ_PROMPT = """You are a bid analyst extracting Pre-Qualification (PQ) / Eligibility criteria
from an Indian government tender document.

IMPORTANT RULES:
1. Extract EVERY numbered row from the PQ/Eligibility table — do not skip any
2. Copy criteria text WORD-FOR-WORD from the document — do not paraphrase
3. The table has columns like: Sr.No | Parameter | Criteria Description | Supporting Documents
4. Each Sr.No = one item in your output
5. Do NOT include Table of Contents entries (those have page numbers like "....31")
6. The real criteria start with phrases like "The bidder should...", "Bidder must...", "Cover letter..."

Nascent Info Technologies profile for status assessment:
{nascent}

Text containing PQ criteria:
{text}

Return ONLY valid JSON:
{{
  "pq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Section reference and page if stated",
      "criteria": "EXACT word-for-word criteria from document — full text, do not cut short",
      "details": "Supporting documents required as stated",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_color": "GREEN / RED / AMBER",
      "nascent_remark": "What Nascent has that satisfies this (cite specific cert/project/figure). If gap: what is missing. If borderline: explain why Conditional. Keep it concise and factual."
    }}
  ]
}}

Status guide:
- Met (GREEN): Nascent clearly satisfies — cite the specific evidence
- Not Met (RED): Clear gap — state exactly what is missing
- Conditional (AMBER): Meets but with caveat OR borderline OR needs clarification
"""


def step4_pq(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    pq_text = extract_section(text, [
        "Pre-Qualification Criteria\n", "Eligibility Criteria\n",
        "Eligibility Criteria \n", "bidder interested in being considered",
        "fulfill the following minimum eligibility",
        "minimum eligibility criteria", "A Bidder must meet",
        "Qualifying Criteria\n", "4. Eligibility", "5.1 Pre-Qualification",
        "6. Pre-Qualification", "S.no.\nParameter", "Sr.\nNo.\nDescription"
    ], chars=8000)
    if not pq_text:
        # Search in middle of document
        mid = len(text) // 3
        pq_text = text[mid:mid+8000]
    prompt = PQ_PROMPT.format(nascent=NASCENT, text=pq_text[:10000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=8192)
        result = clean_json(raw)
        pq = result.get("pq_criteria", [])
        logger.info(f"[Step4-PQ] {len(pq)} criteria extracted")
        return result
    except Exception as e:
        logger.error(f"[Step4-PQ] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════
# STEP 5 — TQ CRITERIA EXTRACTION
# ═══════════════════════════════════════════════════════════════
TQ_PROMPT = """Extract Technical Qualification (TQ) / Technical Evaluation criteria and scoring
from this Indian government tender document.

Nascent profile:
{nascent}

Text:
{text}

Return ONLY valid JSON:
{{
  "tq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Section / Annexure reference",
      "criteria": "EXACT criteria text from document",
      "details": "Max Marks: X | Nascent Estimated Score: Y-Z",
      "nascent_status": "Met / Conditional / Not Met",
      "nascent_color": "GREEN / AMBER / RED",
      "nascent_remark": "Score justification — cite specific Nascent projects, certs, numbers"
    }}
  ],
  "tq_min_qualifying_score": "minimum score to qualify if stated",
  "tq_total_marks": "total TQ marks if stated",
  "key_personnel": [
    {{
      "role": "role name",
      "qualification": "required qualification",
      "experience": "required experience",
      "nascent_status": "Met / Conditional / Not Met"
    }}
  ]
}}
"""


def step5_tq(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    tq_text = extract_section(text, [
        "Technical Qualification", "Technical Evaluation", "TQ Criteria",
        "Annexure- II", "Annexure II", "Marking Scheme", "Max\nMarks",
        "Max Marks", "Evaluation Criteria", "7. Technical", "10. Technical"
    ], chars=7000)
    if not tq_text:
        return {}
    prompt = TQ_PROMPT.format(nascent=NASCENT, text=tq_text[:9000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=6144)
        result = clean_json(raw)
        tq = result.get("tq_criteria", [])
        logger.info(f"[Step5-TQ] {len(tq)} criteria")
        return result
    except Exception as e:
        logger.error(f"[Step5-TQ] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════
# STEP 6 — PAYMENT TERMS EXTRACTION
# ═══════════════════════════════════════════════════════════════
PAYMENT_PROMPT = """Extract payment schedule and milestone information from this tender document.

Text:
{text}

Return ONLY valid JSON:
{{
  "payment_terms": [
    {{
      "milestone": "Milestone name/number (e.g. M1, Milestone 1, Phase A)",
      "activity": "What triggers this payment",
      "scope": "Key deliverables for this milestone",
      "timeline": "Timeline from contract start (e.g. T0+30 days, Month 3)",
      "payment_percent": "Payment percentage or amount (e.g. 30%, Rs. 5 Lakh)"
    }}
  ],
  "penalty_clauses": [
    {{
      "type": "LD / SLA Penalty / Blacklisting / etc",
      "condition": "What triggers it",
      "penalty": "Amount or percentage",
      "max_cap": "Maximum cap if stated",
      "clause_ref": "Clause reference"
    }}
  ],
  "key_conditions": [
    {{
      "term": "Condition name",
      "details": "Full details of the condition"
    }}
  ]
}}

Rules:
- Extract EVERY milestone row from the payment table
- Include O&M/AMC payment terms separately
- For penalty: include LD, SLA penalties, blacklisting clauses
- For key_conditions: include PBG terms, retention, IP rights, exit terms
"""


def step6_payment(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    pay_text = extract_section(text, [
        "Payment Terms", "Payment Schedule", "PAYMENT TO THE AGENCY",
        "Milestone Payment", "10. Payment", "11. Payment", "Schedule of Payment",
        "Deliverables and Timeline", "Payment Terms, Deliverables"
    ], chars=6000)
    if not pay_text:
        return {}
    # Also get penalty/conditions
    penalty_text = extract_section(text, [
        "Liquidated Damages", "Penalty", "Performance Bank Guarantee",
        "Retention", "SLA", "Service Level"
    ], chars=3000)
    combined = pay_text + "\n\n" + penalty_text
    prompt = PAYMENT_PROMPT.format(text=combined[:9000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=6144)
        result = clean_json(raw)
        logger.info(f"[Step6-Payment] {len(result.get('payment_terms',[]))} milestones")
        return result
    except Exception as e:
        logger.error(f"[Step6-Payment] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════
# STEP 7 — NASCENT ASSESSMENT + PRE-BID QUERIES + VERDICT
# ═══════════════════════════════════════════════════════════════
ASSESSMENT_PROMPT = """You are a senior bid manager at Nascent Info Technologies making a Bid/No-Bid decision.

Nascent profile:
{nascent}

Tender: {tender_name}
Organization: {org_name}
Estimated Value: {estimated_cost}
Mode of Selection: {mode_of_selection}
Contract Period: {contract_period}

PQ Criteria assessed:
{pq_summary}

TQ Criteria assessed:
{tq_summary}

Scope summary:
{scope_summary}

Your job:
1. Review the PQ/TQ assessment and make the final verdict
2. Draft pre-bid queries ONLY for real gaps that affect Nascent's eligibility
3. Generate action items with specific dates based on bid deadline: {bid_deadline}

Return ONLY valid JSON:
{{
  "overall_recommendation": "BID or NO-BID or CONDITIONAL",
  "recommendation_reason": "3-5 specific reasons — cite actual criteria, Nascent strengths/gaps",
  "key_reasons": [
    "Reason 1 with specific evidence",
    "Reason 2 with specific evidence"
  ],
  "prebid_queries": [
    {{
      "clause": "Exact clause reference",
      "page_no": "Page number if known",
      "rfp_text": "The exact clause text being queried (verbatim from tender)",
      "query": "Professional, specific query. Cite guidelines where applicable. Explain Nascent's position.",
      "desired_clarification": "What written response we need"
    }}
  ],
  "action_items": [
    {{
      "action": "Specific action",
      "responsible": "Bid Team / Finance / HR / etc",
      "target_date": "Specific date based on bid deadline",
      "priority": "URGENT / HIGH / MEDIUM"
    }}
  ],
  "jv_conditions": [],
  "portal_vs_rfp_discrepancies": []
}}

Rules for pre-bid queries:
- ONLY raise queries for genuine gaps or ambiguities that affect eligibility
- DO NOT query things that are clear in the document
- Maximum 5 focused queries
- Each query must explain Nascent's specific position and what clarification is needed
- Draft as professional business correspondence

Rules for verdict:
- BID: Nascent meets all PQ, good TQ score expected, strong domain match
- NO-BID: Hard disqualifier exists (e.g. mandatory cert not held, turnover far below threshold)
- CONDITIONAL: Meets most criteria but 1-2 gaps addressable via pre-bid query or JV

Rules for action items:
- Base all dates on this bid deadline: {bid_deadline}
- Work backwards: pre-bid queries first, then document prep, then submission
- Be specific (not "prepare documents" — say WHICH documents)
"""


def step7_assessment(snapshot: Dict, pq: Dict, tq: Dict, scope: Dict,
                     api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    # Build summaries for the prompt
    pq_items = pq.get("pq_criteria", [])
    pq_summary = "\n".join([
        f"- Sr.{item.get('sl_no','?')}: {item.get('criteria','')[:100]} → {item.get('nascent_status','?')}: {item.get('nascent_remark','')[:100]}"
        for item in pq_items
    ]) or "No PQ criteria extracted"

    tq_items = tq.get("tq_criteria", [])
    tq_summary = "\n".join([
        f"- {item.get('criteria','')[:80]} → {item.get('details','')[:60]} → {item.get('nascent_remark','')[:80]}"
        for item in tq_items
    ]) or "No TQ criteria extracted"

    scope_items = scope.get("scope_items", [])
    scope_summary = scope.get("scope_background","") + "\n" + "\n".join([
        f"- {item.get('title','')}: {item.get('description','')[:100]}"
        for item in scope_items[:5]
    ]) or "Scope not extracted"

    prompt = ASSESSMENT_PROMPT.format(
        nascent=NASCENT,
        tender_name=snapshot.get("tender_name","Unknown tender"),
        org_name=snapshot.get("org_name","Unknown org"),
        estimated_cost=snapshot.get("estimated_cost","Not stated"),
        mode_of_selection=snapshot.get("mode_of_selection","—"),
        contract_period=snapshot.get("contract_period","—"),
        bid_deadline=snapshot.get("bid_submission_date","—"),
        pq_summary=pq_summary,
        tq_summary=tq_summary,
        scope_summary=scope_summary,
    )
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=8192)
        result = clean_json(raw)
        logger.info(f"[Step7-Assessment] verdict={result.get('overall_recommendation')}")
        return result
    except Exception as e:
        logger.error(f"[Step7-Assessment] failed: {e}")
        return {"overall_recommendation": "CONDITIONAL", "key_reasons": [str(e)]}


# ═══════════════════════════════════════════════════════════════
# STEP 8 — NOTES + CHECKLIST
# ═══════════════════════════════════════════════════════════════
NOTES_PROMPT = """Based on this tender information, generate:
1. Key observations for the bid team
2. A complete submission checklist

Tender details:
{tender_summary}

PQ criteria list:
{pq_list}

Return ONLY valid JSON:
{{
  "notes": [
    {{
      "title": "Short title (e.g. Selection Method, Physical Submission, MSME Exemption)",
      "detail": "Specific detail relevant to Nascent's bid strategy"
    }}
  ],
  "submission_checklist": [
    {{
      "document": "Document name",
      "annexure": "Annexure reference or —",
      "status": "Prepare / Compile / Ready / CRITICAL — verify"
    }}
  ]
}}

For notes: cover selection method, physical submission requirements, any unusual clauses,
MSME exemption status, reverse auction if applicable, JV restrictions, key risks.

For checklist: include ALL documents from PQ list + standard bid documents
(Cover letter, EMD, PoA/BR, Incorporation cert, PAN/GST, Balance sheets,
CA turnover cert, Work orders, Completion certs, HR declaration, CVs, etc.)
"""


def step8_notes_checklist(snapshot: Dict, pq: Dict,
                          api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    tender_summary = "\n".join([
        f"{k}: {v}" for k, v in snapshot.items()
        if v and v != "—" and k in [
            "org_name","tender_name","mode_of_selection","emd","emd_exemption",
            "contract_period","jv_allowed","bid_submission_date","performance_security"
        ]
    ])
    pq_list = "\n".join([
        f"- Sr.{item.get('sl_no','?')}: {item.get('criteria','')[:80]} — docs: {item.get('details','')[:60]}"
        for item in pq.get("pq_criteria", [])
    ])
    prompt = NOTES_PROMPT.format(
        tender_summary=tender_summary,
        pq_list=pq_list or "See tender document"
    )
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=4096)
        result = clean_json(raw)
        logger.info(f"[Step8-Notes] {len(result.get('notes',[]))} notes, {len(result.get('submission_checklist',[]))} checklist items")
        return result
    except Exception as e:
        logger.error(f"[Step8-Notes] failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════
# NORMALIZE HELPERS
# ═══════════════════════════════════════════════════════════════
def normalize_verdict(rec: str):
    rec = rec.upper().strip()
    if any(x in rec for x in ["NO-BID","NO_BID","NOBID","NOT BID","REJECT"]):
        return "NO-BID", "RED"
    if any(x in rec for x in ["CONDITIONAL","CONDITION","AMBER","CAUTION"]):
        return "CONDITIONAL", "AMBER"
    if any(x in rec for x in ["BID","YES","PROCEED","GO"]):
        return "BID", "GREEN"
    return "CONDITIONAL", "AMBER"


def normalize_status(s: str):
    s = str(s).upper()
    if "NOT MET" in s or "CRITICAL" in s or "NO" == s.strip():
        return "Not Met", "RED"
    if "CONDITIONAL" in s or "AMBER" in s or "PARTIAL" in s:
        return "Conditional", "AMBER"
    if "MET" in s or "YES" == s.strip() or "GREEN" in s:
        return "Met", "GREEN"
    return "Review", "BLUE"


def prebid_passed(date_str: str) -> bool:
    """Check if pre-bid query deadline has passed."""
    import re as _re
    from datetime import datetime, date
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


# ═══════════════════════════════════════════════════════════════
# FILE TEXT PROCESSING
# ═══════════════════════════════════════════════════════════════
def build_text_corpus(all_text: str) -> tuple:
    """
    Split combined text into: main_text, corrigendum_texts.
    Returns (main_text, [corr1, corr2, ...])
    """
    # Find all file sections
    pattern = r'=== FILE: ([^=\n]+) ===\n'
    markers = list(re.finditer(pattern, all_text))

    if not markers:
        # No file markers — treat as single main document
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

        # Detect corrigendum by filename or content
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

    main_text = "\n\n".join(main_parts)
    return main_text, corrigendum_parts


# ═══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════
def analyze_with_gemini(full_text: str, prebid_passed_flag: bool = False) -> Dict[str, Any]:
    """
    Multi-call pipeline. Each step extracts one thing well.
    Returns merged result dict ready for doc_generator.
    """
    all_keys = get_all_api_keys()
    if not all_keys:
        return {"error": "No Gemini API key configured. Go to Settings and add key from aistudio.google.com/apikey"}

    cfg = load_config()
    groq_key = cfg.get("groq_api_key", cfg.get("groq_key", ""))
    api_key = all_keys[0]

    print(f"[AI Pipeline] Starting — {len(full_text)} chars, {len(all_keys)} API keys")

    # Split main text from corrigendums
    main_text, corrigendum_texts = build_text_corpus(full_text)
    print(f"[AI Pipeline] Main: {len(main_text)} chars | Corrigendums: {len(corrigendum_texts)}")

    # Deduplicate if same file repeated (common in GeM ZIPs)
    if len(main_text) > 50000:
        quarter = len(main_text) // 4
        if main_text[:500] == main_text[quarter:quarter+500]:
            main_text = main_text[:quarter]
            print(f"[AI Pipeline] Deduplicated repeated content — using {len(main_text)} chars")

    # Run each step
    result = {}

    print("[AI Pipeline] Step 1: Snapshot...")
    snapshot = step1_snapshot(main_text, api_key, all_keys, groq_key)
    result.update(snapshot)

    print("[AI Pipeline] Step 2: Corrigendums...")
    if corrigendum_texts:
        corr_overrides = step2_corrigendums(corrigendum_texts, api_key, all_keys, groq_key)
        if corr_overrides:
            # Override snapshot dates with latest corrigendum dates
            for field in ["bid_submission_date","bid_opening_date","prebid_meeting","prebid_query_date"]:
                if corr_overrides.get(field) and corr_overrides[field] != "null":
                    old = result.get(field,"—")
                    result[field] = corr_overrides[field]
                    print(f"[AI Pipeline] Date override: {field}: {old} → {corr_overrides[field]}")
            if corr_overrides.get("corrigendum_note"):
                result["corrigendum_note"] = corr_overrides["corrigendum_note"]
            result["has_corrigendum"] = True

    print("[AI Pipeline] Step 3: Scope...")
    scope = step3_scope(main_text, api_key, all_keys, groq_key)
    if scope.get("scope_background"):
        result["scope_background"] = scope["scope_background"]
    if scope.get("scope_items"):
        result["scope_items"] = scope["scope_items"]
    if scope.get("key_integrations"):
        result["key_integrations"] = scope["key_integrations"]
    if scope.get("scale_requirements"):
        result["scale_requirements"] = scope["scale_requirements"]

    print("[AI Pipeline] Step 4: PQ Criteria...")
    pq = step4_pq(main_text, api_key, all_keys, groq_key)
    if pq.get("pq_criteria"):
        # Normalize statuses
        normalized = []
        for item in pq["pq_criteria"]:
            if not isinstance(item, dict):
                continue
            status, color = normalize_status(item.get("nascent_status","Review"))
            normalized.append({
                "sl_no": item.get("sl_no",""),
                "clause_ref": item.get("clause_ref","—"),
                "criteria": item.get("criteria",""),
                "details": item.get("details",""),
                "nascent_status": status,
                "nascent_color": color,
                "nascent_remark": item.get("nascent_remark",""),
            })
        result["pq_criteria"] = normalized

    print("[AI Pipeline] Step 5: TQ Criteria...")
    tq = step5_tq(main_text, api_key, all_keys, groq_key)
    if tq.get("tq_criteria"):
        normalized_tq = []
        for item in tq["tq_criteria"]:
            if not isinstance(item, dict):
                continue
            status, color = normalize_status(item.get("nascent_status","Review"))
            normalized_tq.append({
                "sl_no": item.get("sl_no",""),
                "clause_ref": item.get("clause_ref","—"),
                "criteria": item.get("criteria",""),
                "details": item.get("details",""),
                "nascent_status": status,
                "nascent_color": color,
                "nascent_remark": item.get("nascent_remark",""),
            })
        result["tq_criteria"] = normalized_tq

    print("[AI Pipeline] Step 6: Payment Terms...")
    payment = step6_payment(main_text, api_key, all_keys, groq_key)
    if payment.get("payment_terms"):
        result["payment_terms"] = payment["payment_terms"]
    if payment.get("penalty_clauses"):
        result["penalty_clauses"] = payment["penalty_clauses"]
    if payment.get("key_conditions"):
        result["key_conditions"] = payment["key_conditions"]

    print("[AI Pipeline] Step 7: Nascent Assessment + Verdict...")
    assessment = step7_assessment(
        result, pq, tq, scope, api_key, all_keys, groq_key
    )
    if assessment.get("prebid_queries"):
        result["prebid_queries"] = assessment["prebid_queries"]
    if assessment.get("action_items"):
        result["action_items"] = assessment["action_items"]
    if assessment.get("jv_conditions"):
        result["jv_conditions"] = assessment["jv_conditions"]
    if assessment.get("portal_vs_rfp_discrepancies"):
        result["portal_vs_rfp_discrepancies"] = assessment["portal_vs_rfp_discrepancies"]
    if assessment.get("key_reasons"):
        result["key_reasons"] = assessment["key_reasons"]

    # Build overall_verdict
    rec = assessment.get("overall_recommendation", "CONDITIONAL")
    reason = assessment.get("recommendation_reason", "")
    verdict, color = normalize_verdict(rec)
    result["verdict"] = verdict
    pq_list = result.get("pq_criteria", [])
    result["overall_verdict"] = {
        "verdict": verdict,
        "reason": reason,
        "color": color,
        "green": sum(1 for p in pq_list if p.get("nascent_color") == "GREEN"),
        "amber": sum(1 for p in pq_list if p.get("nascent_color") == "AMBER"),
        "red":   sum(1 for p in pq_list if p.get("nascent_color") == "RED"),
    }

    print("[AI Pipeline] Step 8: Notes + Checklist...")
    notes = step8_notes_checklist(result, pq, api_key, all_keys, groq_key)
    if notes.get("notes"):
        result["notes"] = notes["notes"]
    if notes.get("submission_checklist"):
        result["submission_checklist"] = notes["submission_checklist"]

    print(f"[AI Pipeline] Complete — verdict={verdict}, PQ={len(pq_list)} criteria")
    return result


# ═══════════════════════════════════════════════════════════════
# MERGE — kept for backward compatibility with main.py
# ═══════════════════════════════════════════════════════════════
def merge_results(regex_data: Dict, ai_data: Dict, prebid_passed: bool = False) -> Dict:
    """
    New pipeline returns complete data — just merge with any regex fields
    that AI didn't override.
    """
    if not ai_data or "error" in ai_data:
        return regex_data
    result = dict(regex_data)
    # AI data wins on everything it found
    EMPTY = {"—", "Not mentioned", "Not specified", "", "As per tender", None}
    for key, val in ai_data.items():
        if val and str(val).strip() not in EMPTY:
            result[key] = val
    return result
