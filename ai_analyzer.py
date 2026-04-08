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
Read ALL the text below carefully — key fields like EMD, Tender Fee, Bid Validity, and JV rules
are often buried in the body sections (Notice Inviting Bid, Key Events, Instructions, Terms & Conditions),
NOT just in the header.

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
  "portal": "portal URL where bids must be submitted (e.g. smctender.nprocure.com)",
  "tender_type": "form of contract (Item Rate / Lumpsum / QCBS / etc)",
  "mode_of_selection": "evaluation method (L1 / QCBS / LCS / etc)",
  "no_of_covers": "number of bid covers/envelopes",
  "bid_start_date": "bid availability/start date",
  "bid_submission_date": "LAST DATE for bid submission with time",
  "bid_opening_date": "technical bid opening date",
  "commercial_opening_date": "financial/commercial bid opening date if different",
  "prebid_meeting": "pre-bid meeting — date, time, mode (email/physical/online), email address if given",
  "prebid_query_date": "last date for pre-bid queries with time",
  "estimated_cost": "tender/estimated value — search for 'estimated cost', 'NIT value', 'project cost'",
  "tender_fee": "tender/bid document fee — search for 'Bid Fee', 'Tender Fee', 'Document Fee' — include amount + GST + payment mode (DD/online)",
  "emd": "EMD/bid security — search for 'EMD', 'Earnest Money', 'Bid Security' — include full amount in words and figures + payment mode",
  "emd_exemption": "EMD exemption — is it available? For MSME/Startups? Exact clause text",
  "performance_security": "Performance Security / Initial Security Deposit — percentage of contract value + conditions",
  "contract_period": "contract duration — include Phase A (development) + Phase B (O&M/AMC) separately if stated",
  "bid_validity": "bid validity period — search for 'validity', 'valid for acceptance' — state in days",
  "post_implementation": "O&M / AMC / CAMC period after go-live if stated",
  "technology_mandatory": "any mandatory technology, platform, or software stack",
  "location": "project location / city / state",
  "contact": "contact officer name + email + phone + address — search entire document",
  "jv_allowed": "JV/Consortium/Sub-contracting — is it allowed or NOT allowed? Quote the exact clause text"
}}

