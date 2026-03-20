"""
Corrigendum Analyzer
Reads a corrigendum/addendum document and extracts:
- What changed (clause by clause)
- What stayed same ("as per RFP")
- Impact on Nascent's eligibility for each changed clause
- New deadline/dates if changed
"""

import json, re, logging
from typing import Dict, List, Any
from pathlib import Path

logger = logging.getLogger(__name__)
PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"


def build_corrigendum_prompt(corr_text: str, original_tender: Dict) -> str:
    """Build prompt to compare corrigendum against original tender data."""

    # Load Nascent profile for eligibility re-check
    try:
        if PROFILE_PATH.exists():
            p = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            emp_total = p.get("employees", {}).get("total_confirmed", 67)
            turnover_avg = p.get("finance", {}).get("avg_turnover_last_3_fy", 17.18)
            nascent_summary = f"Nascent has {emp_total} employees, avg turnover Rs.{turnover_avg} Cr, CMMI L3, ISO 9001/27001/20000, MSME."
        else:
            nascent_summary = "Nascent has 67 employees, avg turnover Rs.17.18 Cr, CMMI L3, ISO certified, MSME."
    except Exception:
        nascent_summary = "Nascent has 67 employees, avg turnover Rs.17.18 Cr, CMMI L3."

    # Summarise original PQ for comparison
    orig_pq = []
    for item in original_tender.get("pq_criteria", []):
        orig_pq.append({
            "sl_no": item.get("sl_no", ""),
            "clause_ref": item.get("clause_ref", ""),
            "criteria": item.get("criteria", "")[:200],
            "nascent_status": item.get("nascent_status", ""),
        })

    orig_summary = json.dumps({
        "tender_no": original_tender.get("tender_no", ""),
        "bid_submission_date": original_tender.get("bid_submission_date", ""),
        "bid_opening_date": original_tender.get("bid_opening_date", ""),
        "prebid_query_date": original_tender.get("prebid_query_date", ""),
        "estimated_cost": original_tender.get("estimated_cost", ""),
        "emd": original_tender.get("emd", ""),
        "tender_fee": original_tender.get("tender_fee", ""),
        "pq_criteria_summary": orig_pq,
    }, indent=2)

    return f"""You are a senior bid analyst. A corrigendum/addendum has been issued for a tender.

ORIGINAL TENDER DATA:
{orig_summary}

NASCENT INFO TECHNOLOGIES:
{nascent_summary}

CORRIGENDUM DOCUMENT:
{corr_text[:12000]}

Your job:
1. Read the corrigendum carefully
2. For each change, identify:
   - What clause/section it refers to
   - What the ORIGINAL value was (from the original tender data above)
   - What the NEW value is (from the corrigendum)
   - If it says "as per RFP" or "no change" — mark as unchanged
3. Re-evaluate Nascent's eligibility for any PQ criteria that changed
4. Identify if any new documents are required

Return ONLY valid JSON. No markdown. No preamble.

{{
  "corrigendum_no": "e.g. Corrigendum No. 1",
  "corrigendum_date": "date of corrigendum",
  "tender_no": "tender number",
  "summary": "one line summary of what this corrigendum changes",

  "date_changes": {{
    "bid_submission_date": {{
      "changed": true,
      "old_value": "original value or null if not changed",
      "new_value": "new value from corrigendum",
      "note": ""
    }},
    "bid_opening_date": {{
      "changed": false,
      "old_value": null,
      "new_value": null,
      "note": "as per RFP"
    }},
    "prebid_query_date": {{"changed": false, "old_value": null, "new_value": null, "note": ""}},
    "prebid_meeting": {{"changed": false, "old_value": null, "new_value": null, "note": ""}}
  }},

  "financial_changes": {{
    "emd": {{"changed": false, "old_value": null, "new_value": null, "note": ""}},
    "tender_fee": {{"changed": false, "old_value": null, "new_value": null, "note": ""}},
    "estimated_cost": {{"changed": false, "old_value": null, "new_value": null, "note": ""}},
    "performance_security": {{"changed": false, "old_value": null, "new_value": null, "note": ""}}
  }},

  "pq_changes": [
    {{
      "clause_ref": "e.g. Clause 5",
      "parameter": "e.g. Employee Strength",
      "changed": true,
      "old_value": "original requirement e.g. minimum 100 employees",
      "new_value": "revised requirement e.g. minimum 50 employees",
      "note": "exact text from corrigendum",
      "nascent_impact": "POSITIVE / NEGATIVE / NEUTRAL",
      "nascent_old_status": "Conditional",
      "nascent_new_status": "Met",
      "nascent_remark": "With revised requirement of 50, Nascent's 67 employees now meets this criterion."
    }}
  ],

  "scope_changes": [
    {{
      "clause_ref": "",
      "changed": true,
      "old_value": "",
      "new_value": "",
      "note": "",
      "nascent_impact": "POSITIVE / NEGATIVE / NEUTRAL",
      "nascent_remark": ""
    }}
  ],

  "document_changes": [
    {{
      "changed": true,
      "description": "new document added or removed",
      "action_required": "what Nascent needs to do"
    }}
  ],

  "other_changes": [
    {{
      "clause_ref": "",
      "description": "what changed",
      "old_value": "",
      "new_value": "",
      "note": ""
    }}
  ],

  "overall_impact_on_nascent": "POSITIVE / NEGATIVE / NEUTRAL / MIXED",
  "overall_impact_summary": "clear explanation of net effect on Nascent's bid eligibility",
  "action_required": [
    "Specific action Nascent must take as a result of this corrigendum"
  ],
  "verdict_change_recommended": false,
  "verdict_change_reason": "if true, explain why bid verdict should change"
}}"""


