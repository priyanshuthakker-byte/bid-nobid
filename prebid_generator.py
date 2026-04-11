"""
prebid_generator.py — Pre-bid Query Generator v2
Generates pre-bid queries + ready-to-send DOCX letter.

Rules:
1. Only raise queries where bidder has GAPS (Conditional/Not Met status)
2. Never name Nascent — use "the bidder", "bidding firms"
3. Cite applicable guidelines (GFR, PPP-MSME, CVC, DPIIT)
4. Use RFP-specified query format if one exists
5. Output: query table + formatted letter DOCX
"""
import json
from pathlib import Path
from typing import List, Dict
from datetime import date

PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"

# ── APPLICABLE GUIDELINES ─────────────────────────────────────────
GUIDELINES = {
    "msme_emd": {
        "name": "Public Procurement Policy for MSMEs Order 2012 (amended 2018) & DoE OM dated 16.11.2020",
        "provision": "MSME registered firms are exempted from payment of EMD/Bid Security",
        "trigger_words": ["emd", "earnest money", "bid security", "msme"],
    },
    "technology_neutral": {
        "name": "CVC Guidelines on transparency in public procurement",
        "provision": "Tender specifications should be technology-neutral and not restrict competition by mandating specific proprietary platforms",
        "trigger_words": ["microsoft", ".net", "asp.net", "ms sql", "oracle", "sap", "specific software"],
    },
    "startup": {
        "name": "DPIIT Notification on Startup India (GoI)",
        "provision": "DPIIT-recognized startups are exempted from prior experience and turnover criteria in public procurement",
        "trigger_words": ["startup", "dpiit"],
    },
    "open_source": {
        "name": "MeitY Policy on Open Source Software Adoption in Government (2015)",
        "provision": "Government should prefer Open Source Software; closed/proprietary stack should not be mandated without justification",
        "trigger_words": ["open source", "gis", "qgis", "postgis", "technology stack"],
    },
    "similar_work": {
        "name": "GFR Rule 174 / Standard CVC guidelines on experience criteria",
        "provision": "Similar work experience criteria should be interpreted broadly to encourage competition; both individual and cumulative experience should be considered",
        "trigger_words": ["similar work", "experience", "completed work", "work order"],
    },
    "turnover": {
        "name": "Standard procurement practice per GFR & CVC circulars",
        "provision": "Turnover criteria should allow average of last 2 or 3 financial years, not just last year",
        "trigger_words": ["turnover", "annual turnover", "average turnover"],
    },
}


def _load_profile() -> dict:
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _find_applicable_guideline(query_text: str, criteria_text: str) -> str:
    """Find applicable government guideline for this query."""
    combined = (query_text + " " + criteria_text).lower()
    for key, gl in GUIDELINES.items():
        if any(kw in combined for kw in gl["trigger_words"]):
            return f"{gl['name']} — {gl['provision']}"
    return ""


