"""
prebid_generator.py — Pre-bid Query Generator
Generates pre-bid queries based on Nascent profile and tender data.
"""
import json
from pathlib import Path
from typing import List, Dict

PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"

# Standard queries Nascent always asks based on their conditional rules
STANDARD_QUERIES = [
    {
        "clause": "Eligibility",
        "query": "Whether MSME registered bidders are eligible for EMD exemption under Public Procurement Policy for MSMEs Order 2012 (amended 2018)?",
        "trigger": "msme",
        "always": True,
    },
    {
        "clause": "Experience",
        "query": "Whether experience of GIS/IT projects executed for State/Central Government bodies in Gujarat will be considered as similar work experience?",
        "trigger": "gis",
    },
    {
        "clause": "Turnover",
        "query": "Whether average annual turnover of last 3 financial years will be considered, or only last 2 years?",
        "trigger": "turnover",
    },
    {
        "clause": "Consortium",
        "query": "Is a consortium / joint venture arrangement permissible? If yes, what is the maximum number of members and eligibility sharing criteria?",
        "trigger": "consortium",
    },
    {
        "clause": "Local Office",
        "query": "Is it mandatory to have a registered office in the state at the time of bidding, or is a commitment to open one sufficient?",
        "trigger": "local office",
    },
    {
        "clause": "Similar Work",
        "query": "Whether a single similar work order of the specified value OR two/three works cumulatively meeting the total value will be considered?",
        "trigger": "similar work",
    },
    {
        "clause": "Technology",
        "query": "Whether the specification is technology-neutral, allowing use of Open Source GIS platforms (GeoServer, QGIS, PostGIS) in addition to proprietary solutions?",
        "trigger": "gis software",
    },
    {
        "clause": "Employee Count",
        "query": "Whether the minimum employee count requirement includes all categories of staff (technical + non-technical + support)?",
        "trigger": "employee",
    },
]


def generate_prebid_queries(tender_data: Dict) -> List[Dict]:
    """
    Generate pre-bid queries based on tender data.
    Returns list of {clause, query} dicts.
    """
    queries = []

    brief = str(tender_data.get("brief", "") or tender_data.get("tender_name", "")).lower()
    eligibility = str(tender_data.get("eligibility", "")).lower()
    checklist = str(tender_data.get("checklist", "")).lower()
    full_text = " ".join([brief, eligibility, checklist])

    # Load conditional triggers from profile
    try:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        conditional_kw = profile.get("bid_rules", {}).get("conditional", [])
    except Exception:
        conditional_kw = []

    # Always-include queries
    for q in STANDARD_QUERIES:
        if q.get("always"):
            queries.append({"clause": q["clause"], "query": q["query"]})

    # Trigger-based queries
    for q in STANDARD_QUERIES:
        if q.get("always"):
            continue
        trigger = q.get("trigger", "")
        if trigger and trigger in full_text:
            # Avoid duplicates
            if not any(existing["clause"] == q["clause"] for existing in queries):
                queries.append({"clause": q["clause"], "query": q["query"]})

    # Queries from conditional profile rules
    for kw in conditional_kw:
        if kw in full_text:
            if "cert-in" in kw or "stqc" in kw:
                queries.append({
                    "clause": "Certification",
                    "query": "Whether CERT-In/STQC empanelment is mandatory for all team members or only for specific roles? Can the bidder hire empanelled sub-contractors for these activities?",
                })
            elif "office in" in kw or "registered in" in kw:
                queries.append({
                    "clause": "Office Requirement",
                    "query": "Please clarify whether having a registered office in the state is mandatory at the time of bid submission, or can this be established after award?",
                })
            elif ".net" in kw or "asp.net" in kw or "microsoft" in kw:
                queries.append({
                    "clause": "Technology Stack",
                    "query": "Is the Microsoft/.NET technology stack mandatory, or can bidders use equivalent Open Source frameworks (Java Spring Boot, Python FastAPI, React.js)?",
                })
            elif "100 employee" in kw or "200 employee" in kw:
                queries.append({
                    "clause": "Staff Strength",
                    "query": "Whether the minimum staff strength is required at the time of bidding, or is it the capacity to deploy the required team for the project?",
                })

    # AI-generated queries (from analyze_with_gemini result)
    ai_queries = tender_data.get("prebid_queries", [])
    if ai_queries:
        for aq in ai_queries:
            if isinstance(aq, dict) and aq.get("query"):
                # Avoid duplicates by checking for similar clause
                clause = aq.get("clause", "General")
                if not any(existing.get("clause") == clause for existing in queries):
                    queries.append(aq)
            elif isinstance(aq, str) and aq.strip():
                queries.append({"clause": "General", "query": aq})

    return queries[:20]  # Cap at 20 queries
