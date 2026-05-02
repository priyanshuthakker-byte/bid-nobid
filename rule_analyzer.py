"""
Rule Analyzer v1 — Zero-API Tender Analyst
Logic: keyword pattern matching + NascentChecker eligibility + hardcoded bid rules from NASCENT_CONTEXT.
Produces identical JSON output format as analyze_with_gemini() — no UI changes needed.

Bid decision rules are derived from ai_analyzer.NASCENT_CONTEXT (single source of truth).
"""

import re
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)

PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"


def _load_profile() -> Dict:
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ─── DOMAIN KEYWORDS ─────────────────────────────────────────────────────────
# Positive match → company can do this → +score

DOMAIN_KEYWORDS = {
    "gis": 40,
    "geospatial": 40,
    "geographic information": 40,
    "web gis": 40,
    "mobile gis": 40,
    "geoserver": 30,
    "postgis": 30,
    "citylayers": 30,
    "qgis": 30,
    "arcgis": 25,
    "smart city": 35,
    "smart cities": 35,
    "ulb": 30,
    "municipal corporation": 30,
    "nagar palika": 30,
    "nagar nigam": 30,
    "urban local body": 30,
    "urban development": 25,
    "city surveillance": 20,
    "egovernance": 35,
    "e-governance": 35,
    "citizen portal": 35,
    "citizen services": 30,
    "government portal": 30,
    "web portal": 25,
    "web application": 20,
    "web app": 20,
    "mobile application": 25,
    "mobile app": 25,
    "android": 20,
    "ios application": 20,
    "flutter": 15,
    "property survey": 30,
    "property tax": 25,
    "utility mapping": 30,
    "asset mapping": 25,
    "field survey": 20,
    "survey application": 20,
    "java": 15,
    "spring boot": 15,
    "react": 15,
    "angular": 15,
    "postgresql": 15,
    "aws": 10,
    "azure": 10,
    "cloud hosting": 10,
    "it services": 10,
    "software development": 15,
    "system integrator": 15,
    "cms": 15,
    "content management": 15,
    "dashboard": 15,
    "analytics": 15,
    "reporting": 10,
    "data management": 15,
    "database": 10,
    "api integration": 15,
    "rest api": 15,
    "msme": 20,
    "sme": 15,
    "startup india": 10,
    "tourism portal": 30,
    "heritage": 20,
    "boq": 10,
    "bill of quantities": 10,
    "erp": 20,
    "enterprise resource": 20,
}

# Negative match → company cannot do this → hard disqualifiers
HARD_DISQUALIFIERS = [
    # Tech stack Nascent doesn't have
    ("cert-in empanelled", "CERT-In empanelment required — Nascent not empanelled. Cannot subcontract this."),
    ("cert-in empanelment", "CERT-In empanelment required — Nascent not empanelled."),
    ("stqc certified", "STQC certification required — Nascent not STQC certified."),
    ("sap partner", "SAP Partner authorization required — Nascent is not SAP partner."),
    ("sap authorized", "SAP Authorized partner required — Nascent is not SAP partner."),
    ("oracle partner", "Oracle Partner authorization required — Nascent is not Oracle partner."),
    ("esri authorized partner", "Esri Authorized Partner required — Nascent is not Esri authorized partner."),
    ("esri partner", "Esri partner required — Nascent is not Esri partner."),
    # Pure supply tenders
    ("supply of hardware", "Pure supply tender — Nascent is a software company, not a hardware supplier."),
    ("supply of equipment", "Pure hardware supply tender — out of scope."),
    ("supply of vehicles", "Vehicle supply tender — completely out of scope."),
    ("supply of furniture", "Furniture supply tender — completely out of scope."),
    ("supply of computers", "Hardware supply tender — out of scope."),
    # Construction / civil
    ("civil works", "Civil/construction work — Nascent is an IT company, not a civil contractor."),
    ("road construction", "Road construction tender — out of scope."),
    ("building construction", "Building construction tender — out of scope."),
    ("construction of", "Construction tender — likely out of scope for IT company."),
    # Defence
    ("defence procurement", "Defence procurement tender — out of scope."),
    ("ministry of defence", "Defence ministry tender — out of scope."),
    ("weapons", "Weapons/defence tender — out of scope."),
    # Pure manpower
    ("manpower supply", "Pure manpower supply tender — Nascent is not a staffing company."),
    ("staff supply", "Pure staffing tender — out of scope."),
    ("labour supply", "Labour supply tender — out of scope."),
]

# Conditional triggers — require pre-bid queries
CONDITIONAL_TRIGGERS = [
    ("net framework", ".NET Framework mandatory — Nascent primary stack is Java. Raise pre-bid query on Java equivalence."),
    (".net mandatory", ".NET mandatory — raise pre-bid query."),
    ("asp.net", "ASP.NET mandatory — raise pre-bid query on Java/Spring Boot equivalence."),
    ("microsoft sql server mandatory", "MS SQL Server mandatory — Nascent primary DB is PostgreSQL. Raise query."),
    ("sql server mandatory", "SQL Server mandatory — raise pre-bid query on PostgreSQL equivalence."),
    ("cert-in", "CERT-In required — check if subcontracting allowed. Raise pre-bid query."),
    ("office in the state", "State office required — Nascent is Gujarat-based only. Raise pre-bid query."),
    ("local office", "Local office may be required — Nascent is Gujarat-based. Raise query."),
    ("resident office", "Resident office required — raise pre-bid query citing GFR 2017 Rule 144."),
    ("consortium not allowed", "Consortium not allowed — verify if Nascent can qualify solo."),
    ("jv not permitted", "JV not permitted — verify solo eligibility."),
]

# Domain categories for classification
DOMAIN_CATEGORIES = {
    "GIS / Geospatial": ["gis", "geospatial", "geographic information", "geoserver", "postgis", "citylayers",
                          "qgis", "arcgis", "web gis", "mobile gis", "property survey", "utility mapping",
                          "asset mapping", "field survey"],
    "Smart City / Urban": ["smart city", "smart cities", "ulb", "municipal corporation", "nagar palika",
                            "nagar nigam", "urban local body", "urban development", "city surveillance"],
    "eGovernance / Portal": ["egovernance", "e-governance", "citizen portal", "citizen services",
                              "government portal", "web portal", "cms", "content management"],
    "Mobile App": ["mobile application", "mobile app", "android", "ios application", "flutter"],
    "ERP / Enterprise": ["erp", "enterprise resource", "property tax", "billing", "accounting"],
    "IT Services / Dev": ["it services", "software development", "system integrator", "web application",
                           "web app", "java", "spring boot", "react", "angular", "api integration"],
}


