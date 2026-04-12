"""
BOQ Engine v1 - Bill of Quantities for NIT Bid/No-Bid System
- Auto-generates BOQ line items from RFP scope (extracted by AI analyzer)
- All prices blank — filled manually by department heads
- Margin % auto-calculates totals
- Stored per tender in tenders_db.json
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any

# ── CATEGORY DEFINITIONS ────────────────────────────────────────────────────
CATEGORIES = [
    "Manpower",
    "Software / Licenses",
    "Hardware",
    "Cloud / Hosting",
    "Training",
    "AMC / Support",
    "Survey / Field Work",
    "Data / GIS",
    "Travel & Logistics",
    "Miscellaneous",
]

UNIT_TYPES = [
    "Months",
    "Nos",
    "Lumpsum",
    "Sq.Km",
    "Per User",
    "Per Site",
    "Per Year",
    "Per Day",
    "Per License",
    "Per Module",
]

# ── MANPOWER ROLES MASTER ───────────────────────────────────────────────────
MANPOWER_ROLES = [
    "Project Manager",
    "GIS Expert / Lead",
    "GIS Developer",
    "Software Developer (Backend)",
    "Software Developer (Frontend)",
    "Mobile App Developer",
    "Database Administrator",
    "System Architect",
    "Business Analyst",
    "Quality Assurance Engineer",
    "Network / Infrastructure Engineer",
    "Support Engineer",
    "Data Entry Operator",
    "Survey Supervisor",
    "Field Survey Staff",
    "Training Coordinator",
]

# ── KEYWORD → CATEGORY MAPPING ──────────────────────────────────────────────
KEYWORD_CATEGORY_MAP = {
    # Manpower
    "project manager": ("Manpower", "Project Manager", "Months"),
    "gis expert": ("Manpower", "GIS Expert / Lead", "Months"),
    "gis developer": ("Manpower", "GIS Developer", "Months"),
    "developer": ("Manpower", "Software Developer (Backend)", "Months"),
    "frontend": ("Manpower", "Software Developer (Frontend)", "Months"),
    "mobile": ("Manpower", "Mobile App Developer", "Months"),
    "dba": ("Manpower", "Database Administrator", "Months"),
    "qa": ("Manpower", "Quality Assurance Engineer", "Months"),
    "business analyst": ("Manpower", "Business Analyst", "Months"),
    "system architect": ("Manpower", "System Architect", "Months"),
    "support engineer": ("Manpower", "Support Engineer", "Months"),
    "data entry": ("Manpower", "Data Entry Operator", "Months"),
    "survey staff": ("Manpower", "Field Survey Staff", "Months"),
    "survey supervisor": ("Manpower", "Survey Supervisor", "Months"),

    # Software
    "arcgis": ("Software / Licenses", "ArcGIS License", "Per Year"),
    "qgis": ("Software / Licenses", "QGIS (Open Source)", "Lumpsum"),
    "geoserver": ("Software / Licenses", "GeoServer Setup", "Lumpsum"),
    "license": ("Software / Licenses", "Software License", "Per Year"),
    "software": ("Software / Licenses", "Application Software", "Lumpsum"),
    "ssl": ("Software / Licenses", "SSL Certificate", "Per Year"),
    "domain": ("Software / Licenses", "Domain Registration", "Per Year"),

    # Hardware
    "server": ("Hardware", "Server (Application/DB)", "Nos"),
    "workstation": ("Hardware", "Workstation", "Nos"),
    "laptop": ("Hardware", "Laptop", "Nos"),
    "gps": ("Hardware", "GPS Device", "Nos"),
    "tablet": ("Hardware", "Tablet / Mobile Device", "Nos"),
    "ups": ("Hardware", "UPS", "Nos"),
    "storage": ("Hardware", "Storage / NAS", "Nos"),

    # Cloud / Hosting
    "cloud": ("Cloud / Hosting", "Cloud Hosting (AWS/Azure)", "Per Year"),
    "hosting": ("Cloud / Hosting", "Web Hosting", "Per Year"),
    "bandwidth": ("Cloud / Hosting", "Internet Bandwidth", "Per Year"),
    "amc": ("AMC / Support", "AMC / Annual Support", "Per Year"),
    "maintenance": ("AMC / Support", "Maintenance & Support", "Per Year"),

    # Training
    "training": ("Training", "User Training Program", "Nos"),
    "capacity building": ("Training", "Capacity Building Workshop", "Nos"),

    # Survey
    "survey": ("Survey / Field Work", "Field Survey", "Sq.Km"),
    "drone": ("Survey / Field Work", "Drone Survey", "Sq.Km"),
    "digitization": ("Data / GIS", "Digitization / Data Capture", "Sq.Km"),
    "data migration": ("Data / GIS", "Data Migration", "Lumpsum"),
    "gis data": ("Data / GIS", "GIS Data Preparation", "Lumpsum"),

    # Travel
    "travel": ("Travel & Logistics", "Travel & Conveyance", "Lumpsum"),
}


def extract_boq_from_scope(tender_data: Dict) -> List[Dict]:
    """
    Auto-generate BOQ line items from tender scope_items and other fields.
    All prices set to 0 (blank) — to be filled by department heads.
    """
    items = []
    seen_descriptions = set()

    scope_items = tender_data.get("scope_items", [])
    contract_period = tender_data.get("contract_period", "")
    post_impl = tender_data.get("post_implementation", "")
    tender_name = tender_data.get("tender_name", "") or tender_data.get("brief", "")

    # Extract duration in months from contract period
    duration_months = _extract_months(contract_period)
    support_months = _extract_months(post_impl) if post_impl else 12

    # --- Parse scope items for keywords ---
    full_scope_text = " ".join([str(s) for s in scope_items]).lower()
    full_scope_text += " " + tender_name.lower()

    added_categories = set()

    for keyword, (cat, desc, unit) in KEYWORD_CATEGORY_MAP.items():
        if keyword in full_scope_text and desc not in seen_descriptions:
            qty = duration_months if unit == "Months" else (1 if unit in ["Lumpsum", "Per Year"] else 0)
            items.append(_make_item(
                category=cat,
                description=desc,
                unit=unit,
                qty=qty,
                source="auto"
            ))
            seen_descriptions.add(desc)
            added_categories.add(cat)

    # --- Always add core manpower if nothing was detected ---
    if "Manpower" not in added_categories:
        for role, unit in [("Project Manager", "Months"), ("GIS Developer", "Months"), ("Software Developer (Backend)", "Months")]:
            if role not in seen_descriptions:
                items.append(_make_item("Manpower", role, unit, duration_months or 12, "default"))
                seen_descriptions.add(role)

    # --- Add AMC if post-implementation period mentioned ---
    if post_impl and "AMC / Support" not in added_categories:
        items.append(_make_item("AMC / Support", "Annual Maintenance & Support", "Per Year",
                                max(1, round(support_months / 12)), "auto"))

    # --- Sort by category order ---
    cat_order = {c: i for i, c in enumerate(CATEGORIES)}
    items.sort(key=lambda x: (cat_order.get(x["category"], 99), x["description"]))

    # Add serial numbers
    for i, item in enumerate(items):
        item["sl_no"] = i + 1

    return items


def _make_item(category: str, description: str, unit: str,
               qty: float, source: str) -> Dict:
    return {
        "sl_no": 0,
        "category": category,
        "description": description,
        "unit": unit,
        "qty": qty if qty else 1,
        "rate": 0,          # blank — filled by head
        "amount": 0,        # auto = qty * rate
        "remarks": "",
        "source": source,   # "auto" or "manual"
    }


def _extract_months(text: str) -> int:
    if not text:
        return 12
    text = text.lower()
    # "24 months", "2 years", "18 month"
    m = re.search(r'(\d+)\s*(?:months?)', text)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*(?:years?)', text)
    if m:
        return int(m.group(1)) * 12
    return 12


def calculate_boq_totals(items: List[Dict], margin_pct: float = 0.0,
                          gst_pct: float = 18.0) -> Dict:
    """
    Calculate totals for a list of BOQ items.
    Returns summary dict.
    """
    for item in items:
        try:
            qty = float(item.get("qty") or 0)
            rate = float(item.get("rate") or 0)
            item["amount"] = round(qty * rate, 2)
        except Exception:
            item["amount"] = 0

    base_total = sum(float(i.get("amount", 0)) for i in items)
    margin_amount = round(base_total * margin_pct / 100, 2)
    subtotal = round(base_total + margin_amount, 2)
    gst_amount = round(subtotal * gst_pct / 100, 2)
    grand_total = round(subtotal + gst_amount, 2)

    return {
        "base_total": base_total,
        "margin_pct": margin_pct,
        "margin_amount": margin_amount,
        "subtotal": subtotal,
        "gst_pct": gst_pct,
        "gst_amount": gst_amount,
        "grand_total": grand_total,
        "items": items,
    }


def get_boq_constants() -> Dict:
    """Return category and unit lists for frontend dropdowns."""
    return {
        "categories": CATEGORIES,
        "unit_types": UNIT_TYPES,
        "manpower_roles": MANPOWER_ROLES,
    }