Rules:
- Use "—" ONLY if a field is truly not found anywhere in the text after careful reading
- For amounts: include Rs. symbol, figures, and words as written (e.g. Rs. 4,248/- (Rs. 3,600 + 18% GST))
- For portal: look for the submission URL, not just the organization website
- For JV: look in Terms & Conditions section — it often explicitly says 'NOT allowed'
- For Bid Validity: look for '120 days', '180 days from opening of price bid' etc.
- Contact field: search entire document including footer, Notice Inviting Bid section
"""


def step1_snapshot(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    # Use first 12000 chars (covers NIT + Key Events + Instructions) + last 3000 (T&C)
    chunk = get_first_n_chars(text, 12000) + "\n\n...\n\n" + get_last_n_chars(text, 3000)
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

The PQ criteria appear as a table with columns like:
  S.N. | Basic Requirement | Specific Requirements | Documents Required
OR
  Sr.No | Criteria / Parameter | Proof/Documents Required
OR as a numbered list of conditions.

════════════════════════════════════════════════════════
RULE 1 — COPY WORD-FOR-WORD. NO PARAPHRASING.
The "criteria" field MUST be the EXACT TEXT from the document.
Do not summarise, shorten, rephrase, or rewrite a single word.
Copy every sentence, every condition, every threshold exactly as written.
If the document says "The bidder should have been in operation for a period
of at least 10 years in India prior to the date of submission of bid" —
that exact sentence goes into "criteria". Not "10 years experience". Not
"company age requirement". The EXACT text.
════════════════════════════════════════════════════════

RULE 2 — COPY DOCUMENTS REQUIRED WORD-FOR-WORD.
The "details" field = the Documents Required column, copied exactly.
Every document name, every annexure reference, every sub-point — exact.

RULE 3 — EXTRACT EVERY ROW.
Every Sr.No or numbered criterion = one item in the array.
Do not skip any. Do not merge rows.

RULE 4 — COLUMN NAMES.
If the table has a "Basic Requirement" column (short label like "Legal Entity",
"Annual Turnover") put that in "clause_ref". Put the full Specific Requirements
text in "criteria".

RULE 5 — IGNORE TABLE OF CONTENTS.
Skip rows that end with dots and page numbers (....7, ....31).

Nascent Info Technologies profile — use ONLY for nascent_status assessment:
{nascent}

KEY RULE FOR STATUS — Nascent is a pure IT/ITeS company:
- If tender asks for IT/ITeS turnover and Nascent's turnover meets the number → Met (GREEN)
  Nascent's ENTIRE turnover is IT/ITeS. Do NOT mark as Conditional if the number qualifies.
- If tender asks for IT/ITeS employees and Nascent has 67 employees → Met if 67 >= required
  All Nascent employees are IT/ITeS. Do NOT say Conditional for numbers that qualify.
- Only mark Conditional for: EMD exemption query, local office, CERT-In, STQC,
  or where a genuine caveat EXISTS regardless of the numbers.
- Only mark Not Met for hard disqualifiers: expired cert, number genuinely below threshold.

Text containing PQ/Eligibility criteria:
{text}

Return ONLY valid JSON — no markdown, no explanation before or after:
{{
  "pq_criteria": [
    {{
      "sl_no": "i",
      "clause_ref": "Basic Requirement label OR clause/section reference (e.g. 'Legal Entity', 'Cl. 5.1-i')",
      "criteria": "EXACT WORD-FOR-WORD text of the Specific Requirements column. Every word. Every sentence. Every sub-point. No truncation.",
      "details": "EXACT WORD-FOR-WORD text of the Documents Required column. Every document. Every annexure. No truncation.",
      "nascent_status": "Met / Not Met / Conditional",
      "nascent_color": "GREEN / RED / AMBER",
      "nascent_remark": "Specific evidence from Nascent profile: cite cert name + validity, project name + value, exact turnover figure, employee count. If Not Met: state exactly what is missing. If Conditional: state exactly what pre-bid query is needed."
    }}
  ]
}}

IMPORTANT: If no PQ/Eligibility section is found in the text, return {{"pq_criteria": []}}
"""