# ─── TEXT EXTRACTION HELPERS ─────────────────────────────────────────────────

def _extract_field(text: str, patterns: List[str], max_len: int = 200) -> str:
    """Extract a field value from text using regex patterns."""
    t = text[:50000]  # scan first 50K only for speed
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE | re.MULTILINE)
        if m:
            val = m.group(1).strip() if m.lastindex else m.group(0).strip()
            return val[:max_len]
    return "Not found in document"


def _extract_amount_cr(text: str) -> Optional[float]:
    """Find turnover/amount requirements in Crores from text."""
    patterns = [
        r"(?:turnover|annual turnover)[^\d]*(?:Rs\.?\s*)?(\d+(?:\.\d+)?)\s*(?:crore|cr)",
        r"(?:Rs\.?\s*)?(\d+(?:\.\d+)?)\s*(?:crore|cr).*?(?:turnover|financial)",
        r"minimum.*?(?:Rs\.?\s*)?(\d+(?:\.\d+)?)\s*(?:crore|cr)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    return None


def _extract_section(text: str, keywords: List[str], window: int = 4000) -> str:
    """Extract a section of text near a keyword."""
    t_lower = text.lower()
    for kw in keywords:
        idx = t_lower.find(kw.lower())
        if idx != -1:
            return text[max(0, idx - 200): idx + window]
    return ""


def _find_tender_no(text: str) -> str:
    patterns = [
        r"(?:tender\s*(?:no|number|id|ref)[\s:./–-]+)([A-Z0-9/\-_.()]+)",
        r"(?:NIT\s*(?:no|number|ref)?[\s:./–-]+)([A-Z0-9/\-_.()]+)",
        r"(?:RFP\s*(?:no|number|ref)?[\s:./–-]+)([A-Z0-9/\-_.()]+)",
        r"(?:EOI\s*(?:no|number|ref)?[\s:./–-]+)([A-Z0-9/\-_.()]+)",
        r"(?:RFQ\s*(?:no|number|ref)?[\s:./–-]+)([A-Z0-9/\-_.()]+)",
    ]
    return _extract_field(text, patterns, 100)


def _find_org(text: str) -> str:
    patterns = [
        r"(?:issued\s+by|published\s+by|procuring\s+entity|organization|organisation)[\s:]+([^\n]{10,120})",
        r"(?:invites.*?tenders?\s+from|inviting.*?bids?\s+from)(?:[^\n]{0,60})\n([^\n]{10,100})",
    ]
    return _extract_field(text, patterns, 150)


def _find_date(text: str, keywords: List[str]) -> str:
    for kw in keywords:
        idx = text.lower().find(kw.lower())
        if idx != -1:
            snippet = text[idx: idx + 150]
            m = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?:\s*[AP]M)?)?)", snippet, re.IGNORECASE)
            if m:
                return m.group(1).strip()
    return "Not stated"


def _find_amount(text: str, keywords: List[str]) -> str:
    for kw in keywords:
        idx = text.lower().find(kw.lower())
        if idx != -1:
            snippet = text[idx: idx + 300]
            m = re.search(r"(?:Rs\.?|INR|₹)\s*[\d,]+(?:\.\d+)?(?:\s*(?:lakhs?|crore|cr|lakh))?", snippet, re.IGNORECASE)
            if m:
                return m.group(0).strip()
    return "Not stated"


def _find_contract_period(text: str) -> str:
    patterns = [
        r"(?:contract\s*period|period\s*of\s*contract|duration)[^\d]*(\d+\s*(?:years?|months?|days?)[^\n]{0,80})",
        r"(?:completion\s*period|implementation\s*period)[^\d]*(\d+\s*(?:years?|months?|days?)[^\n]{0,60})",
    ]
    return _extract_field(text, patterns, 120)


def _find_scope_items(text: str) -> List[str]:
    """Extract scope of work bullet points."""
    section = _extract_section(text, [
        "scope of work", "scope of services", "scope of supply",
        "deliverables", "functionalities", "requirements", "modules required"
    ], window=5000)
    if not section:
        section = text[:3000]
    items = []
    for m in re.finditer(r"(?:^|\n)\s*(?:\d+\.|[a-z]\.|[-•*▪])\s+(.{30,300})", section, re.MULTILINE):
        item = m.group(1).strip()
        if item and not any(x in item.lower() for x in ["page", "section", "clause"]):
            items.append(item)
        if len(items) >= 10:
            break
    if not items:
        # fallback: extract first few sentences from scope section
        sents = re.split(r"(?<=[.!?])\s+", section)
        items = [s.strip() for s in sents if len(s.strip()) > 40][:6]
    return items or ["Scope details not clearly structured — review full document."]


def _find_payment_terms(text: str) -> List[str]:
    section = _extract_section(text, ["payment terms", "payment schedule", "payment milestone", "payment break"], 3000)
    items = []
    for m in re.finditer(r"(?:^|\n)\s*(?:\d+\.|[a-z]\.|[-•*▪])\s+(.{20,300})", section, re.MULTILINE):
        item = m.group(1).strip()
        if any(x in item.lower() for x in ["%", "percent", "milestone", "upon", "after", "delivery", "payment"]):
            items.append(item)
        if len(items) >= 6:
            break
    return items or ["Payment terms not clearly structured in extracted text — review full document."]


def _find_jv_clause(text: str) -> str:
    section = _extract_section(text, ["consortium", "joint venture", "jv allowed", "subcontract"], 500)
    if section:
        lines = [l.strip() for l in section.split("\n") if l.strip() and len(l.strip()) > 20]
        return " | ".join(lines[:3]) if lines else "Not explicitly stated"
    return "Not explicitly stated"


def _find_portal(text: str) -> str:
    m = re.search(r"https?://[^\s\"'<>]{10,100}", text)
    if m:
        return m.group(0).strip().rstrip(".,)")
    for kw in ["cppp", "eprocure", "gem", "etender", "mstc", "nic"]:
        if kw in text.lower():
            return f"Portal reference: {kw.upper()} (URL not extracted from text)"
    return "Portal URL not found in text"


# ─── SCORING ENGINE ───────────────────────────────────────────────────────────

