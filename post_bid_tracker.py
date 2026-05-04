"""
Post-bid Tracker v1.0 — Nascent Info Technologies Bid/No-Bid System

Tracks bid results and generates analytics:
- Record Won / Lost / L1 / L2 position
- Competitor name + gap amount
- Win rate by domain, org, month, year
- Pipeline value tracking
- Financial projection (expected revenue from bids)
"""

import json
import re
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data"
DB_FILE    = OUTPUT_DIR / "tenders_db.json"


# ─────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────

def _load_db() -> dict:
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tenders": {}}


def _save_db(db: dict):
    DB_FILE.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# RESULT RECORDING
# ─────────────────────────────────────────────────────────────────

def record_bid_result(
    t247_id:          str,
    result:           str,       # "Won" | "Lost" | "No-Bid"
    our_quote_cr:     float = 0,
    l1_amount_cr:     float = 0,
    l1_name:          str = "",
    l2_amount_cr:     float = 0,
    our_rank:         int = 0,
    total_bidders:    int = 0,
    reason_lost:      str = "",
    notes:            str = "",
    award_letter_ref: str = "",
) -> dict:
    """
    Record the final result of a submitted bid.

    result: "Won" | "Lost" | "No-Bid" | "Under Evaluation"
    """
    db     = _load_db()
    tender = db["tenders"].get(t247_id, {})

    if not tender:
        return {"success": False, "error": f"Tender {t247_id} not found"}

    # Compute gap
    gap_cr = 0.0
    if result == "Lost" and l1_amount_cr and our_quote_cr:
        gap_cr = round(our_quote_cr - l1_amount_cr, 4)

    # Compute our rank position
    position = ""
    if our_rank == 1:
        position = "L1 (Lowest Bidder)"
    elif our_rank == 2:
        position = "L2"
    elif our_rank == 3:
        position = "L3"
    elif our_rank > 3:
        position = f"L{our_rank}"

    bid_result = {
        "result":           result,
        "recorded_at":      datetime.now().isoformat(),
        "our_quote_cr":     our_quote_cr,
        "l1_amount_cr":     l1_amount_cr,
        "l1_competitor":    l1_name,
        "l2_amount_cr":     l2_amount_cr,
        "our_rank":         our_rank,
        "our_position":     position,
        "total_bidders":    total_bidders,
        "gap_cr":           gap_cr,
        "gap_pct":          round(gap_cr / l1_amount_cr * 100, 1) if l1_amount_cr else 0,
        "reason_lost":      reason_lost,
        "notes":            notes,
        "award_letter_ref": award_letter_ref,
    }

    tender["status"]     = result
    tender["bid_result"] = bid_result
    if result == "Won":
        tender["won_at"]          = datetime.now().isoformat()
        tender["contract_value"]  = our_quote_cr
    elif result == "Lost":
        tender["lost_at"]         = datetime.now().isoformat()

    db["tenders"][t247_id] = tender
    _save_db(db)

    return {
        "success":    True,
        "result":     result,
        "position":   position,
        "gap_cr":     gap_cr,
        "message":    f"Result recorded: {result} for {tender.get('brief','')[:50]}",
    }


# ─────────────────────────────────────────────────────────────────
# ANALYTICS ENGINE
# ─────────────────────────────────────────────────────────────────

def _classify_domain(tender: dict) -> str:
    """Classify tender into a domain for analytics grouping."""
    text = (
        tender.get("brief", "") + " " +
        tender.get("scope_summary", "") + " " +
        tender.get("org_name", "")
    ).lower()

    if any(k in text for k in ["gis", "geospatial", "mapping", "geoinformatics"]):
        return "GIS"
    if any(k in text for k in ["mobile", "android", "ios", "app"]):
        return "Mobile App"
    if any(k in text for k in ["smart city", "smart cities"]):
        return "Smart City"
    if any(k in text for k in ["erp", "enterprise resource"]):
        return "ERP"
    if any(k in text for k in ["portal", "egovernance", "e-governance", "citizen"]):
        return "eGovernance"
    if any(k in text for k in ["survey", "data collection", "enumeration"]):
        return "Survey"
    if any(k in text for k in ["training", "capacity building"]):
        return "Training"
    return "IT Services"