def generate_prebid_queries(tender_data: Dict) -> List[Dict]:
    """
    Generate pre-bid queries from tender data.
    Only raises queries for gaps (Conditional/Not Met PQ/TQ).
    Uses AI-generated queries from step9 if available.
    Falls back to gap-based heuristic generation.
    """
    queries = []

    # ── PRIORITY 1: Use AI-generated queries from step9 ──────────
    ai_queries = tender_data.get("prebid_queries", [])
    if ai_queries and isinstance(ai_queries, list) and len(ai_queries) > 0:
        # AI already did the work in step9 — use those queries
        for q in ai_queries:
            if not isinstance(q, dict):
                continue
            # Ensure no Nascent name in query text
            query_text = str(q.get("query", "") or "")
            query_text = query_text.replace("Nascent", "the bidder")
            query_text = query_text.replace("Nascent Info Technologies", "the bidder")
            query_text = query_text.replace("NIT", "the bidder")

            # Find applicable guideline if not already set
            guideline = str(q.get("guideline_cited","") or "")
            if not guideline or guideline == "None":
                guideline = _find_applicable_guideline(
                    query_text,
                    str(q.get("rfp_text","") or "")
                )

            queries.append({
                "query_no":           q.get("query_no", f"Q{len(queries)+1}"),
                "priority":           q.get("priority", "MEDIUM"),
                "clause_ref":         q.get("clause_ref", "—"),
                "page_no":            q.get("page_no", "—"),
                "rfp_text":           str(q.get("rfp_text","") or ""),
                "query":              query_text,
                "guideline_cited":    guideline,
                "clarification_sought": str(q.get("clarification_sought","") or ""),
                "gap_addressed":      str(q.get("gap_addressed","") or ""),
            })
        return queries[:15]

    # ── PRIORITY 2: Generate from PQ/TQ gap analysis ─────────────
    pq_criteria = tender_data.get("pq_criteria", [])
    tq_criteria = tender_data.get("tq_criteria", [])

    gap_items = []
    for item in pq_criteria:
        if isinstance(item, dict) and item.get("nascent_status") in ("Conditional", "Not Met"):
            gap_items.append(("PQ", item))
    for item in tq_criteria:
        if isinstance(item, dict) and item.get("nascent_status") in ("Conditional",):
            gap_items.append(("TQ", item))

    q_num = 1
    for item_type, item in gap_items:
        criteria = str(item.get("criteria","") or item.get("eval_criteria","") or "")
        criteria_lower = criteria.lower()
        clause = str(item.get("clause_ref","—") or "—")
        page = str(item.get("page_no","—") or "—")
        remark = str(item.get("nascent_remark","") or "")
        header = str(item.get("clause_header","") or "")
        calc = str(item.get("calculation_shown","") or "")

        query_text = ""
        rfp_text = criteria[:300] if criteria else "—"
        guideline = ""

        # EMD exemption
        if any(kw in criteria_lower for kw in ["emd","earnest money","bid security"]):
            query_text = (
                "Whether MSME-registered bidders are eligible for exemption from "
                "payment of Earnest Money Deposit (EMD) / Bid Security under the "
                "Public Procurement Policy for MSMEs Order 2012 (as amended in 2018)? "
                "If yes, please confirm the documentation required to claim this exemption."
            )
            guideline = _find_applicable_guideline(query_text, criteria)

        # Technology stack
        elif any(kw in criteria_lower for kw in [".net","asp.net","ms sql","microsoft stack","mssql","sql server"]):
            query_text = (
                "Whether the technology platform specification mentioned in the RFP is "
                "mandatory, or whether bidders using equivalent Open Source technology "
                "frameworks (e.g. Java/Spring Boot, Python, React.js, PostgreSQL) that "
                "achieve the same functional outcomes may participate? "
                "Kindly clarify in the interest of technology neutrality and broader competition."
            )
            guideline = _find_applicable_guideline(query_text, criteria)

        # Turnover criteria
        elif any(kw in criteria_lower for kw in ["turnover","annual turnover","average annual"]):
            query_text = (
                "Whether the annual turnover criterion will be evaluated on the basis of "
                "average turnover of the last 2 (two) or 3 (three) financial years, "
                "or only the immediately preceding financial year? "
                "Kindly clarify the computation method to be adopted."
            )
            guideline = _find_applicable_guideline(query_text, criteria)

        # Similar work / experience
        elif any(kw in criteria_lower for kw in ["similar work","similar nature","experience","completed"]):
            query_text = (
                "Whether the similar work experience requirement can be met by "
                "(a) a single completed work order of the requisite value, OR "
                "(b) multiple work orders cumulatively meeting the requisite value? "
                "Also, whether work currently under execution / ongoing (not yet completed) "
                "will be considered for meeting the experience criterion?"
            )
            guideline = _find_applicable_guideline(query_text, criteria)

        # Employee / staff count
        elif any(kw in criteria_lower for kw in ["employee","staff","manpower","minimum.*employees","100 employees","200 employees"]):
            query_text = (
                "Whether the minimum employee/staff strength criteria encompasses all "
                "categories of employees on payroll (technical, non-technical, support, "
                "management), or only technical/IT staff specifically? "
                "Kindly clarify the basis for determination of eligible employee count."
            )
            guideline = _find_applicable_guideline(query_text, criteria)

        # Local office requirement
        elif any(kw in criteria_lower for kw in ["local office","office in","registered office","state office","branch"]):
            query_text = (
                "Whether having a registered/operational office in the state is a "
                "mandatory eligibility criterion at the time of bid submission, or "
                "whether a commitment/undertaking to establish a local office within "
                "a specified period after contract award would be acceptable?"
            )
            guideline = ""

        # CERT-In / STQC
        elif any(kw in criteria_lower for kw in ["cert-in","cert in","stqc","meity empanelled"]):
            query_text = (
                "Whether CERT-In / STQC empanelment is mandatory for the bidding entity itself, "
                "or whether the requirement can be fulfilled by associating/sub-contracting "
                "with CERT-In/STQC empanelled firms for the specific activities requiring such empanelment? "
                "Kindly clarify the scope of this requirement."
            )
            guideline = ""

        # Consortium / JV
        elif any(kw in criteria_lower for kw in ["consortium","joint venture","jv","partner"]):
            query_text = (
                "Kindly confirm whether consortium/joint venture bids are permissible. "
                "If yes, please specify: (a) maximum number of consortium members, "
                "(b) eligibility sharing criteria between lead and associate members, "
                "(c) any restrictions on lead member's minimum qualifying share."
            )
            guideline = ""

        # Generic for remaining conditional items
        else:
            if remark and len(remark) > 20:
                query_text = (
                    f"With reference to the criteria specified under {header or 'this clause'}, "
                    f"kindly clarify the exact scope and interpretation of the requirement: "
                    f"{criteria[:150]}... "
                    f"Specifically, whether the condition applies at bid submission stage "
                    f"or can be fulfilled at contract execution stage."
                )
            else:
                continue  # Skip if we can't form a meaningful query

        if query_text:
            queries.append({
                "query_no":           f"Q{q_num}",
                "priority":           "HIGH" if item.get("nascent_status") == "Not Met" else "MEDIUM",
                "clause_ref":         clause,
                "page_no":            page,
                "rfp_text":           rfp_text,
                "query":              query_text,
                "guideline_cited":    guideline,
                "clarification_sought": f"Written confirmation from the Tender Authority on the above.",
                "gap_addressed":      f"{item_type} — {header or criteria[:40]}",
            })
            q_num += 1

    # ── PRIORITY 3: Always add MSME EMD query if EMD > 0 ─────────
    emd = tender_data.get("emd","")
    emd_val = emd.get("value","") if isinstance(emd, dict) else str(emd or "")
    emd_exemption = tender_data.get("emd_exemption","")
    emd_exempt_val = emd_exemption.get("value","") if isinstance(emd_exemption, dict) else str(emd_exemption or "")

    has_emd_query = any("emd" in str(q.get("query","")).lower() or "earnest" in str(q.get("query","")).lower() for q in queries)
    if emd_val and emd_val not in ("—","0","") and not has_emd_query and "msme" not in str(emd_exempt_val).lower():
        queries.insert(0, {
            "query_no":           "Q1",
            "priority":           "HIGH",
            "clause_ref":         emd.get("clause_ref","—") if isinstance(emd, dict) else "—",
            "page_no":            emd.get("page_no","—") if isinstance(emd, dict) else "—",
            "rfp_text":           f"EMD: {emd_val}",
            "query":              "Whether MSME-registered bidders are exempt from payment of Earnest Money Deposit (EMD) under the Public Procurement Policy for MSMEs Order 2012 (as amended in 2018) and DoE OM F.No.6/18/2019-PPD dated 16.11.2020? If yes, please confirm the documents required to claim exemption.",
            "guideline_cited":    GUIDELINES["msme_emd"]["name"] + " — " + GUIDELINES["msme_emd"]["provision"],
            "clarification_sought": "Written confirmation of MSME EMD exemption and required documentation.",
            "gap_addressed":      "MSME EMD exemption",
        })
        # Re-number
        for i, q in enumerate(queries):
            q["query_no"] = f"Q{i+1}"

    return queries[:15]