def _score_domain(text: str) -> Tuple[int, List[str]]:
    """Score how well tender domain matches Nascent capabilities."""
    t_lower = text.lower()
    matched = []
    score = 0
    seen_cats = set()

    for kw, pts in sorted(DOMAIN_KEYWORDS.items(), key=lambda x: -x[1]):
        if kw in t_lower:
            matched.append(kw)
            # cap contribution per category to avoid double-counting
            cat = next((c for c, kws in DOMAIN_CATEGORIES.items() if kw in kws), "Other")
            if cat not in seen_cats:
                score += pts
                seen_cats.add(cat)
            elif pts >= 25:
                score += pts // 3  # partial credit

    return min(score, 100), matched


def _check_hard_disqualifiers(text: str) -> List[Dict]:
    """Check for hard NO-BID conditions. Returns list of triggered disqualifiers."""
    t_lower = text.lower()
    triggered = []
    for pattern, reason in HARD_DISQUALIFIERS:
        if pattern in t_lower:
            triggered.append({"pattern": pattern, "reason": reason})
    return triggered


def _check_conditionals(text: str) -> List[Dict]:
    t_lower = text.lower()
    triggered = []
    for pattern, reason in CONDITIONAL_TRIGGERS:
        if pattern in t_lower:
            triggered.append({"pattern": pattern, "reason": reason})
    return triggered


def _check_turnover_eligibility(text: str, profile: Dict) -> Dict:
    """Check turnover requirement against Nascent financials."""
    required = _extract_amount_cr(text)
    fin = profile.get("finance", {})
    avg2 = fin.get("avg_turnover_last_2_fy", 17.60)
    avg3 = fin.get("avg_turnover_last_3_fy", 17.18)
    avg5 = fin.get("avg_turnover_last_5_fy", 16.23)

    if required is None:
        return {
            "status": "Review",
            "color": "BLUE",
            "remark": f"Turnover requirement not clearly stated in extracted text. Nascent avg turnover: 2-yr Rs.{avg2}Cr | 3-yr Rs.{avg3}Cr | 5-yr Rs.{avg5}Cr. Verify in full document."
        }
    if required <= avg5:
        return {
            "status": "Met",
            "color": "GREEN",
            "remark": f"Turnover required Rs.{required}Cr. Nascent 5-yr avg Rs.{avg5}Cr meets this."
        }
    elif required <= avg3:
        return {
            "status": "Met",
            "color": "GREEN",
            "remark": f"Turnover required Rs.{required}Cr. Nascent 3-yr avg Rs.{avg3}Cr meets this."
        }
    elif required <= avg2:
        return {
            "status": "Met",
            "color": "GREEN",
            "remark": f"Turnover required Rs.{required}Cr. Nascent 2-yr avg Rs.{avg2}Cr meets this."
        }
    elif required <= 20.0:
        return {
            "status": "Conditional",
            "color": "AMBER",
            "remark": (
                f"Turnover required Rs.{required}Cr. Nascent 2-yr avg is Rs.{avg2}Cr — marginally below. "
                "Raise pre-bid query: 'As per MSME Development Act 2006 and Public Procurement Policy for MSMEs, "
                "MSME entities are eligible for relaxation in turnover criteria. Nascent Info Technologies is "
                f"registered MSME (UDYAM-GJ-01-0007420). Request confirmation if MSME relaxation applies to the "
                f"Rs.{required}Cr requirement under Clause [X].' "
                "Also check if 5-yr average or individual year turnover (Rs.20.42Cr in FY21-22) is acceptable."
            )
        }
    else:
        return {
            "status": "Not Met",
            "color": "RED",
            "remark": (
                f"Turnover required Rs.{required}Cr exceeds Nascent maximum avg of Rs.{avg2}Cr (2-yr). "
                "Even with 50% MSME relaxation, requirement may not be met. "
                "RECOMMENDATION: Do not bid unless pre-bid query confirms significant relaxation."
            )
        }


def _check_certifications(text: str, profile: Dict) -> List[Dict]:
    """Check certification requirements."""
    t_lower = text.lower()
    checks = []
    certs = profile.get("certifications", {})

    # CMMI
    if "cmmi" in t_lower:
        level_m = re.search(r"cmmi\s*(?:level|dev|svc)?\s*(\d)", t_lower)
        req_level = int(level_m.group(1)) if level_m else 3
        our_level = certs.get("cmmi", {}).get("level", 3)
        valid_to = certs.get("cmmi", {}).get("valid_to", "19-Dec-2026")
        if our_level >= req_level:
            checks.append({
                "criteria": f"CMMI Level {req_level} certification",
                "status": "Met", "color": "GREEN",
                "remark": f"Nascent holds CMMI V2.0 (DEV) Level 3 (Benchmark 68617), valid to {valid_to}. Meets Level {req_level} requirement."
            })
        else:
            checks.append({
                "criteria": f"CMMI Level {req_level} certification",
                "status": "Not Met", "color": "RED",
                "remark": f"Tender requires CMMI Level {req_level}. Nascent holds Level {our_level} only."
            })

    # ISO 9001
    if "iso 9001" in t_lower or "iso9001" in t_lower:
        iso = certs.get("iso_9001", {})
        checks.append({
            "criteria": "ISO 9001 certification",
            "status": "Met", "color": "GREEN",
            "remark": f"Nascent holds {iso.get('standard','ISO 9001:2015')} (Cert: {iso.get('cert_no','25EQPE64')}), valid to {iso.get('valid_to','08-Sep-2028')}."
        })

    # ISO 27001
    if "iso 27001" in t_lower or "iso27001" in t_lower or "information security" in t_lower:
        iso = certs.get("iso_27001", {})
        checks.append({
            "criteria": "ISO 27001 / Information Security Management",
            "status": "Met", "color": "GREEN",
            "remark": f"Nascent holds ISO/IEC 27001:2022 (Cert: {iso.get('cert_no','25EQPG58')}), valid to {iso.get('valid_to','08-Sep-2028')}."
        })

    # ISO 20000
    if "iso 20000" in t_lower or "iso20000" in t_lower or "itsm" in t_lower:
        iso = certs.get("iso_20000", {})
        checks.append({
            "criteria": "ISO 20000 / IT Service Management",
            "status": "Met", "color": "GREEN",
            "remark": f"Nascent holds ISO/IEC 20000-1:2018, valid to {iso.get('valid_to','08-Sep-2028')}."
        })

    # CERT-In
    if "cert-in" in t_lower or "cert in" in t_lower or "certin" in t_lower:
        if any(x in t_lower for x in ["cert-in empanell", "cert-in empanel", "certin empanel"]):
            checks.append({
                "criteria": "CERT-In Empanelment",
                "status": "Not Met", "color": "RED",
                "remark": "CERT-In empanelment required. Nascent is NOT CERT-In empanelled. This is a hard disqualifier unless subcontracting is permitted."
            })
        else:
            checks.append({
                "criteria": "CERT-In compliance",
                "status": "Conditional", "color": "AMBER",
                "remark": "CERT-In mentioned. Check if empanelment is mandatory or if compliance suffices. Raise pre-bid query."
            })

    # MSME
    if "msme" in t_lower:
        udyam = profile.get("company", {}).get("udyam", "UDYAM-GJ-01-0007420")
        checks.append({
            "criteria": "MSME registration",
            "status": "Met", "color": "GREEN",
            "remark": f"Nascent is registered MSME ({udyam}, Lifetime validity). MSME Policy benefits applicable."
        })

    return checks


