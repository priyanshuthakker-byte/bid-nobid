"""
guidelines_library.py — Indian Procurement Guidelines Library
Pre-loaded with key guidelines. User can add custom ones.
Used by prebid_generator.py and chatbot.py for accurate citations.
"""
import json
from pathlib import Path
from typing import List, Dict

BASE_DIR = Path(__file__).parent
CUSTOM_GUIDELINES_FILE = BASE_DIR / "data" / "custom_guidelines.json"

# ── PRE-LOADED INDIAN PROCUREMENT GUIDELINES ──────────────────
BUILTIN_GUIDELINES = [
    {
        "id": "ppp_msme_2012",
        "name": "Public Procurement Policy for MSMEs Order 2012",
        "short": "PPP-MSME 2012",
        "category": "MSME",
        "applies_to": ["emd", "earnest money", "bid security", "msme", "exemption", "turnover"],
        "key_provisions": [
            "MSME registered entities are exempt from paying EMD/Bid Security",
            "25% of annual procurement to be from MSMEs",
            "Tender fee exemption for MSMEs in many central government tenders",
            "Amended by DoE OM F.No.6/18/2019-PPD dated 16.11.2020",
        ],
        "authority": "Ministry of MSME, Government of India",
        "cite_as": "Public Procurement Policy for MSMEs Order 2012 (as amended by DoE OM dated 16.11.2020)",
    },
    {
        "id": "gfr_2017",
        "name": "General Financial Rules 2017",
        "short": "GFR 2017",
        "category": "Procurement",
        "applies_to": ["experience", "similar work", "turnover", "qualification", "open tender", "limited tender"],
        "key_provisions": [
            "Rule 161: Open tender to be used for procurements above Rs.25 lakh",
            "Rule 173: Bid security / EMD to be prescribed in RFP",
            "Rule 174: Experience criteria should be proportionate to procurement value",
            "Similar work experience: both single and cumulative should be considered",
            "Turnover criterion: should not exceed 3 times the estimated value",
        ],
        "authority": "Ministry of Finance, Department of Expenditure",
        "cite_as": "General Financial Rules (GFR) 2017, Rule [X]",
    },
    {
        "id": "cvc_circular_2007",
        "name": "CVC Circular on Procurement Transparency",
        "short": "CVC Guidelines",
        "category": "Transparency",
        "applies_to": ["technology", "proprietary", "brand", "mandatory software", "specific platform", "microsoft", ".net", "oracle"],
        "key_provisions": [
            "Tender specifications should not be tailor-made to favour a specific vendor",
            "Technology stack should be specified functionally, not by brand/vendor name",
            "Open standards and technology-neutral specifications to be preferred",
            "Mandatory requirement for specific proprietary software should be justified",
            "OEM authorization letters should not be mandated unless technically justified",
        ],
        "authority": "Central Vigilance Commission",
        "cite_as": "CVC Guidelines on Transparency in Public Procurement / CVC Circular No. 98/ORD/1 dated 23.09.2019",
    },
    {
        "id": "dpiit_startup",
        "name": "DPIIT Startup India Exemptions",
        "short": "DPIIT Startup",
        "category": "Startup",
        "applies_to": ["startup", "experience", "prior experience", "turnover criterion", "dpiit"],
        "key_provisions": [
            "DPIIT-recognized startups exempt from prior experience criterion in government tenders",
            "DPIIT startups exempt from turnover criteria",
            "Applicable for procurements up to Rs.25 Cr (subject to notification)",
            "Startups must produce DPIIT recognition certificate",
        ],
        "authority": "DPIIT (Ministry of Commerce & Industry), Government of India",
        "cite_as": "DPIIT Order F.No. P-15025/1/2022-Startup Policy dated 09.02.2022",
    },
    {
        "id": "meity_oss_2015",
        "name": "MeitY Policy on Open Source Software",
        "short": "MeitY OSS Policy",
        "category": "Technology",
        "applies_to": ["open source", "proprietary software", "technology", "gis software", "platform", "mandatory technology"],
        "key_provisions": [
            "Government organizations should prefer Open Source Software (OSS)",
            "Proprietary software should not be mandated unless OSS alternatives are not available",
            "Equal evaluation of OSS and proprietary solutions",
            "OSS helps avoid vendor lock-in",
        ],
        "authority": "Ministry of Electronics & Information Technology (MeitY)",
        "cite_as": "MeitY Policy on Adoption of Open Source Software in Government Organisations (2015)",
    },
    {
        "id": "gem_guidelines",
        "name": "GeM Procurement Guidelines",
        "short": "GeM Guidelines",
        "category": "GeM",
        "applies_to": ["gem", "government e-marketplace", "mse preference", "mse", "startup gem"],
        "key_provisions": [
            "MSEs get price preference of 15% on GeM",
            "25% of annual procurement target for MSEs on GeM",
            "MSE sellers get purchase preference",
            "Startups recognized by DPIIT get special access",
            "Custom bid on GeM allows technical evaluation criteria",
        ],
        "authority": "GeM / Ministry of Commerce & Industry",
        "cite_as": "GeM Seller Registration and Procurement Guidelines (updated 2024)",
    },
    {
        "id": "jv_guidelines_cpwd",
        "name": "JV / Consortium Guidelines",
        "short": "JV Guidelines",
        "category": "JV",
        "applies_to": ["jv", "joint venture", "consortium", "sub-contracting", "partner"],
        "key_provisions": [
            "JV/Consortium criteria should be clearly specified in RFP",
            "Lead member typically required to meet 50-60% of eligibility criteria",
            "Consortium members jointly and severally liable",
            "Sub-contracting limits typically 25-49% of contract value",
            "Experience of all members can be combined if allowed by RFP",
        ],
        "authority": "Standard procurement practice / CVC guidelines",
        "cite_as": "Standard JV/Consortium Guidelines as per RFP terms and CVC best practices",
    },
    {
        "id": "make_in_india",
        "name": "Make in India / Public Procurement Order 2017",
        "short": "MII Order 2017",
        "category": "MII",
        "applies_to": ["make in india", "domestic", "local content", "mii", "class 1", "class 2"],
        "key_provisions": [
            "Class 1 local supplier: >50% local content — gets purchase preference",
            "Class 2 local supplier: 20-50% local content",
            "Non-local suppliers disqualified if sufficient local supply available",
            "IT/software products have specific local content definitions",
            "Bidder to submit self-certification of local content",
        ],
        "authority": "DPIIT / Ministry of Commerce & Industry",
        "cite_as": "Public Procurement (Preference to Make in India) Order 2017 (as amended)",
    },
    {
        "id": "gfr_security_deposit",
        "name": "Security Deposit / PBG Rules",
        "short": "GFR PBG Rules",
        "category": "Security",
        "applies_to": ["performance guarantee", "security deposit", "pbg", "performance bank guarantee", "bank guarantee"],
        "key_provisions": [
            "PBG typically 3-10% of contract value",
            "PBG validity: contract period + 3-6 months",
            "BG to be from a scheduled bank",
            "Extension of BG if project delayed",
            "Forfeiture conditions must be specifically stated in RFP",
        ],
        "authority": "GFR 2017, Rule 177 / Standard contract terms",
        "cite_as": "GFR 2017 Rule 177 / Standard Security Deposit provisions",
    },
    {
        "id": "it_empanelment",
        "name": "IT Services Empanelment / NICSI / NIC",
        "short": "IT Empanelment",
        "category": "Empanelment",
        "applies_to": ["empanelment", "panel", "nic", "nicsi", "meity empanelled", "cert-in", "stqc"],
        "key_provisions": [
            "NIC/NICSI empanelment is not mandatory for all IT tenders",
            "CERT-In empanelment required only for specific security audit work",
            "STQC empanelment for specific quality testing work",
            "Some tenders accept ISO 27001 as alternative to CERT-In for general IT security",
            "Sub-contracting to empanelled firms often acceptable",
        ],
        "authority": "MeitY / NIC / CERT-In",
        "cite_as": "MeitY IT Services Empanelment Policy / CERT-In Empanelment Scheme",
    },
]