def generate_prebid_letter_text(tender_data: Dict, queries: List[Dict]) -> str:
    """Generate pre-bid letter as formatted text (for DOCX generation)."""

    def _val(d, key="value"):
        v = d.get(key, d) if isinstance(d, dict) else d
        return str(v or "—")

    today = date.today().strftime("%d %B %Y")
    tender_no = _val(tender_data.get("tender_no","—"))
    org_name = _val(tender_data.get("org_name","The Authority"))
    tender_name = _val(tender_data.get("tender_name","—"))
    contact = _val(tender_data.get("contact","—"))
    query_deadline = _val(tender_data.get("prebid_query_date","—"))
    estimated_cost = _val(tender_data.get("estimated_cost","—"))
    query_format = str(tender_data.get("prebid_query_format_used","Standard letter format") or "Standard letter format")

    lines = []
    lines.append(f"Date: {today}")
    lines.append("")
    lines.append("To,")
    lines.append(f"{org_name}")
    if contact and contact != "—":
        lines.append(f"Contact: {contact}")
    lines.append("")
    lines.append(f"Subject: Pre-Bid Queries — Ref: {tender_no} — {tender_name[:80]}")
    lines.append("")
    lines.append("Respected Sir/Madam,")
    lines.append("")
    lines.append(f"We refer to the above-mentioned Request for Proposal (RFP/NIT No. {tender_no}) "
                 f"issued by {org_name} for {tender_name[:100]}, "
                 f"with an estimated value of {estimated_cost}.")
    lines.append("")
    lines.append("We are interested in participating in this tender and, upon careful study of "
                 "the RFP document, wish to seek clarification on the following points for the "
                 "purpose of preparing a complete and compliant bid:")
    lines.append("")
    lines.append("─" * 70)

    for q in queries:
        lines.append("")
        lines.append(f"QUERY {q.get('query_no','Q?')} — {q.get('gap_addressed','')}")
        lines.append(f"Reference: Clause {q.get('clause_ref','—')}, Page {q.get('page_no','—')}")
        rfp_text = q.get("rfp_text","")
        if rfp_text and rfp_text != "—":
            lines.append(f"RFP Text: \"{rfp_text[:300]}\"")
        lines.append(f"Query: {q.get('query','')}")
        guideline = q.get("guideline_cited","")
        if guideline and guideline not in ("None","—",""):
            lines.append(f"Applicable Guideline: {guideline}")
        clarification = q.get("clarification_sought","")
        if clarification:
            lines.append(f"Clarification Sought: {clarification}")
        lines.append("─" * 70)

    lines.append("")
    if query_deadline and query_deadline != "—":
        lines.append(f"We request your kind consideration and written response to the above "
                     f"queries at your earliest, preferably by {query_deadline}.")
    else:
        lines.append("We request your kind consideration and written response to the above queries.")
    lines.append("")
    lines.append("Thanking you,")
    lines.append("")
    lines.append("For Nascent Info Technologies Pvt. Ltd.")
    lines.append("")
    lines.append("")
    lines.append("Hitesh Patel")
    lines.append("Chief Administrative Officer")
    lines.append("Nascent Info Technologies Pvt. Ltd.")
    lines.append("A-805, Shapath IV, SG Highway, Prahlad Nagar, Ahmedabad – 380015, Gujarat")
    lines.append("Tel: +91-79-40200400 | Email: nascent.tender@nascentinfo.com")
    lines.append("Web: www.nascentinfo.com")
    lines.append("")
    lines.append("⚠  NOTE: Power of Attorney of Hitesh Patel EXPIRED 31-Mar-2026.")
    lines.append("    Letter must be signed only after POA renewal is complete.")

    return "\n".join(lines)