def _build_pq_criteria(text: str, profile: Dict) -> List[Dict]:
    """Build PQ criteria list using NascentChecker + rule-based extraction."""
    criteria = []
    sl = 1

    try:
        from nascent_checker import NascentChecker
        checker = NascentChecker()
        pq_section = _extract_section(text, [
            "pre-qualification", "pre qualification", "eligibility criteria",
            "qualifying criteria", "pq criteria"
        ], 6000)

        if pq_section:
            # Extract individual criteria rows from PQ section
            rows = re.split(r"\n{2,}|\r\n{2,}", pq_section)
            for row in rows:
                row = row.strip()
                if len(row) < 30:
                    continue
                # Classify and check the row
                row_lower = row.lower()
                check_result = None
                criteria_type = "General"

                if any(x in row_lower for x in ["turnover", "annual turnover", "financial turnover"]):
                    check_result = checker.check_turnover(row)
                    criteria_type = "Turnover"
                elif any(x in row_lower for x in ["registration", "incorporated", "company registration"]):
                    check_result = checker.check_company_registration(row)
                    criteria_type = "Company Registration"
                elif any(x in row_lower for x in ["experience", "similar work", "project", "executed"]):
                    check_result = checker.check_similar_work_experience(row)
                    criteria_type = "Experience"
                elif any(x in row_lower for x in ["net worth", "networth", "solvency"]):
                    check_result = checker.check_net_worth(row)
                    criteria_type = "Net Worth / Solvency"
                elif any(x in row_lower for x in ["employee", "manpower", "staff", "resource"]):
                    check_result = checker.check_employee_count(row)
                    criteria_type = "Employees"
                elif any(x in row_lower for x in ["cmmi", "iso", "cert-in", "msme", "certificate"]):
                    check_result = checker.check_certifications(row)
                    criteria_type = "Certifications"
                elif any(x in row_lower for x in ["blacklist", "debar", "litigation"]):
                    check_result = checker.check_blacklist(row)
                    criteria_type = "Legal / Blacklist"

                if check_result:
                    criteria.append({
                        "sl_no": str(sl),
                        "clause_ref": f"PQ Criteria {sl}",
                        "criteria": row[:300],
                        "details": f"Auto-extracted from PQ section — verify against original document",
                        "nascent_status": check_result.get("status", "Review"),
                        "nascent_remark": check_result.get("remark", "See full document")
                    })
                    sl += 1
                    if sl > 15:
                        break
    except Exception as e:
        logger.warning(f"NascentChecker PQ extraction failed: {e}")

    # Always add cert checks
    cert_checks = _check_certifications(text, profile)
    for c in cert_checks:
        criteria.append({
            "sl_no": str(sl),
            "clause_ref": "Certification check",
            "criteria": c["criteria"],
            "details": "Auto-detected from document scan",
            "nascent_status": c["status"],
            "nascent_remark": c["remark"]
        })
        sl += 1

    # Turnover check
    to_check = _check_turnover_eligibility(text, profile)
    criteria.append({
        "sl_no": str(sl),
        "clause_ref": "Financial Eligibility",
        "criteria": "Annual Turnover requirement",
        "details": "Auto-extracted from document",
        "nascent_status": to_check["status"],
        "nascent_remark": to_check["remark"]
    })

    if not criteria:
        criteria.append({
            "sl_no": "1",
            "clause_ref": "General",
            "criteria": "PQ criteria not clearly structured in extracted text",
            "details": "Manual review required",
            "nascent_status": "Review",
            "nascent_remark": "Please review the original tender document for PQ criteria."
        })

    return criteria


def _classify_domains(text: str) -> List[str]:
    t_lower = text.lower()
    matched = []
    for cat, keywords in DOMAIN_CATEGORIES.items():
        if any(kw in t_lower for kw in keywords):
            matched.append(cat)
    return matched or ["General IT / Software"]


def _determine_verdict(
    domain_score: int,
    hard_disqs: List[Dict],
    conditionals: List[Dict],
    turnover_check: Dict,
    domain_matches: List[str]
) -> Tuple[str, int, str]:
    """
    Returns (verdict, final_score, reason).
    verdict: "BID" | "NO_BID" | "CONDITIONAL"
    """

    # Hard disqualifiers = instant NO_BID
    if hard_disqs:
        reasons = " | ".join(d["reason"] for d in hard_disqs[:3])
        return "NO_BID", 0, f"Hard disqualifier(s) found: {reasons}"

    # Turnover hard fail
    if turnover_check.get("status") == "Not Met":
        return "NO_BID", 5, f"Financial disqualifier: {turnover_check['remark']}"

    # Score calculation
    # domain_score: 0-100 (how well keywords match)
    # Normalize to 60% of final score
    domain_component = int(domain_score * 0.6)

    # Financial component: 0-30
    fin_map = {"Met": 30, "Conditional": 15, "Review": 15, "Not Met": 0}
    fin_component = fin_map.get(turnover_check.get("status", "Review"), 10)

    # Conditional penalty: -5 per conditional
    conditional_penalty = min(len(conditionals) * 5, 20)

    final_score = max(0, min(100, domain_component + fin_component - conditional_penalty))

    # Verdict logic
    if hard_disqs:
        verdict = "NO_BID"
        reason = f"Hard disqualifier: {hard_disqs[0]['reason']}"
    elif domain_score < 15 and not domain_matches:
        verdict = "NO_BID"
        reason = "Tender domain does not match Nascent capabilities (GIS, web/mobile app, eGov, Smart City)."
        final_score = min(final_score, 20)
    elif conditionals or turnover_check.get("status") in ("Conditional", "Review"):
        verdict = "CONDITIONAL"
        cond_list = " | ".join(c["reason"] for c in conditionals[:2]) if conditionals else ""
        reason = f"Conditional items require pre-bid queries before committing to bid. {cond_list}".strip()
    elif final_score >= 60:
        verdict = "BID"
        reason = f"Tender aligns with Nascent capabilities in: {', '.join(domain_matches[:3])}. Financial eligibility met."
    elif final_score >= 35:
        verdict = "CONDITIONAL"
        reason = "Partial domain match. Verify full PQ criteria before deciding."
    else:
        verdict = "NO_BID"
        reason = "Low domain alignment with Nascent capabilities. Risk outweighs potential."

    return verdict, final_score, reason