def step4_pq(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    pq_text = extract_section(text, [
        "Pre-Qualification Criteria\n", "Eligibility Criteria\n",
        "Eligibility Criteria \n", "bidder interested in being considered",
        "fulfill the following minimum eligibility",
        "minimum eligibility criteria", "A Bidder must meet",
        "Qualifying Criteria\n", "4. Eligibility", "5.1 Pre-Qualification",
        "6. Pre-Qualification", "S.no.\nParameter", "Sr.\nNo.\nDescription",
        "Sr.\nNo.\nDescription", "4. Eligibility Criteria",
        "ELIGIBILITY CRITERIA", "Eligibility:", "Minimum Eligibility",
    ], chars=10000)
    if not pq_text:
        # Search in first half of document (PQ usually in first 60%)
        pq_text = text[3000:13000]
    prompt = PQ_PROMPT.format(nascent=NASCENT, text=pq_text[:12000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=10240)
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
TQ_PROMPT = """You are a bid analyst extracting Technical Qualification (TQ) / Technical Evaluation
criteria from an Indian government tender document.

The TQ table typically has columns:
  S. No. | Criteria | Evaluation Criteria | Documents Required | Maximum Marks

════════════════════════════════════════════════════════
RULE 1 — COPY WORD-FOR-WORD. NO PARAPHRASING.
"criteria" field = EXACT text of the Criteria column, word for word.
"eval_criteria" field = EXACT text of the Evaluation Criteria column, word for word.
  This includes every slab (e.g. "INR = 7 Cr: 4 Marks, INR 7 Cr and <= INR 10 Cr: 6 Marks").
  Copy every slab. Every threshold. Every mark allocation. Exactly.
"documents_required" field = EXACT text of the Documents Required column, word for word.
Do NOT summarise, shorten, or rephrase anything.
════════════════════════════════════════════════════════

RULE 2 — EXTRACT EVERY ROW.
Every numbered criterion = one item. Do not skip or merge rows.

RULE 3 — SCORE ESTIMATION.
After copying RFP text exactly, then estimate Nascent's score based on the profile below.
Nascent profile is ONLY used for score estimation and remarks. Never for changing RFP text.

Nascent Info Technologies profile:
{nascent}

KEY SCORING RULE — Nascent is a pure IT/ITeS company:
- Turnover slabs: use Nascent's actual figures (FY23=16.36Cr, FY24=16.36Cr, FY25=18.83Cr)
  to determine which slab applies. All turnover = IT/ITeS turnover.
- Employee slabs: all 67 employees count as IT/ITeS employees.
- CMMI L3 = 5 marks if asked. ISO 9001=1, ISO 27001=2, ISO 20000=2 marks if asked.
- Project experience: use actual project values from profile.

Text containing TQ/Technical Evaluation criteria:
{text}

Return ONLY valid JSON — no markdown:
{{
  "tq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "Section / Annexure / Clause reference",
      "criteria": "EXACT WORD-FOR-WORD text from the Criteria column of the TQ table.",
      "eval_criteria": "EXACT WORD-FOR-WORD text from the Evaluation Criteria column — every slab, every threshold, every mark allocation, exactly as written.",
      "documents_required": "EXACT WORD-FOR-WORD text from the Documents Required column.",
      "max_marks": "Maximum marks for this criterion as stated in RFP",
      "nascent_score": "Nascent's estimated score for this criterion (number only)",
      "nascent_status": "Met / Conditional / Not Met",
      "nascent_color": "GREEN / AMBER / RED",
      "nascent_remark": "Which slab Nascent falls into and why — cite specific figures, cert names, project values. State exact score justification."
    }}
  ],
  "tq_min_qualifying_score": "minimum qualifying score if stated",
  "tq_total_marks": "total marks if stated",
  "tq_nascent_estimated_total": "sum of all Nascent estimated scores",
  "key_personnel": [
    {{
      "role": "role name as stated in RFP",
      "qualification": "exact qualification requirement",
      "experience": "exact experience requirement",
      "max_marks": "marks allocated",
      "nascent_status": "Met / Conditional / Not Met",
      "nascent_remark": "who in Nascent team qualifies"
    }}
  ]
}}
"""


def step5_tq(text: str, api_key: str, all_keys: List[str], groq_key: str) -> Dict:
    tq_text = extract_section(text, [
        "Technical Qualification", "Technical Evaluation", "TQ Criteria",
        "Annexure- II", "Annexure II", "Marking Scheme", "Max\nMarks",
        "Max Marks", "5.2 Technical", "Evaluation Criteria", "7. Technical", "10. Technical",
        "S. No.\nCriteria", "S.No.\nCriteria",
    ], chars=8000)
    if not tq_text:
        return {}
    prompt = TQ_PROMPT.format(nascent=NASCENT, text=tq_text[:10000])
    try:
        raw = _call(prompt, api_key, all_keys, groq_key, max_tokens=8192)
        result = clean_json(raw)
        # Normalise TQ items — merge eval_criteria + documents_required into details field
        # so doc_generator can render them properly
        tq_list = result.get("tq_criteria", [])
        for item in tq_list:
            if not isinstance(item, dict):
                continue
            # Build details field from eval_criteria + max_marks if present
            eval_cr = item.get("eval_criteria", "") or item.get("details", "")
            docs_req = item.get("documents_required", "")
            max_marks = item.get("max_marks", "")
            parts = []
            if max_marks:
                parts.append(f"Max Marks: {max_marks}")
            if eval_cr:
                parts.append(eval_cr)
            if docs_req:
                parts.append(f"Documents: {docs_req}")
            item["details"] = " | ".join(parts) if parts else ""
            # Preserve eval_criteria and documents_required as separate fields too
        logger.info(f"[Step5-TQ] {len(tq_list)} criteria")
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
        f"- Sr.{item.get('sl_no','?')}: {str(item.get('criteria','') or '')[:100]} → {item.get('nascent_status','?')}: {str(item.get('nascent_remark','') or '')[:100]}"
        for item in pq_items if isinstance(item, dict)
    ]) or "No PQ criteria extracted"

    tq_items = tq.get("tq_criteria", [])
    tq_summary = "\n".join([
        f"- {str(item.get('criteria','') or '')[:80]} → {str(item.get('details','') or '')[:60]} → {str(item.get('nascent_remark','') or '')[:80]}"
        for item in tq_items if isinstance(item, dict)
    ]) or "No TQ criteria extracted"

    scope_items = scope.get("scope_items", [])
    scope_summary = str(scope.get("scope_background","") or "") + "\n" + "\n".join([
        f"- {str(item.get('title','') or '')}: {str(item.get('description','') or '')[:100]}"
        for item in scope_items[:5] if isinstance(item, dict)
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
        f"- Sr.{item.get('sl_no','?')}: {str(item.get('criteria','') or '')[:80]} — docs: {str(item.get('details','') or '')[:60]}"
        for item in pq.get("pq_criteria", [])
        if isinstance(item, dict)
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


def regex_extract_snapshot(text: str) -> Dict:
    """
    Fast regex extraction of key fields from Indian tender documents.
    Used as fallback when all AI API calls fail.
    Handles common patterns in SMC/GeM/NIT tender formats.
    """
    result = {}

    def first_match(patterns, txt=text):
        for p in patterns:
            m = re.search(p, txt, re.IGNORECASE | re.MULTILINE)
            if m:
                val = m.group(1).strip().rstrip('/-')
                if val and len(val) < 200:
                    return val
        return "—"

    # Tender No — try specific patterns first
    result["tender_no"] = "—"
    # DC/GIS CELL style
    m = re.search(r'(DC/[A-Z ]+/\d+/\d{4}-\d{2,4})', text)
    if m: result["tender_no"] = m.group(1).strip()
    # GeM style
    if result["tender_no"] == "—":
        m = re.search(r'(GEM/\d{4}/[A-Z]/\d+)', text)
        if m: result["tender_no"] = m.group(1)
    # Generic NIT/Tender No
    if result["tender_no"] == "—":
        result["tender_no"] = first_match([
            r'(?:Notice Inviting (?:Tender|Bid)|NIT No\.?|Tender No\.?|Bid No\.?|RFP No\.?)[:\s]+([A-Z0-9/\-\. ]+)',
            r'Tender Reference Number\s+([A-Z0-9/ ]+dated[^\n]+)',
        ])

    # Org name
    result["org_name"] = first_match([
        r'(?:Organisation Name|Organization)[:\s]+([^\n]+)',
        r'(?:Issued by|Inviting Authority)[:\s]+([^\n]+)',
        r'^((?:Surat|Ahmedabad|Mumbai|Delhi|Gujarat|Rajasthan)[^\n]{5,50}(?:Corporation|Department|Office|Council|Authority))',
    ])

    # Tender name / title
    result["tender_name"] = first_match([
        r'(?:Title|Work Description|Item Category)[:\s]+([^\n]{20,})',
        r'Request for Proposal[^\n]*\n([^\n]{20,})',
        r'(?:NIB|NIT) for ([^\n]{20,})',
    ])

    # Bid submission deadline
    result["bid_submission_date"] = first_match([
        r'(?:Bid End Date|Last Date.*Submission|Online Bid End Date)[:/\s]+([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}[^\n]{0,20})',
        r'(?:last date.*online submission|bid end date/time)[:\s]+([0-9-]{8,}[^\n]{0,15})',
        r'on or before\s+([0-9]{2}/[0-9]{2}/[0-9]{4}[^\n]{0,15})',
    ])

    # Bid opening date
    result["bid_opening_date"] = first_match([
        r'(?:Bid Opening Date|Opening of Technical Bids?)[:/\s]+([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}[^\n]{0,20})',
        r'Tentatively on ([0-9]{2}/[0-9]{2}/[0-9]{4})',
    ])

    # Bid start date
    result["bid_start_date"] = first_match([
        r'(?:Bid (?:Start|Availability|Start Date|Available from))[:/\s]+([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})',
        r'Online Bid Start Date\s+([0-9]{2}/[0-9]{2}/[0-9]{4})',
        r'Start from ([0-9]{2}/[0-9]{2}/[0-9]{4})',
    ])

    # EMD
    result["emd"] = first_match([
        r'EMD[^\n]*?(?:Rs\.?|INR|₹)\s*([\d,]+(?:/[-])?)',
        r'Earnest Money Deposit[^\n]*?(?:Rs\.?|INR|₹)\s*([\d,]+)',
        r'EMD Amount[^\n]*?([\d,]+)',
        r'(?:Rs\.?\s*[\d,]+)[^\n]*(?:EMD|Earnest)',
    ])
    if result["emd"] != "—":
        result["emd"] = "Rs. " + result["emd"].replace("Rs.", "").replace("Rs", "").strip()

    # Tender fee
    result["tender_fee"] = first_match([
        r'(?:Tender Fee|Bid Fee|Document Fee)[^\n]*?(?:Rs\.?|INR|₹)\s*([\d,]+[^\n]{0,50})',
        r'(?:Rs\.?\s*[\d,]+)[^\n]*(?:Tender Fee|Bid Fee)',
    ])

    # Pre-bid query deadline
    result["prebid_query_date"] = first_match([
        r'(?:Pre-Bid Quer(?:y|ies)|Last Date.*Quer)[^\n]*?([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}[^\n]{0,20})',
        r'(?:post queries|submit.*queries)[^\n]*?([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}[^\n]{0,20})',
        r'on or before\s+([0-9]{2}/[0-9]{2}/[0-9]{4}[^\n]{0,20}hrs)',
    ])

    # Pre-bid meeting
    result["prebid_meeting"] = first_match([
        r'Pre-Bid Meeting[^\n]*?([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}[^\n]{0,80})',
        r'Pre-Bid Conference[^\n]*?([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}[^\n]{0,80})',
    ])

    # Contact email
    email_m = re.search(r'([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', text)
    if email_m:
        result["contact"] = email_m.group(1)

    # Portal
    url_m = re.search(r'(https?://[a-zA-Z0-9./\-]+(?:tender|gem|eproc|nprocure|bid)[a-zA-Z0-9./\-]*)', text)
    if url_m:
        result["portal"] = url_m.group(1)

    # Contract period
    result["contract_period"] = first_match([
        r'(?:Period of Work|Contract Period|Duration)[:/\s]+([^\n]{5,60})',
        r'(\d+\s+(?:Year|Month)[s]?[^\n]{0,40}(?:O&M|AMC|maintenance)?)',
    ])

    # Bid validity
    result["bid_validity"] = first_match([
        r'(?:Bid Validity|Offer Valid)[:/\s]+([^\n]{5,40})',
        r'valid.*?(\d+\s*days?)[^\n]{0,20}(?:opening|price)',
    ])

    # JV
    if re.search(r'(?:JV|Joint Venture|Consortium)[^\n]*NOT\s+(?:allowed|permitted)', text, re.IGNORECASE):
        result["jv_allowed"] = "NOT allowed — JV/Consortium not permitted"
    elif re.search(r'NOT\s+(?:allowed|permitted)[^\n]*(?:JV|Joint Venture|Consortium)', text, re.IGNORECASE):
        result["jv_allowed"] = "NOT allowed — JV/Consortium not permitted"

    # EMD exemption
    if re.search(r'MSME.*exemption|exemption.*MSME', text, re.IGNORECASE):
        result["emd_exemption"] = "MSME exemption may be applicable — verify with authority"

    # Performance security
    result["performance_security"] = first_match([
        r'(?:Performance (?:Bank Guarantee|Security|Guarantee))[^\n]*?(\d+%[^\n]{0,60})',
        r'(?:Initial Security Deposit|ISD)[^\n]*?(\d+%[^\n]{0,60})',
        r'Security Deposit[^\n]*?(?:@\s*)?(\d+%[^\n]{0,40})',
    ])

    # Estimated cost
    result["estimated_cost"] = first_match([
        r'(?:Estimated (?:Bid )?Value|Tender Value)[:/\s]+(?:Rs\.?|INR|₹)?\s*([\d,]+)',
        r'(?:Estimated Cost)[:/\s]+([^\n]{5,40})',
    ])

    # Location
    result["location"] = first_match([
        r'(?:Location|Project Location)[:/\s]+([^\n]{5,50})',
        r'Pincode[^\n]*?([A-Za-z ,]+)\n',
    ])

    # Mode of selection
    if re.search(r'L1\b|lowest bid|L-1', text, re.IGNORECASE):
        result["mode_of_selection"] = "L1 (Lowest Bid)"
    elif re.search(r'QCBS', text, re.IGNORECASE):
        result["mode_of_selection"] = "QCBS (Quality cum Cost Based Selection)"
    elif re.search(r'LCS|Least Cost', text, re.IGNORECASE):
        result["mode_of_selection"] = "LCS (Least Cost Selection)"

    # Remove "—" values to keep result clean
    return {k: v for k, v in result.items() if v and v != "—"}


# ═══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════
def analyze_with_gemini(full_text: str, prebid_passed_flag: bool = False) -> Dict[str, Any]:
    """
    Multi-call pipeline. Each step extracts one thing well.
    Returns merged result dict ready for doc_generator.
    Falls back to regex extraction if all API keys are exhausted.
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

    # Always run regex extraction first — it's instant and reliable
    # AI steps will override these with richer data
    result = regex_extract_snapshot(full_text)
    print(f"[AI Pipeline] Regex extracted {len(result)} fields as baseline")

    # Track if any AI step succeeded
    any_ai_success = False
    api_quota_exhausted = False

    # ── STEP 1: SNAPSHOT ─────────────────────────────────────
    print("[AI Pipeline] Step 1: Snapshot...")
    try:
        snapshot = step1_snapshot(main_text, api_key, all_keys, groq_key)
        if snapshot and len(snapshot) > 3:
            # Merge: AI wins on non-empty fields
            for k, v in snapshot.items():
                if v and str(v).strip() not in ("—", "null", "None", ""):
                    result[k] = v
            any_ai_success = True
            print(f"[AI Pipeline] Step 1 OK: {len(snapshot)} fields")
        else:
            print("[AI Pipeline] Step 1: Empty response — using regex baseline")
    except Exception as e:
        err = str(e).lower()
        if "429" in err or "quota" in err or "exhausted" in err:
            api_quota_exhausted = True
        print(f"[AI Pipeline] Step 1 failed: {str(e)[:60]}")

    # ── STEP 2: CORRIGENDUMS ──────────────────────────────────
    print("[AI Pipeline] Step 2: Corrigendums...")
    if corrigendum_texts and not api_quota_exhausted:
        try:
            corr_overrides = step2_corrigendums(corrigendum_texts, api_key, all_keys, groq_key)
            if corr_overrides:
                for field in ["bid_submission_date","bid_opening_date","prebid_meeting","prebid_query_date"]:
                    if corr_overrides.get(field) and corr_overrides[field] not in ("null","—",""):
                        old = result.get(field,"—")
                        result[field] = corr_overrides[field]
                        print(f"[AI Pipeline] Date override: {field}: {old} → {corr_overrides[field]}")
                if corr_overrides.get("corrigendum_note"):
                    result["corrigendum_note"] = corr_overrides["corrigendum_note"]
                result["has_corrigendum"] = True
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                api_quota_exhausted = True
            print(f"[AI Pipeline] Step 2 failed: {str(e)[:60]}")
    elif corrigendum_texts:
        # Regex-based corrigendum date extraction
        for ct in corrigendum_texts:
            m = re.search(r'extended to (\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', ct)
            if m:
                result["bid_submission_date"] = m.group(1).replace("-", "/").strip()
                result["has_corrigendum"] = True
                result["corrigendum_note"] = "Bid deadline extended — see corrigendum"
                print(f"[AI Pipeline] Corrigendum regex: new deadline = {result['bid_submission_date']}")

    # If quota exhausted after step 1, skip remaining AI steps
    if api_quota_exhausted:
        print("[AI Pipeline] API quota exhausted — using regex baseline for all remaining fields")
        result["ai_warning"] = (
            "API quota exhausted — snapshot extracted by regex. "
            "PQ criteria, scope, and payment terms not available. "
            "Add a new free Gemini key at aistudio.google.com/apikey or try again tomorrow."
        )
        # Build minimal verdict from regex data
        result["verdict"] = "CONDITIONAL"
        result["overall_verdict"] = {
            "verdict": "CONDITIONAL", "color": "BLUE",
            "reason": "API quota exhausted — manual review required",
            "green": 0, "amber": 0, "red": 0
        }
        result["key_reasons"] = ["API quota exhausted — AI analysis incomplete. Review tender manually."]
        result["action_items"] = [{
            "action": "Add a new free Gemini API key at aistudio.google.com/apikey, then re-analyse this tender",
            "responsible": "Admin", "target_date": "Today", "priority": "URGENT"
        }]
        return result

    # ── STEP 3: SCOPE ─────────────────────────────────────────
    print("[AI Pipeline] Step 3: Scope...")
    scope = {}
    try:
        scope = step3_scope(main_text, api_key, all_keys, groq_key)
        if scope.get("scope_background"):
            result["scope_background"] = scope["scope_background"]
        if scope.get("scope_items"):
            result["scope_items"] = scope["scope_items"]
        if scope.get("key_integrations"):
            result["key_integrations"] = scope["key_integrations"]
        any_ai_success = True
    except Exception as e:
        print(f"[AI Pipeline] Step 3 failed: {str(e)[:60]}")

    # ── STEP 4: PQ CRITERIA ───────────────────────────────────
    print("[AI Pipeline] Step 4: PQ Criteria...")
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
                    "sl_no":          str(item.get("sl_no","") or ""),
                    "clause_ref":     str(item.get("clause_ref","—") or "—"),
                    "criteria":       str(item.get("criteria","") or ""),
                    "details":        str(item.get("details","") or ""),
                    "nascent_status": status,
                    "nascent_color":  color,
                    "nascent_remark": str(item.get("nascent_remark","") or ""),
                })
            result["pq_criteria"] = normalized
            any_ai_success = True
    except Exception as e:
        print(f"[AI Pipeline] Step 4 failed: {str(e)[:60]}")

    # ── STEP 5: TQ CRITERIA ───────────────────────────────────
    print("[AI Pipeline] Step 5: TQ Criteria...")
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
                    "criteria":       str(item.get("criteria","") or ""),
                    "details":        str(item.get("details","") or ""),
                    "nascent_status": status,
                    "nascent_color":  color,
                    "nascent_remark": str(item.get("nascent_remark","") or ""),
                })
            result["tq_criteria"] = normalized_tq
    except Exception as e:
        print(f"[AI Pipeline] Step 5 failed: {str(e)[:60]}")

    # ── STEP 6: PAYMENT TERMS ─────────────────────────────────
    print("[AI Pipeline] Step 6: Payment Terms...")
    payment = {}
    try:
        payment = step6_payment(main_text, api_key, all_keys, groq_key)
        if payment.get("payment_terms"):
            result["payment_terms"] = payment["payment_terms"]
        if payment.get("penalty_clauses"):
            result["penalty_clauses"] = payment["penalty_clauses"]
        if payment.get("key_conditions"):
            result["key_conditions"] = payment["key_conditions"]
    except Exception as e:
        print(f"[AI Pipeline] Step 6 failed: {str(e)[:60]}")

    # ── STEP 7: ASSESSMENT + VERDICT ─────────────────────────
    print("[AI Pipeline] Step 7: Nascent Assessment + Verdict...")
    assessment = {}
    try:
        assessment = step7_assessment(result, pq, tq, scope, api_key, all_keys, groq_key)
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
    except Exception as e:
        print(f"[AI Pipeline] Step 7 failed: {str(e)[:60]}")

    # Build overall_verdict
    rec = assessment.get("overall_recommendation", "CONDITIONAL") if assessment else "CONDITIONAL"
    reason = assessment.get("recommendation_reason", "") if assessment else ""
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

    # ── STEP 8: NOTES + CHECKLIST ─────────────────────────────
    print("[AI Pipeline] Step 8: Notes + Checklist...")
    try:
        notes = step8_notes_checklist(result, pq, api_key, all_keys, groq_key)
        if notes.get("notes"):
            result["notes"] = notes["notes"]
        if notes.get("submission_checklist"):
            result["submission_checklist"] = notes["submission_checklist"]
    except Exception as e:
        print(f"[AI Pipeline] Step 8 failed: {str(e)[:60]}")

    print(f"[AI Pipeline] Complete — verdict={verdict}, PQ={len(pq_list)} criteria, AI steps succeeded: {any_ai_success}")
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