def get_win_loss_analytics() -> dict:
    """Comprehensive analytics across all tenders."""
    db      = _load_db()
    tenders = list(db.get("tenders", {}).values())

    # Filter submitted/won/lost
    completed = [t for t in tenders if t.get("status") in
                 ["Won", "Lost", "Submitted", "Under Evaluation"]]

    won    = [t for t in tenders if t.get("status") == "Won"]
    lost   = [t for t in tenders if t.get("status") == "Lost"]
    active = [t for t in tenders if t.get("status") not in
              ["Won", "Lost", "No-Bid", "Not Interested", "Submitted"]
              and t.get("verdict") in ["BID", "CONDITIONAL"]]

    # Value calculations
    def safe_float(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    won_value  = sum(safe_float(t.get("contract_value") or
                                t.get("estimated_cost_cr")) for t in won)
    lost_value = sum(safe_float(t.get("estimated_cost_cr")) for t in lost)
    active_val = sum(safe_float(t.get("estimated_cost_cr")) for t in active)
    total_bid  = len(won) + len(lost)
    win_rate   = round(won / total_bid * 100, 1) if total_bid > 0 else 0

    # By domain
    domain_stats = {}
    for t in tenders:
        if t.get("status") not in ["Won", "Lost"]:
            continue
        dom = _classify_domain(t)
        if dom not in domain_stats:
            domain_stats[dom] = {"won": 0, "lost": 0, "won_val": 0, "lost_val": 0}
        if t.get("status") == "Won":
            domain_stats[dom]["won"]     += 1
            domain_stats[dom]["won_val"] += safe_float(t.get("contract_value") or
                                                       t.get("estimated_cost_cr"))
        else:
            domain_stats[dom]["lost"]     += 1
            domain_stats[dom]["lost_val"] += safe_float(t.get("estimated_cost_cr"))
    for dom in domain_stats:
        tot = domain_stats[dom]["won"] + domain_stats[dom]["lost"]
        domain_stats[dom]["win_rate"] = round(
            domain_stats[dom]["won"] / tot * 100, 1) if tot else 0

    # By organisation type
    org_type_stats = {}
    for t in tenders:
        if t.get("status") not in ["Won", "Lost"]:
            continue
        org = t.get("org_name", "Unknown")
        org_key = org[:40]
        if org_key not in org_type_stats:
            org_type_stats[org_key] = {"won": 0, "lost": 0}
        if t.get("status") == "Won":
            org_type_stats[org_key]["won"] += 1
        else:
            org_type_stats[org_key]["lost"] += 1
    # Sort by total bids
    org_type_stats = dict(sorted(
        org_type_stats.items(),
        key=lambda x: x[1]["won"] + x[1]["lost"],
        reverse=True
    )[:10])

    # Monthly trend (last 12 months)
    monthly = {}
    for t in tenders:
        result_date = t.get("won_at") or t.get("lost_at") or t.get("submitted_at")
        if not result_date or t.get("status") not in ["Won", "Lost"]:
            continue
        try:
            dt = datetime.fromisoformat(result_date[:10])
            key = dt.strftime("%Y-%m")
        except Exception:
            continue
        if key not in monthly:
            monthly[key] = {"won": 0, "lost": 0, "won_val": 0}
        if t.get("status") == "Won":
            monthly[key]["won"]     += 1
            monthly[key]["won_val"] += safe_float(t.get("contract_value") or
                                                  t.get("estimated_cost_cr"))
        else:
            monthly[key]["lost"] += 1

    # Competitor analysis (from lost bid results)
    competitors = {}
    for t in lost:
        br = t.get("bid_result", {})
        comp = br.get("l1_competitor", "")
        if comp:
            competitors[comp] = competitors.get(comp, 0) + 1
    competitors = dict(sorted(competitors.items(), key=lambda x: x[1], reverse=True)[:10])

    # Average gap on lost bids
    gaps = [t.get("bid_result", {}).get("gap_cr", 0)
            for t in lost if t.get("bid_result", {}).get("gap_cr", 0) > 0]
    avg_gap = round(sum(gaps) / len(gaps), 3) if gaps else 0

    # Reason for loss breakdown
    loss_reasons = {}
    for t in lost:
        reason = t.get("bid_result", {}).get("reason_lost", "Not recorded")
        if reason:
            loss_reasons[reason] = loss_reasons.get(reason, 0) + 1

    # Pipeline value (active BID/CONDITIONAL tenders)
    pipeline_val = sum(safe_float(t.get("estimated_cost_cr")) for t in active)

    return {
        # Summary
        "total_tenders":  len(tenders),
        "total_bid":      total_bid,
        "total_won":      len(won),
        "total_lost":     len(lost),
        "win_rate_pct":   win_rate,

        # Value
        "won_value_cr":   round(won_value, 2),
        "lost_value_cr":  round(lost_value, 2),
        "pipeline_cr":    round(pipeline_val, 2),
        "active_count":   len(active),

        # Gap analysis
        "avg_gap_cr":     avg_gap,
        "competitors":    competitors,
        "loss_reasons":   loss_reasons,

        # Breakdowns
        "by_domain":      domain_stats,
        "by_org":         org_type_stats,
        "monthly_trend":  dict(sorted(monthly.items())[-12:]),  # last 12 months

        # Recent won tenders
        "recent_wins": [
            {
                "t247_id": t.get("t247_id", ""),
                "brief":   t.get("brief", "")[:60],
                "org":     t.get("org_name", ""),
                "value":   t.get("contract_value") or t.get("estimated_cost_cr"),
                "won_at":  t.get("won_at", "")[:10],
            }
            for t in sorted(won, key=lambda x: x.get("won_at",""), reverse=True)[:5]
        ],
    }


def get_pipeline_value() -> dict:
    """Quick pipeline summary for dashboard."""
    db      = _load_db()
    tenders = list(db.get("tenders", {}).values())

    active = [t for t in tenders if
              t.get("verdict") in ["BID", "CONDITIONAL"] and
              t.get("status") not in ["Won","Lost","No-Bid","Not Interested","Submitted"]]

    def safe_float(v):
        try: return float(v or 0)
        except: return 0.0

    return {
        "active_bid_count":  len(active),
        "pipeline_value_cr": round(sum(safe_float(t.get("estimated_cost_cr")) for t in active), 2),
        "submitted_count":   sum(1 for t in tenders if t.get("status") == "Submitted"),
        "won_count_ytd":     sum(1 for t in tenders if
                                 t.get("status") == "Won" and
                                 t.get("won_at", "")[:4] == str(date.today().year)),
        "won_value_cr_ytd":  round(sum(safe_float(t.get("contract_value") or
                                                  t.get("estimated_cost_cr"))
                                   for t in tenders if
                                   t.get("status") == "Won" and
                                   t.get("won_at","")[:4] == str(date.today().year)), 2),
    }


def get_competitor_report() -> list:
    """List of all encountered competitors with win/loss data."""
    db      = _load_db()
    comp_data = {}
    for t in db.get("tenders", {}).values():
        br   = t.get("bid_result", {})
        comp = br.get("l1_competitor", "")
        if not comp:
            continue
        if comp not in comp_data:
            comp_data[comp] = {"times_l1": 0, "tenders": []}
        comp_data[comp]["times_l1"] += 1
        comp_data[comp]["tenders"].append({
            "brief":     t.get("brief", "")[:50],
            "gap_cr":    br.get("gap_cr", 0),
            "our_quote": br.get("our_quote_cr", 0),
            "l1_quote":  br.get("l1_amount_cr", 0),
        })
    return sorted(
        [{"name": k, **v} for k, v in comp_data.items()],
        key=lambda x: x["times_l1"],
        reverse=True
    )
