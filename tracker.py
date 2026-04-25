"""
Bid Tracker - Pipeline, Deadlines, Checklists, Win/Loss
FIXED: Removed dead loop that caused ValueError: too many values to unpack
"""

import json, re
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List

OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
DB_FILE = OUTPUT_DIR / "tenders_db.json"

PIPELINE_STAGES = [
    "Identified", "Analysed", "Pre-bid Sent", "Documents Ready",
    "Submitted", "Under Evaluation", "Won", "Lost", "No-Bid"
]

STAGE_COLORS = {
    "Identified": "#94A3B8", "Analysed": "#3B82F6", "Pre-bid Sent": "#8B5CF6",
    "Documents Ready": "#F59E0B", "Submitted": "#10B981",
    "Under Evaluation": "#06B6D4", "Won": "#059669",
    "Lost": "#EF4444", "No-Bid": "#6B7280",
}

def load_db() -> dict:
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tenders": {}}

def save_db(db: dict):
    DB_FILE.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")

def days_left(deadline_str: str) -> int:
    if not deadline_str:
        return 999
    for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"]:
        try:
            d = datetime.strptime(str(deadline_str).split()[0], fmt).date()
            return (d - date.today()).days
        except Exception:
            continue
    return 999

def get_deadline_alerts() -> List[Dict]:
    db = load_db()
    alerts = []
    for tid, t in db["tenders"].items():
        if t.get("verdict") not in ["BID", "CONDITIONAL"]:
            continue
        if t.get("status") in ["Submitted", "Won", "Lost", "No-Bid"]:
            continue
        dl = days_left(t.get("deadline", ""))
        if 0 <= dl <= 7:
            alerts.append({
                "t247_id": tid,
                "brief": t.get("brief", "")[:80],
                "deadline": t.get("deadline", ""),
                "days_left": dl,
                "verdict": t.get("verdict", ""),
                "status": t.get("status", "Identified"),
            })
    return sorted(alerts, key=lambda x: x["days_left"])

def get_pipeline_stats() -> Dict:
    db = load_db()
    stats = {s: 0 for s in PIPELINE_STAGES}
    for t in db["tenders"].values():
        s = t.get("status", "Identified")
        if s in stats:
            stats[s] += 1
    return stats

def get_win_loss_stats() -> Dict:
    """
    FIXED: Removed dead loop. Only .items() loop is used — gives (tid, dict) correctly.
    """
    db = load_db()
    won = lost = total = 0
    won_value = 0.0
    results = []

    for tid, t in db["tenders"].items():
        status = t.get("status", "")
        outcome = t.get("outcome", "")
        cost = float(t.get("estimated_cost_cr", 0) or 0)
        effective_outcome = outcome or (status if status in ["Won", "Lost", "Submitted"] else "")

        if effective_outcome in ["Won", "Lost", "Submitted", "Disqualified"]:
            total += 1
            if effective_outcome == "Won":
                won += 1
                won_value += float(t.get("outcome_value", 0) or cost or 0)
            elif effective_outcome == "Lost":
                lost += 1
            results.append({
                "t247_id": tid,
                "brief": t.get("brief", t.get("tender_name", ""))[:60],
                "org_name": t.get("org_name", "—"),
                "outcome": effective_outcome,
                "outcome_value": t.get("outcome_value", ""),
                "outcome_competitor": t.get("outcome_competitor", "—"),
                "outcome_date": t.get("outcome_date", t.get("analysed_at", "")),
                "outcome_notes": t.get("outcome_notes", ""),
            })

    win_rate = round(won / total, 3) if total > 0 else 0.0
    results.sort(key=lambda x: x.get("outcome_date", ""), reverse=True)

    return {
        "total": total,
        "won": won,
        "lost": lost,
        "win_rate": win_rate,
        "total_won_value": round(won_value, 2),
        "results": results,
        "history": results,
        "submitted": sum(1 for r in results if r["outcome"] == "Submitted"),
        "disqualified": sum(1 for r in results if r["outcome"] == "Disqualified"),
    }