def load_custom_guidelines() -> List[Dict]:
    """Load user-added custom guidelines from file."""
    try:
        CUSTOM_GUIDELINES_FILE.parent.mkdir(exist_ok=True, parents=True)
        if CUSTOM_GUIDELINES_FILE.exists():
            return json.loads(CUSTOM_GUIDELINES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def save_custom_guidelines(guidelines: List[Dict]):
    """Save custom guidelines to file."""
    CUSTOM_GUIDELINES_FILE.parent.mkdir(exist_ok=True, parents=True)
    CUSTOM_GUIDELINES_FILE.write_text(json.dumps(guidelines, indent=2), encoding="utf-8")


def get_all_guidelines() -> List[Dict]:
    """Return all guidelines — built-in + custom."""
    return BUILTIN_GUIDELINES + load_custom_guidelines()


def add_custom_guideline(name: str, short: str, category: str,
                          applies_to: List[str], key_provisions: List[str],
                          authority: str, cite_as: str) -> Dict:
    """Add a new custom guideline."""
    custom = load_custom_guidelines()
    new_gl = {
        "id": f"custom_{len(custom)+1}",
        "name": name,
        "short": short,
        "category": category,
        "applies_to": [k.lower() for k in applies_to],
        "key_provisions": key_provisions,
        "authority": authority,
        "cite_as": cite_as,
        "is_custom": True,
    }
    custom.append(new_gl)
    save_custom_guidelines(custom)
    return new_gl


def find_relevant_guidelines(text: str) -> List[Dict]:
    """
    Find guidelines relevant to the given text.
    Returns list of matching guidelines with relevance score.
    """
    text_lower = text.lower()
    results = []
    for gl in get_all_guidelines():
        score = 0
        for kw in gl.get("applies_to", []):
            if kw in text_lower:
                score += 1
        if score > 0:
            results.append({**gl, "_relevance": score})
    results.sort(key=lambda x: -x["_relevance"])
    return results


def get_guideline_for_query(query_context: str) -> str:
    """
    Return the most relevant guideline citation for a pre-bid query.
    Returns formatted citation string or empty string.
    """
    matches = find_relevant_guidelines(query_context)
    if not matches:
        return ""
    best = matches[0]
    cite = best.get("cite_as", best.get("name", ""))
    provisions = best.get("key_provisions", [])
    if provisions:
        return f"{cite} — {provisions[0]}"
    return cite


def format_guideline_for_prompt(guidelines: List[Dict]) -> str:
    """Format guidelines for injection into AI prompts."""
    if not guidelines:
        return "No specific guidelines found."
    lines = []
    for gl in guidelines[:5]:  # Top 5 most relevant
        lines.append(f"[{gl['short']}] {gl['cite_as']}")
        for prov in gl.get("key_provisions", [])[:2]:
            lines.append(f"  • {prov}")
    return "\n".join(lines)