# ─── CHECKLIST BUILDER ────────────────────────────────────────────────────────

# Standard documents always needed in govt tenders
_STANDARD_DOCS = [
    ("Company Registration Certificate / MoA", True,
     "Certificate of Incorporation + MoA/AoA (self-attested copy)"),
    ("PAN Card", True, "Company PAN — self-attested Xerox"),
    ("GST Registration Certificate", True, "GSTIN 24AACCN3670J1ZG — self-attested"),
    ("Audited Balance Sheet + P&L (last 3 FY)", True,
     "FY 2022-23, 2023-24, 2024-25 — signed by CA Anuj J. Sharedalal"),
    ("CA Certificate for Turnover", True,
     "Annual turnover certificate on CA letterhead with UDIN"),
    ("MSME / Udyam Registration Certificate", True,
     "UDYAM-GJ-01-0007420 — Lifetime validity"),
    ("CMMI Level 3 Certificate", True,
     "CMMI V2.0 Dev, Benchmark 68617, valid till 19-Dec-2026"),
    ("ISO 9001:2015 Certificate", True,
     "Cert No. 25EQPE64, valid till 08-Sep-2028"),
    ("ISO/IEC 27001:2022 Certificate", True,
     "Cert No. 25EQPG58, valid till 08-Sep-2028"),
    ("Work Order Copies (similar projects)", True,
     "Scan copies — WO from client on letterhead"),
    ("Completion / Satisfactory Performance Certificate", True,
     "From client — per tender format or plain letter"),
    ("Employee Strength Certificate", True,
     "On company letterhead — signed by authorized signatory"),
    ("Self-Declaration / Non-Blacklisting Affidavit", True,
     "Notarized on stamp paper — signed by Hitesh Patel (POA valid till 31-Mar-2026)"),
    ("Power of Attorney / Authorization", True,
     "Authorizing Hitesh Patel (CAO) to sign bid documents — expires 31-Mar-2026"),
    ("Solvency Certificate from Banker", False,
     "From SBI, SG Highway Branch — obtain on demand"),
    ("Net Worth Certificate from CA", False,
     "Net worth Rs. 26.09 Cr as of latest FY — on CA letterhead"),
]

_CONDITIONAL_DOCS = [
    ("emd", "EMD Demand Draft / Bank Guarantee or Udyam EMD Exemption Certificate",
     "Nascent is MSME — raise pre-bid query for EMD exemption first"),
    ("iso 20000", "ISO/IEC 20000-1:2018 Certificate",
     "Cert No. 25ZQZQ030409IT, valid till 08-Sep-2028"),
    ("stqc", "STQC Certificate (or raise pre-bid query on ISO equivalence)", "Nascent does not hold STQC"),
    ("cert-in", "CERT-In Empanelment Certificate (or pre-bid query)", "Nascent is NOT CERT-In empanelled"),
    ("consortium", "JV / Consortium Agreement (signed by all partners)", "Required if bidding as consortium"),
    ("jv", "JV Agreement — registered or unregistered as required", "All partners to sign"),
    ("performance security", "Performance Bank Guarantee (PBG)", "Typically 5-10% of contract value"),
    ("bid validity", "EMD / Bid Security validity certificate", "Check validity period in tender"),
    ("msme", "MSME / Udyam Certificate for EMD exemption claim", "UDYAM-GJ-01-0007420"),
    ("nda", "Non-Disclosure Agreement (if required)", "To be signed before document access"),
    ("integrity pact", "Integrity Pact (signed)", "Some tenders require IP with independent monitor"),
    ("local office", "Undertaking to open local/state office within 30 days of award",
     "Nascent office only in Ahmedabad — provide undertaking"),
    ("annual report", "Annual Report (last 3 years)", "For certain large value tenders"),
    ("bank details", "Bank Account Details — IFSC, Account No.", "For payment processing at contract stage"),
]


def _build_checklist(text: str, profile: Dict) -> List[Dict]:
    """Build submission checklist from standard docs + conditional triggers from text."""
    t_lower = text.lower()
    sl = 1
    items = []

    # Always-required documents
    for doc_name, mandatory, hint in _STANDARD_DOCS:
        items.append({
            "sl_no": str(sl),
            "document": doc_name,
            "mandatory": mandatory,
            "status": "Prepare",
            "hint": hint,
        })
        sl += 1

    # Conditional documents triggered by text
    for kw, doc_name, hint in _CONDITIONAL_DOCS:
        if kw in t_lower:
            items.append({
                "sl_no": str(sl),
                "document": doc_name,
                "mandatory": kw not in ("stqc","cert-in","nda","integrity pact","annual report","bank details"),
                "status": "Verify",
                "hint": hint,
            })
            sl += 1

    # Project-specific additions
    if "mobile" in t_lower or "android" in t_lower:
        items.append({
            "sl_no": str(sl), "mandatory": False,
            "document": "Screenshots / App Demo Link (mobile GIS experience)",
            "status": "Optional",
            "hint": "Show AMC/BMC/KVIC mobile app links if available",
        }); sl += 1

    if "ogc" in t_lower or "citylayers" in t_lower:
        items.append({
            "sl_no": str(sl), "mandatory": True,
            "document": "OGC CityLayers 2.0 Compliance Certificate",
            "status": "Prepare",
            "hint": "PID 1600 — OGC compliance certificate for CityLayers 2.0",
        }); sl += 1

    return items


# ─── WORK SCHEDULE BUILDER ────────────────────────────────────────────────────