def generate_doc_checklist(tender_data: Dict) -> List[Dict]:
    checklist = []
    pq = tender_data.get("pq_criteria", [])

    standard = [
        {"id": "std-1",  "label": "Certificate of Incorporation (COI)", "category": "Company", "source": "Company Records", "done": False},
        {"id": "std-2",  "label": "PAN Card — AACCN3670J (self-attested)", "category": "Company", "source": "Company Records", "done": False},
        {"id": "std-3",  "label": "GST Registration Certificate — 24AACCN3670J1ZG (self-attested)", "category": "Company", "source": "Company Records", "done": False},
        {"id": "std-4",  "label": "MSME/Udyam Registration Certificate — UDYAM-GJ-01-0007420", "category": "Company", "source": "Lifetime validity", "done": False},
        {"id": "std-5",  "label": "Audited Balance Sheet FY 2022-23", "category": "Financial", "source": "CA/Accounts", "done": False},
        {"id": "std-6",  "label": "Audited Balance Sheet FY 2023-24", "category": "Financial", "source": "CA/Accounts", "done": False},
        {"id": "std-7",  "label": "Audited Balance Sheet FY 2024-25", "category": "Financial", "source": "CA/Accounts", "done": False},
        {"id": "std-8",  "label": "CA Certificate — Avg Annual IT/ITeS Turnover (3yr avg Rs.17.18 Cr)", "category": "Financial", "source": "CA Anuj J. Sharedalal & Co.", "done": False},
        {"id": "std-9",  "label": "Non-Blacklisting Declaration (on letterhead, notarized)", "category": "Legal", "source": "Prepare + Notarise", "done": False},
        {"id": "std-10", "label": "⚠ Power of Attorney — Hitesh Patel (CAO) — EXPIRED 31-Mar-2026 — MUST RENEW BEFORE SUBMISSION", "category": "Legal", "source": "URGENT: Management action required", "done": False},
        {"id": "std-11", "label": "Covering Letter / Bid Submission Letter on Company Letterhead", "category": "Bid", "source": "Generate from system", "done": False},
        {"id": "std-12", "label": "CMMI V2.0 Level 3 Certificate — valid till 19-Dec-2026 (Benchmark ID: 68617)", "category": "Certification", "source": "CUNIX Infotech Pvt. Ltd.", "done": False},
        {"id": "std-13", "label": "ISO 9001:2015 Certificate — valid till 08-Sep-2028 (Cert: 25EQPE64)", "category": "Certification", "source": "Assurance Quality Certification LLC", "done": False},
        {"id": "std-14", "label": "ISO 27001:2022 Certificate — valid till 08-Sep-2028 (Cert: 25EQPG58)", "category": "Certification", "source": "Assurance Quality Certification LLC", "done": False},
        {"id": "std-15", "label": "ISO 20000-1:2018 Certificate — valid till 08-Sep-2028 (Cert: 25ZQZQ030409IT)", "category": "Certification", "source": "IQCS UK Ltd", "done": False},
        {"id": "std-16", "label": "Employee Strength Declaration (67 employees, all IT/ITeS)", "category": "HR", "source": "Prepare on company letterhead", "done": False},
        {"id": "std-17", "label": "Work Orders + Completion Certificates for qualifying projects", "category": "Experience", "source": "From project files", "done": False},
    ]
    checklist.extend(standard)

    emd = tender_data.get("emd", "")
    if emd and emd not in ["—", "Nil", "Not specified", ""]:
        checklist.append({
            "id": "emd-1",
            "label": f"EMD: {emd} — OR — MSME EMD Exemption Letter with Udyam Certificate",
            "category": "EMD",
            "source": "Finance — raise pre-bid query for MSME exemption written confirmation",
            "done": False
        })

    for i, item in enumerate(pq):
        docs_text = item.get("documents_required", "") or item.get("details", "")
        if not docs_text:
            continue
        docs = re.split(r'[|•\n,]', docs_text)
        for doc in docs:
            doc = doc.strip()
            if len(doc) > 10 and doc not in [d["label"] for d in checklist]:
                category = (
                    "Certification" if any(k in doc.lower() for k in ["cmmi","iso","cert","certificate"]) else
                    "Financial" if any(k in doc.lower() for k in ["turnover","balance","audited","ca cert"]) else
                    "Experience" if any(k in doc.lower() for k in ["work order","completion","client","project"]) else
                    "Document"
                )
                checklist.append({
                    "id": f"pq-{i}-{len(checklist)}",
                    "label": doc[:120],
                    "category": category,
                    "source": f"RFP {item.get('clause_ref','')}",
                    "clause": item.get("clause_ref", ""),
                    "done": False,
                })

    return checklist