def analyze_corrigendum(corr_text: str, original_tender: Dict) -> Dict[str, Any]:
    """
    Main entry point. Takes corrigendum text and original tender data.
    Returns structured diff of what changed.
    """
    from ai_analyzer import get_all_api_keys, get_groq_key, call_gemini, call_groq, clean_json

    all_keys = get_all_api_keys()
    if not all_keys:
        return {"error": "No Gemini API key configured."}

    prompt = build_corrigendum_prompt(corr_text, original_tender)
    response_text = ""

    for key_idx, api_key in enumerate(all_keys):
        try:
            response_text = call_gemini(prompt, api_key)
            result = clean_json(response_text)
            logger.info(f"Corrigendum analysis success with key {key_idx+1}")
            return result
        except json.JSONDecodeError as e:
            try:
                m = re.search(r'\{.*\}', response_text, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
            except Exception:
                pass
            return {"error": f"Invalid JSON from Gemini: {str(e)[:100]}"}
        except Exception as e:
            err = str(e)
            if "quota" in err.lower() or "429" in err or "exhausted" in err.lower():
                continue
            return {"error": err[:200]}

    groq_key = get_groq_key()
    if groq_key:
        try:
            response_text = call_groq(prompt, groq_key)
            return clean_json(response_text)
        except Exception as e:
            logger.error(f"Groq corrigendum failed: {e}")

    return {"error": "All API keys exhausted for corrigendum analysis."}


def apply_corrigendum_to_tender(original_tender: Dict, corr_result: Dict) -> Dict:
    """
    Apply confirmed changes from corrigendum to the original tender data.
    Only updates fields where changed=True.
    Returns updated tender dict with corrigendum history appended.
    """
    updated = dict(original_tender)

    # Update dates
    date_changes = corr_result.get("date_changes", {})
    field_map = {
        "bid_submission_date": "bid_submission_date",
        "bid_opening_date": "bid_opening_date",
        "prebid_query_date": "prebid_query_date",
        "prebid_meeting": "prebid_meeting",
    }
    for corr_key, tender_key in field_map.items():
        change = date_changes.get(corr_key, {})
        if change.get("changed") and change.get("new_value"):
            updated[tender_key] = change["new_value"]

    # Update financial fields
    fin_changes = corr_result.get("financial_changes", {})
    fin_map = {
        "emd": "emd",
        "tender_fee": "tender_fee",
        "estimated_cost": "estimated_cost",
        "performance_security": "performance_security",
    }
    for corr_key, tender_key in fin_map.items():
        change = fin_changes.get(corr_key, {})
        if change.get("changed") and change.get("new_value"):
            updated[tender_key] = change["new_value"]

    # Update PQ criteria statuses where changed
    pq_changes = corr_result.get("pq_changes", [])
    if pq_changes and updated.get("pq_criteria"):
        for pq_change in pq_changes:
            if not pq_change.get("changed"):
                continue
            new_status = pq_change.get("nascent_new_status", "")
            clause_ref = pq_change.get("clause_ref", "")
            for i, criterion in enumerate(updated["pq_criteria"]):
                if (clause_ref and clause_ref in criterion.get("clause_ref", "")) or \
                   (pq_change.get("parameter", "").lower() in criterion.get("criteria", "").lower()):
                    if new_status:
                        from ai_analyzer import normalize_status
                        status, color = normalize_status(new_status)
                        updated["pq_criteria"][i]["nascent_status"] = status
                        updated["pq_criteria"][i]["nascent_color"] = color
                        updated["pq_criteria"][i]["nascent_remark"] = (
                            f"[UPDATED BY CORRIGENDUM {corr_result.get('corrigendum_no', '')}] "
                            + pq_change.get("nascent_remark", "")
                        )
                    break

    # Recalculate overall verdict
    if updated.get("pq_criteria"):
        green = sum(1 for p in updated["pq_criteria"] if p.get("nascent_color") == "GREEN")
        amber = sum(1 for p in updated["pq_criteria"] if p.get("nascent_color") == "AMBER")
        red = sum(1 for p in updated["pq_criteria"] if p.get("nascent_color") == "RED")
        if red > 0:
            verdict, color = "NO-BID RECOMMENDED", "RED"
        elif amber > 2:
            verdict, color = "CONDITIONAL BID", "AMBER"
        elif amber > 0:
            verdict, color = "BID RECOMMENDED", "AMBER"
        else:
            verdict, color = "BID RECOMMENDED", "GREEN"
        if updated.get("overall_verdict"):
            updated["overall_verdict"]["green"] = green
            updated["overall_verdict"]["amber"] = amber
            updated["overall_verdict"]["red"] = red
            updated["overall_verdict"]["verdict"] = verdict
            updated["overall_verdict"]["color"] = color

    # Store corrigendum history
    if "corrigendum_history" not in updated:
        updated["corrigendum_history"] = []
    updated["corrigendum_history"].append({
        "corrigendum_no": corr_result.get("corrigendum_no", ""),
        "corrigendum_date": corr_result.get("corrigendum_date", ""),
        "summary": corr_result.get("summary", ""),
        "overall_impact": corr_result.get("overall_impact_on_nascent", ""),
        "applied_at": __import__("datetime").datetime.now().isoformat(),
        "full_result": corr_result,
    })
    updated["has_corrigendum"] = True
    updated["last_corrigendum_date"] = corr_result.get("corrigendum_date", "")

    return updated