def _build_work_schedule(text: str) -> List[Dict]:
    """Extract or estimate work schedule milestones from text."""
    t_lower = text.lower()
    schedule = []

    # Try to extract milestones from text (numbered list items near schedule section)
    section = _extract_section(text, [
        "work schedule", "implementation schedule", "project schedule",
        "deployment schedule", "milestone", "deliverable timeline",
        "activity schedule", "time schedule"
    ], 5000)

    if section:
        lines = section.split("\n")
        milestone_re = re.compile(
            r"(?:^\s*(?:\d+(?:\.\d+)?\.?|[a-z]\.|[-•*▪])\s+)(.{20,200})",
            re.IGNORECASE | re.MULTILINE
        )
        week_re = re.compile(r"(\d+)\s*(?:weeks?|wks?)", re.IGNORECASE)
        month_re = re.compile(r"(\d+)\s*(?:months?|mths?)", re.IGNORECASE)
        day_re = re.compile(r"(\d+)\s*(?:days?)", re.IGNORECASE)

        for m in milestone_re.finditer(section):
            item = m.group(1).strip()
            if not item or len(item) < 15: continue
            timeline = ""
            for pat, unit in [(week_re, "weeks"), (month_re, "months"), (day_re, "days")]:
                tm = pat.search(item)
                if tm:
                    timeline = f"{tm.group(1)} {unit}"
                    break
            schedule.append({
                "sl": str(len(schedule) + 1),
                "activity": item[:200],
                "timeline": timeline or "—",
            })
            if len(schedule) >= 12: break

    # Fallback: generate standard GIS/IT project schedule based on domain detection
    if not schedule:
        # Detect contract period
        period_text = _find_contract_period(text)
        months = 12  # default
        m_months = re.search(r"(\d+)\s*months?", period_text, re.IGNORECASE)
        m_years  = re.search(r"(\d+)\s*years?", period_text, re.IGNORECASE)
        if m_months: months = int(m_months.group(1))
        elif m_years: months = int(m_years.group(1)) * 12

        # Build domain-specific milestones
        t_lower2 = text.lower()
        ms_list = []
        if any(x in t_lower2 for x in ["gis","geospatial","mapping"]):
            ms_list = [
                ("Project Kickoff & Team Mobilization", "1 month"),
                ("Requirement Gathering & Gap Analysis", "1-2 months"),
                ("GIS Data Collection & Field Survey", f"1-{min(months//3,4)} months"),
                ("GIS Database Setup & Layer Creation", f"{min(months//4,3)}-{min(months//2,6)} months"),
                ("Web GIS / Application Development", f"{min(months//3,4)}-{min(months*2//3,8)} months"),
                ("UAT (User Acceptance Testing)", "1 month"),
                ("Data Migration & Integration", "1 month"),
                ("Training & Handholding", "1 month"),
                ("Go-Live & Stabilization", "1 month"),
                ("AMC / O&M Support", f"Post go-live ({max(0,months-10)} months remaining)"),
            ]
        elif any(x in t_lower2 for x in ["portal","web","egovernance","citizen"]):
            ms_list = [
                ("Project Kickoff & Requirement Study", "1 month"),
                ("System Design & Architecture", "1 month"),
                ("Module Development (Phase 1)", f"1-{min(months//3,4)} months"),
                ("Module Development (Phase 2)", f"{min(months//3,4)}-{min(months//2,6)} months"),
                ("Integration & API Development", "1-2 months"),
                ("QA / Testing & Bug Fixing", "1-2 months"),
                ("UAT with Client Team", "1 month"),
                ("Deployment & Go-Live", "1 month"),
                ("Training & Documentation", "1 month"),
                ("AMC / Support Period", f"Post go-live"),
            ]
        else:
            ms_list = [
                ("Project Kickoff & Mobilization", "Month 1"),
                ("Requirement Analysis & Design", f"Month 1-{min(2,months//5+1)}"),
                ("Development Phase 1", f"Month {min(3,months//4+1)}-{min(months//2,6)}"),
                ("Development Phase 2", f"Month {min(months//2,7)}-{min(months*2//3,9)}"),
                ("Testing & QA", f"Month {min(months*2//3,10)}-{min(months-2,11)}"),
                ("UAT & Acceptance", f"Month {max(months-2,5)}-{max(months-1,6)}"),
                ("Go-Live & Support", f"Month {max(months-1,6)} onwards"),
            ]

        for i, (act, tl) in enumerate(ms_list, 1):
            schedule.append({"sl": str(i), "activity": act, "timeline": tl})

    return schedule


# ─── CORRIGENDUM DETECTOR ─────────────────────────────────────────────────────

def _detect_corrigendum(text: str) -> Dict:
    """Detect if document is a corrigendum / amendment."""
    t_lower = text.lower()
    is_corrigendum = any(x in t_lower for x in [
        "corrigendum", "addendum", "amendment", "erratum", "modification to",
        "revised tender", "change in tender", "revised nit", "revised rfp",
        "extension of date", "extension of time", "date extension"
    ])
    if not is_corrigendum:
        return {"detected": False, "type": None, "notes": []}

    corr_type = (
        "Corrigendum" if "corrigendum" in t_lower else
        "Addendum" if "addendum" in t_lower else
        "Amendment" if "amendment" in t_lower else
        "Date Extension" if "extension" in t_lower else
        "Erratum"
    )

    # Extract what changed
    change_section = _extract_section(text, [
        "amendment", "change", "modification", "revised", "revised as follows",
        "new date", "extended date", "corrected", "replaced with"
    ], 3000)

    notes = []
    if "date" in t_lower or "deadline" in t_lower:
        new_date = _find_date(text, [
            "new date", "revised date", "extended date", "extended to",
            "submission date", "last date", "closing date"
        ])
        if new_date and new_date != "Not stated":
            notes.append(f"Revised submission date: {new_date}")
        notes.append("Update deadline in system to reflect revised date.")

    if any(x in t_lower for x in ["eligibility", "criteria", "qualification"]):
        notes.append("Eligibility criteria may have changed — re-check PQ criteria.")

    if any(x in t_lower for x in ["scope", "specification", "bill of quantity", "boq"]):
        notes.append("Scope/BOQ may have changed — re-check scope of work.")

    if any(x in t_lower for x in ["emd", "earnest money"]):
        notes.append("EMD amount may have changed — verify in corrigendum.")

    if not notes:
        notes.append("Review corrigendum carefully — changes may affect bid strategy.")

    return {
        "detected": True,
        "type": corr_type,
        "notes": notes,
        "action": "Update tender record with revised dates/conditions before bid submission.",
    }


# ─── SIMILAR PROJECT MATCHER ──────────────────────────────────────────────────

def _find_similar_projects(text: str, profile: Dict) -> List[Dict]:
    """Match tender requirements to Nascent's past projects (for experience certificates)."""
    t_lower = text.lower()
    projects = profile.get("projects", [])
    if not projects:
        return []

    # Score each project against tender text
    scored = []
    for proj in projects:
        proj_text = " ".join([
            proj.get("name",""),
            proj.get("client",""),
            " ".join(proj.get("tags",[])),
        ]).lower()

        score = 0
        matched_on = []

        # Tag-based matching
        for tag in proj.get("tags",[]):
            tag_kws = tag.lower().split()
            if all(kw in t_lower for kw in tag_kws if len(kw) > 3):
                score += 20
                matched_on.append(tag)

        # Domain keyword matching
        domain_pairs = [
            (["gis","geospatial","geographic","mapping"], ["gis survey","gis mapping","web gis","gis portal","utility mapping"]),
            (["mobile app","android","ios","mobile application"], ["mobile app","mobile gis"]),
            (["smart city","ulb","municipal","urban"], ["smart city"]),
            (["erp","enterprise resource"], ["erp"]),
            (["egovernance","citizen portal","government portal"], ["egovernance","web portal"]),
            (["tourism"], ["tourism"]),
            (["survey","field data"], ["gis survey","field survey"]),
        ]
        for tender_kws, proj_tags in domain_pairs:
            tender_match = any(kw in t_lower for kw in tender_kws)
            proj_match = any(pt.lower() in proj_text for pt in proj_tags)
            if tender_match and proj_match:
                score += 15
                matched_on.append(tender_kws[0])

        # Client type preference
        required_cr = _extract_amount_cr(text)
        if required_cr:
            val = proj.get("value_cr", 0) or 0
            if val >= required_cr:
                score += 25
                matched_on.append(f"meets value threshold Rs.{required_cr}Cr")
            elif val >= required_cr * 0.5:
                score += 10
                matched_on.append(f"partially meets value Rs.{required_cr}Cr")

        # Web GIS / Mobile GIS bonus
        if "web gis" in t_lower and proj.get("web_gis"):
            score += 15
            matched_on.append("web GIS")
        if any(x in t_lower for x in ["mobile","android","ios"]) and proj.get("mobile_gis"):
            score += 15
            matched_on.append("mobile GIS")
        if "central server" in t_lower and proj.get("central_server"):
            score += 10
            matched_on.append("central server")

        if score > 0:
            scored.append((score, proj, list(set(matched_on))))

    scored.sort(key=lambda x: -x[0])

    result = []
    for score, proj, matched in scored[:5]:
        result.append({
            "project": proj.get("name",""),
            "client": proj.get("client",""),
            "client_type": proj.get("client_type",""),
            "state": proj.get("state",""),
            "value_cr": proj.get("value_cr",0),
            "status": proj.get("status",""),
            "role": proj.get("role","Solo"),
            "matched_on": ", ".join(matched) if matched else "General domain match",
            "relevance_score": score,
            "use_for": "Similar work certificate — work order + completion certificate",
        })

    return result


# ─── MAIN ANALYSIS FUNCTION ───────────────────────────────────────────────────

def analyze_with_rules(text: str, prebid_passed: bool = False) -> Dict[str, Any]:
    """
    Full rule-based tender analysis. Zero API calls.
    Returns same JSON structure as analyze_with_gemini().
    """
    profile = _load_profile()
    t_lower = text.lower()

    # Field extraction
    tender_no = _find_tender_no(text)
    org_name = _find_org(text)
    portal = _find_portal(text)

    bid_start = _find_date(text, ["start date", "bid start", "start of bid", "publishing date", "published on"])
    bid_end = _find_date(text, [
        "bid submission", "last date", "closing date", "submission deadline",
        "due date", "bid closing", "end date"
    ])
    bid_opening = _find_date(text, ["opening date", "bid opening", "technical bid opening"])
    prebid_date = _find_date(text, ["pre-bid", "prebid", "pre bid meeting"])

    est_cost = _find_amount(text, ["estimated cost", "estimated value", "project cost", "tender value", "approximate cost"])
    tender_fee = _find_amount(text, ["tender fee", "document fee", "bid document", "tender document fee"])
    emd = _find_amount(text, ["emd", "earnest money", "bid security"])

    # Check MSME EMD exemption
    emd_exemption = "MSME exemption applicable as per Public Procurement Policy 2012 — verify in document" \
        if "msme" in t_lower or "micro small" in t_lower else "Not mentioned — verify in document"

    contract_period = _find_contract_period(text)
    scope_items = _find_scope_items(text)
    payment_terms = _find_payment_terms(text)
    jv_allowed = _find_jv_clause(text)

    # Mode of selection
    mode = "L1 (Lowest Cost)" if any(x in t_lower for x in ["l1", "lowest bid", "least cost"]) else \
           "QCBS" if "qcbs" in t_lower else \
           "Quality + Cost" if "quality" in t_lower and "cost" in t_lower else \
           "Not clearly stated — verify in document"

    # Technology mandatory
    tech_mandatory = []
    for tech in [".net", "asp.net", "sap", "oracle", "esri", "arcgis server",
                 "cert-in", "stqc", "cmmi", "java", "python", "angular", "react"]:
        if tech in t_lower:
            tech_mandatory.append(tech.upper())
    tech_mandatory_str = ", ".join(tech_mandatory) if tech_mandatory else "No specific technology mandate detected"

    # Scoring
    domain_score, domain_matches_raw = _score_domain(text)
    domain_matches = _classify_domains(text)
    hard_disqs = _check_hard_disqualifiers(text)
    conditionals = _check_conditionals(text)
    turnover_check = _check_turnover_eligibility(text, profile)

    verdict, final_score, verdict_reason = _determine_verdict(
        domain_score, hard_disqs, conditionals, turnover_check, domain_matches
    )

    # PQ criteria
    pq_criteria = _build_pq_criteria(text, profile)

    # TQ criteria (rule-based estimation only)
    tq_criteria = []
    tq_domains = [
        ("GIS / Geospatial experience", "gis", "geoserver", "web gis"),
        ("Smart City / Urban projects", "smart city", "ulb", "municipal"),
        ("eGovernance / Portal", "egovernance", "citizen portal"),
        ("Mobile Application", "mobile app", "android", "flutter"),
        ("Certifications (CMMI / ISO)", "cmmi", "iso 9001", "iso 27001"),
        ("Turnover / Financial strength", "turnover", "financial"),
    ]
    tq_sl = 1
    for (label, *kws) in tq_domains:
        matched = any(kw in t_lower for kw in kws)
        if matched:
            tq_criteria.append({
                "sl_no": str(tq_sl),
                "clause_ref": f"TQ {tq_sl}",
                "criteria": label,
                "details": "TQ marks not extractable without AI — verify in full document",
                "nascent_status": "Met",
                "nascent_remark": f"Nascent has relevant experience in {label}. Exact score depends on TQ scoring table in document."
            })
            tq_sl += 1

    # Notes & action items
    notes = []
    poa = profile.get("company", {}).get("poa_alert", "")
    if poa:
        notes.append(poa)
    if hard_disqs:
        for d in hard_disqs:
            notes.append(f"DISQUALIFIER: {d['reason']}")
    if conditionals:
        for c in conditionals:
            notes.append(f"PRE-BID NEEDED: {c['reason']}")
    if turnover_check.get("status") == "Conditional":
        notes.append("MSME turnover relaxation query must be sent before bid submission.")
    if bid_end and bid_end != "Not stated":
        notes.append(f"Submission deadline: {bid_end} — verify exact time in document.")
    if emd != "Not stated":
        notes.append(f"EMD: {emd}. Check MSME exemption clause in document.")
    notes.append("NOTE: This is a RULE-BASED analysis (no AI). Verify all extracted values against original tender document.")
    notes.append("For full AI analysis with clause-level extraction, configure Gemini/Groq API key in Settings.")

    # Pre-bid queries
    prebid_queries = []
    if not prebid_passed:
        if turnover_check.get("status") == "Conditional":
            prebid_queries.append(
                "Query on MSME Turnover Relaxation: 'Ref Clause [X] — Turnover requirement is stated as Rs.[X]Cr. "
                "Nascent Info Technologies is a registered MSME (UDYAM-GJ-01-0007420). "
                "Request confirmation whether MSME turnover relaxation of 50% as per Public Procurement Policy 2012 applies to this tender.'"
            )
        for c in conditionals:
            prebid_queries.append(f"Pre-bid query required: {c['reason']}")

    # Tender name heuristic
    tender_name = _extract_field(text, [
        r"(?:request\s+for\s+proposal|rfp|nit|tender)\s+for\s+([^\n]{20,200})",
        r"(?:development|implementation|supply|provision)\s+of\s+([^\n]{20,150})",
    ], 200)

    # Submission checklist — rule-based
    checklist = _build_checklist(text, profile)

    # Work schedule — rule-based
    work_schedule = _build_work_schedule(text)

    # Corrigendum detection
    corrigendum = _detect_corrigendum(text)

    # Similar work from profile
    similar_work = _find_similar_projects(text, profile)

    # Build final result
    result = {
        "tender_no": tender_no,
        "org_name": org_name,
        "tender_name": tender_name,
        "portal": portal,
        "bid_start_date": bid_start,
        "bid_submission_date": bid_end,
        "bid_opening_date": bid_opening,
        "commercial_opening_date": "Not stated",
        "prebid_meeting": prebid_date if prebid_date != "Not stated" else "Not stated / Not applicable",
        "prebid_query_date": prebid_date,
        "estimated_cost": est_cost,
        "tender_fee": tender_fee,
        "emd": emd,
        "emd_exemption": emd_exemption,
        "performance_security": _find_amount(text, ["performance security", "performance guarantee", "security deposit"]) or "Not stated",
        "contract_period": contract_period,
        "bid_validity": _extract_field(text, [r"bid\s+validity[^\d]*(\d+\s*(?:days?|months?))"]),
        "location": _extract_field(text, [
            r"(?:project\s+location|work\s+location|delivery\s+location)[:\s]+([^\n]{10,100})",
            r"(?:state|district|city)[:\s]+([^\n]{5,80})",
        ]),
        "contact": _extract_field(text, [
            r"(?:contact|email|e-mail)[:\s]+([^\n]{10,100})",
        ]),
        "jv_allowed": jv_allowed,
        "mode_of_selection": mode,
        "tender_type": _extract_field(text, [
            r"(?:type\s+of\s+(?:contract|tender|bid))[:\s]+([^\n]{5,80})",
        ]),
        "post_implementation": _extract_field(text, [
            r"(?:amc|annual\s+maintenance|post\s+implementation\s+support)[^\d]*(\d+\s*(?:years?|months?))",
        ]),
        "technology_mandatory": tech_mandatory_str,
        "scope_items": scope_items,
        "pq_criteria": pq_criteria,
        "tq_criteria": tq_criteria,
        "payment_terms": payment_terms,
        "checklist": checklist,
        "work_schedule": work_schedule,
        "corrigendum": corrigendum,
        "similar_projects": similar_work,
        "overall_recommendation": verdict,
        "recommendation_reason": verdict_reason,
        "notes": notes,
        # Extra metadata
        "_analysis_mode": "RULE_BASED",
        "_domain_score": domain_score,
        "_domain_matches": domain_matches,
        "_final_score": final_score,
        "_hard_disqualifiers": [d["reason"] for d in hard_disqs],
        "_conditionals": [c["reason"] for c in conditionals],
        "_prebid_queries": prebid_queries,
    }

    return result


# ─── STANDALONE TEST ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore")
    else:
        text = """
        Tender No: GIS/ULB/2024/001
        Organization: Municipal Corporation of Ahmedabad
        Request for Proposal for Development of Web GIS and Mobile Application
        for Property Survey and Utility Mapping.
        Turnover requirement: Rs. 10 Crore per annum (last 3 financial years)
        CMMI Level 3 certification required.
        ISO 9001 certification required.
        MSME exemption applicable on EMD.
        Bid submission date: 15/06/2024 5:00 PM
        EMD: Rs. 5,00,000 (Five Lakhs only)
        Contract period: 12 months + 2 years AMC
        Estimated project cost: Rs. 2.5 Crore
        """
    result = analyze_with_rules(text)
    print(json.dumps(result, indent=2))
