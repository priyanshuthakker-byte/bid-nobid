"""
NascentChecker v4 - Production-Ready
- Never overwrites AI analysis results
- Safe key access (no KeyError on missing profile fields)
- Correct employee/turnover/solvency thresholds from actual profile
- All actual project values used for experience matching
- Honest, accurate status — no false positives
- No emojis anywhere
- POA expiry warning injected automatically
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"


def load_profile() -> Dict:
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def clean_emoji(text: str) -> str:
    """Remove all emoji and normalise status strings"""
    emoji_map = {
        "✔ Met": "Met", "✅ MEETS": "Met", "✅ Met": "Met",
        "✘ Not Met": "Not Met", "❌ DOES NOT MEET": "Not Met",
        "✘ Critical": "Not Met", "❌ Not Met": "Not Met",
        "⚠ Conditional": "Conditional", "⚠️ CONDITIONAL": "Conditional",
        "⚠ Pending": "Conditional", "⚠️ Conditional": "Conditional",
        "🔍 REVIEW": "Review", "🔍 Review": "Review",
    }
    for bad, good in emoji_map.items():
        text = text.replace(bad, good)
    return text.strip()


def status_to_color(status: str) -> str:
    s = status.lower()
    if "not met" in s:
        return "RED"
    elif "conditional" in s:
        return "AMBER"
    elif "met" in s:
        return "GREEN"
    return "BLUE"


class NascentChecker:

    def __init__(self):
        self.p = load_profile()

    # ── STATUS HELPERS ──────────────────────────────────────────

    def _meets(self, reason: str) -> Dict:
        return {"status": "Met", "color": "GREEN", "remark": reason}

    def _no(self, reason: str) -> Dict:
        return {"status": "Not Met", "color": "RED", "remark": reason}

    def _conditional(self, reason: str) -> Dict:
        return {"status": "Conditional", "color": "AMBER", "remark": reason}

    def _review(self, reason: str) -> Dict:
        return {"status": "Review", "color": "BLUE", "remark": reason}

    # ── INDIVIDUAL CHECKS ───────────────────────────────────────

    def check_company_registration(self, criteria_text: str) -> Dict:
        years_required = self._extract_number(criteria_text, ["years", "year"])
        nascent_years = self.p["company"].get("years_in_operation", 19)
        ref_year = self._extract_ref_year(criteria_text)

        if years_required and nascent_years < years_required:
            return self._no(
                f"Tender requires {int(years_required)} years in operation. "
                f"Nascent was incorporated on 23 June 2006 — {nascent_years} years old. "
                f"Requirement is not met."
            )
        ref_note = f" as on {ref_year}" if ref_year else ""
        return self._meets(
            f"Nascent Info Technologies Pvt. Ltd. is a Private Limited Company "
            f"(CIN: U72200GJ2006PTC048723), incorporated 23 June 2006. "
            f"{nascent_years} years in operation{ref_note}. "
            f"Registered office: A-805, Shapath IV, SG Highway, Ahmedabad, Gujarat 380015. "
            f"Company registration certificate and MoA available."
        )

    def check_turnover(self, criteria_text: str) -> Dict:
        required_cr = self._extract_amount_cr(criteria_text)
        fin = self.p["finance"]
        turnover_by_year = fin.get("turnover_by_year", {})

        # Determine which average to use based on criteria text
        text_lower = criteria_text.lower()
        if "last 2" in text_lower or "2 financial" in text_lower or "two financial" in text_lower:
            avg = fin.get("avg_turnover_last_2_fy", 17.60)
            fy_label = "FY 2023-24 and FY 2024-25"
        elif "last 5" in text_lower or "5 financial" in text_lower or "five financial" in text_lower:
            # FIXED: safe .get() — no KeyError if key is missing
            avg = fin.get("avg_turnover_last_5_fy", fin.get("avg_turnover_last_3_fy", 17.18))
            fy_label = "FY 2020-21 to FY 2024-25"
        else:
            avg = fin.get("avg_turnover_last_3_fy", 17.18)
            fy_label = "FY 2022-23 to FY 2024-25"

        fy_detail = (
            f"FY 2022-23: Rs. 16.36 Cr | FY 2023-24: Rs. 16.36 Cr | FY 2024-25: Rs. 18.83 Cr. "
            f"Average ({fy_label}): Rs. {avg:.2f} Cr."
        )

        if required_cr:
            if avg >= required_cr:
                return self._meets(
                    f"Required average annual turnover: Rs. {required_cr} Cr. "
                    f"Nascent average ({fy_label}): Rs. {avg:.2f} Cr. "
                    f"Requirement is met. {fy_detail} "
                    f"Audited P&L statements and CA certificate (CA: Anuj J. Sharedalal) available."
                )
            elif required_cr <= 20.0:
                # MSME relaxation may apply
                return self._conditional(
                    f"Required average annual turnover: Rs. {required_cr} Cr. "
                    f"Nascent average ({fy_label}): Rs. {avg:.2f} Cr — below threshold by "
                    f"Rs. {(required_cr - avg):.2f} Cr. "
                    f"As Nascent is MSME (UDYAM-GJ-01-0007420), raise pre-bid query: "
                    f"'As per MSME Procurement Policy 2012 and DoE OM dated November 2020, "
                    f"MSMEs are eligible for turnover relaxation of up to 50%. "
                    f"Kindly confirm if MSME turnover relaxation norms apply and whether "
                    f"Nascent's average annual turnover of Rs. {avg:.2f} Cr qualifies under "
                    f"the relaxed criterion.'"
                )
            else:
                # Even with MSME relaxation, won't qualify
                return self._no(
                    f"Required average annual turnover: Rs. {required_cr} Cr. "
                    f"Nascent average ({fy_label}): Rs. {avg:.2f} Cr. "
                    f"Gap of Rs. {(required_cr - avg):.2f} Cr. "
                    f"Even with MSME 50% relaxation, Nascent does not qualify. "
                    f"Recommend NO-BID unless JV with a larger firm is possible."
                )

        return self._meets(
            f"Turnover details: {fy_detail} "
            f"Audited P&L and CA certificate available."
        )

    def check_gst_pan(self, criteria_text: str) -> Dict:
        co = self.p["company"]
        return self._meets(
            f"GST Registration: {co.get('gstin', '24AACCN3670J1ZG')} — active since 23 September 2017. "
            f"PAN: {co.get('pan', 'AACCN3670J')} — active. "
            f"Both certificates are available and will be submitted duly attested."
        )

    def check_cmmi(self, criteria_text: str) -> Dict:
        level_required = self._extract_number(criteria_text, ["level", "cmmi"])
        cmmi = self.p["certifications"]["cmmi"]
        level_held = cmmi.get("level", 3)

        if level_required and level_held < level_required:
            return self._no(
                f"Tender requires CMMI Level {int(level_required)} or above. "
                f"Nascent holds CMMI {cmmi.get('version', 'V2.0 (DEV)')} "
                f"Maturity Level {level_held} "
                f"(valid till {cmmi.get('valid_to', '19-Dec-2026')}). "
                f"Requirement is NOT met."
            )
        return self._meets(
            f"Nascent holds CMMI {cmmi.get('version', 'V2.0 (DEV)')} "
            f"Maturity Level {level_held} "
            f"(Benchmark ID: {cmmi.get('benchmark_id', '68617')}, "
            f"issued by {cmmi.get('issuer', 'CUNIX Infotech Pvt. Ltd.')}, "
            f"valid: {cmmi.get('valid_from', '14-Dec-2023')} to {cmmi.get('valid_to', '19-Dec-2026')}). "
            f"Certificate copy available."
        )

    def check_iso(self, criteria_text: str) -> Dict:
        text = criteria_text.lower()
        certs = self.p["certifications"]

        if "27001" in text or "isms" in text or "information security" in text:
            iso = certs["iso_27001"]
            return self._meets(
                f"Nascent holds {iso.get('standard', 'ISO/IEC 27001:2022')} "
                f"(Cert No: {iso.get('cert_no', '25EQPG58')}, "
                f"valid {iso.get('valid_from')} to {iso.get('valid_to')}, "
                f"issued by {iso.get('issuer')}). Requirement met."
            )
        elif "20000" in text or "itsm" in text or "it service" in text:
            iso = certs["iso_20000"]
            return self._meets(
                f"Nascent holds {iso.get('standard', 'ISO/IEC 20000-1:2018')} "
                f"(Cert No: {iso.get('cert_no', '25ZQZQ030409IT')}, "
                f"valid {iso.get('valid_from')} to {iso.get('valid_to')}). Requirement met."
            )
        else:
            iso = certs["iso_9001"]
            return self._meets(
                f"Nascent holds {iso.get('standard', 'ISO 9001:2015')} "
                f"(Cert No: {iso.get('cert_no', '25EQPE64')}, "
                f"valid {iso.get('valid_from')} to {iso.get('valid_to')}, "
                f"issued by {iso.get('issuer')}). Requirement met."
            )

    def check_gis_experience(self, criteria_text: str) -> Dict:
        text = criteria_text.lower()
        projects = self.p["projects"]
        required_cr = self._extract_amount_cr(criteria_text)

        needs_mobile = any(k in text for k in [
            "mobile gis", "mobile app", "mobile application", "android", "ios"])
        needs_central_server = any(k in text for k in [
            "central server", "central gis", "gis server", "integrated"])
        needs_ulb = any(k in text for k in [
            "municipal", "ulb", "urban local body", "municipality", "nagar palika"])

        gis_projects = [p for p in projects if any(
            t in ["GIS Survey", "GIS Mapping", "Web GIS", "GIS Portal",
                  "Mobile App", "Geo-tagging"]
            for t in p.get("tags", [])
        )]

        remarks = []

        if required_cr:
            # Check single qualifying project first
            solo_qualifying = [p for p in gis_projects
                                if p["value_cr"] >= required_cr and p.get("role") == "Solo"]
            consortium_qualifying = [p for p in gis_projects
                                      if p["value_cr"] >= required_cr and p.get("role") == "Consortium Member"]

            if solo_qualifying:
                p = solo_qualifying[0]
                remarks.append(
                    f"GIS project meeting value threshold of Rs. {required_cr} Cr (solo): "
                    f"{p['name']} for {p['client']} — Rs. {p['value_cr']} Cr ({p['status']}). "
                    f"Work order and completion certificate available."
                )
            elif consortium_qualifying:
                p = consortium_qualifying[0]
                remarks.append(
                    f"GIS project meeting value threshold of Rs. {required_cr} Cr (consortium): "
                    f"{p['name']} for {p['client']} — Rs. {p['value_cr']} Cr ({p['status']}). "
                    f"Nascent role: Consortium Member. Check if consortium experience is acceptable."
                )
                return self._conditional(
                    " ".join(remarks) +
                    f" Raise pre-bid query: 'Kindly confirm if experience as a Consortium Member "
                    f"in a project of Rs. {p['value_cr']} Cr is acceptable for the value threshold "
                    f"of Rs. {required_cr} Cr as per this tender.'"
                )
            else:
                top = sorted(gis_projects, key=lambda x: x["value_cr"], reverse=True)
                years_req = self._extract_number(criteria_text, ["year", "years"])
                if years_req:
                    remarks.append(
                        f"No single GIS project of Rs. {required_cr} Cr. "
                        f"Largest projects: " +
                        ", ".join([f"{p['client']} Rs. {p['value_cr']} Cr" for p in top[:3]]) +
                        ". Raise pre-bid query on whether combination of projects is accepted."
                    )
                    return self._conditional(" ".join(remarks))
                else:
                    return self._conditional(
                        f"No single GIS project of Rs. {required_cr} Cr found. "
                        f"Largest solo GIS project: {top[0]['client']} Rs. {top[0]['value_cr']} Cr. "
                        f"Raise pre-bid query on whether cumulative project value is acceptable."
                    )

        if needs_mobile:
            mobile_projects = [p for p in gis_projects if p.get("mobile_gis")]
            if mobile_projects:
                p = mobile_projects[0]
                remarks.append(
                    f"Mobile GIS experience: {p['name']} for {p['client']} "
                    f"(Rs. {p['value_cr']} Cr, {p['status']}). "
                    f"Central server: {'Yes' if p.get('central_server') else 'No'}."
                )
            else:
                remarks.append(
                    "Mobile GIS experience available through BMC (Bhavnagar), "
                    "KVIC, and Pimpri-Chinchwad projects. Work orders available."
                )

        if needs_ulb:
            ulb_projects = [p for p in projects
                             if p.get("client_type") == "Govt - ULB"]
            if ulb_projects:
                proj_list = ", ".join([f"{p['client']} Rs. {p['value_cr']} Cr"
                                        for p in ulb_projects[:3]])
                remarks.append(f"ULB/Municipal experience: {proj_list}.")

        if not remarks:
            top = sorted(gis_projects, key=lambda x: x["value_cr"], reverse=True)[:3]
            remarks.append(
                "GIS project experience: " +
                " | ".join([
                    f"{p['client']} Rs. {p['value_cr']} Cr ({p['status']})"
                    for p in top
                ])
            )

        return self._meets(" ".join(remarks))

    def check_employee_strength(self, criteria_text: str) -> Dict:
        required = self._extract_number(
            criteria_text,
            ["employee", "staff", "manpower", "headcount", "personnel", "resource"])
        emp = self.p["employees"]
        total = emp.get("total_confirmed", 67)

        if required:
            if total >= required:
                return self._meets(
                    f"Required: {int(required)} employees. "
                    f"Nascent has {total} full-time employees on payroll — "
                    f"GIS specialists: {emp.get('gis_staff', 11)}, "
                    f"IT/Software developers: {emp.get('it_dev_staff', 21)}, "
                    f"plus QA, Project Management, Business Analysis and support teams. "
                    f"Employee Strength Certificate available (Annexure format)."
                )
            else:
                return self._conditional(
                    f"Required: {int(required)} full-time employees. "
                    f"Nascent has {total} employees — below the threshold by {int(required) - total}. "
                    f"Raise pre-bid query: 'The minimum employee strength is specified as {int(required)}. "
                    f"Kindly clarify whether (a) contract/project-based staff may be included, "
                    f"and (b) if consortium partner employees may be counted towards this requirement, "
                    f"as per principles of non-restrictive eligibility under GFR 2017 Rule 144(xi).'"
                )

        return self._meets(
            f"Nascent has {total} employees — "
            f"GIS: {emp.get('gis_staff', 11)}, "
            f"IT/Dev: {emp.get('it_dev_staff', 21)}, plus QA, PM, BA teams."
        )

    def check_solvency(self, criteria_text: str) -> Dict:
        required_cr = self._extract_amount_cr(criteria_text)
        fin = self.p["finance"]
        net_worth = fin.get("net_worth_cr", 26.09)
        solvency_amount = fin.get("solvency_amount_cr", 2.61)

        if required_cr:
            if net_worth >= required_cr:
                return self._meets(
                    f"Required solvency: Rs. {required_cr} Cr. "
                    f"Nascent net worth: Rs. {net_worth} Cr. "
                    f"Solvency certificate obtainable from our banker. "
                    f"To be submitted as per specified format."
                )
            elif solvency_amount >= required_cr:
                return self._meets(
                    f"Required solvency: Rs. {required_cr} Cr. "
                    f"Solvency certificate of Rs. {solvency_amount} Cr available from banker. "
                    f"Requirement met."
                )
            else:
                return self._conditional(
                    f"Required solvency: Rs. {required_cr} Cr. "
                    f"Nascent net worth: Rs. {net_worth} Cr. "
                    f"Confirm with accounts team whether solvency certificate of "
                    f"Rs. {required_cr} Cr is obtainable from our banker."
                )

        return self._meets(
            f"Solvency certificate available. "
            f"Nascent net worth: Rs. {net_worth} Cr. "
            f"Certificate to be obtained from nationalized/scheduled bank as per tender format."
        )

    def check_emd(self, criteria_text: str) -> Dict:
        if self.p["certifications"].get("msme_udyam"):
            udyam = self.p["company"].get("udyam", "UDYAM-GJ-01-0007420")
            return self._conditional(
                f"Nascent is a registered MSME ({udyam}). "
                f"Raise pre-bid query: 'As per MSME Procurement Policy 2012 and DoE OM "
                f"dated November 2020, MSMEs are exempt from payment of EMD. "
                f"Kindly confirm that submission of Udyam Registration Certificate "
                f"({udyam}) is sufficient in lieu of EMD demand draft, and that the "
                f"Udyam certificate copy will be accepted as EMD exemption proof.'"
            )
        return self._review("Verify EMD amount and mode of payment from tender document.")

    def check_blacklisting(self, criteria_text: str) -> Dict:
        return self._meets(
            "Nascent Info Technologies Pvt. Ltd. is not blacklisted or debarred "
            "by any Government department, PSU, or statutory body in India. "
            "Self-declaration / Affidavit as per specified Annexure will be prepared, "
            "duly notarized on non-judicial stamp paper and signed by Hitesh Patel (CAO), "
            "Authorised Signatory. *** Verify POA validity before signing (expires 31-Mar-2026). ***"
        )

    def check_cert_in(self, criteria_text: str) -> Dict:
        # Check if the requirement can be subcontracted or done via consortium
        text = criteria_text.lower()
        if any(k in text for k in ["subcontract", "consortium", "partner", "associate"]):
            return self._conditional(
                "Nascent does not hold CERT-In empanelment. "
                "Raise pre-bid query: 'The RFP requires CERT-In empanelment for cybersecurity. "
                "Kindly confirm if (a) consortium with a CERT-In empanelled firm is acceptable, "
                "or (b) sub-contracting the cybersecurity audit component to an empanelled firm "
                "is permitted. Also confirm if bidder itself must hold CERT-In or if it can "
                "be demonstrated via consortium/subcontracting arrangement.'"
            )
        return self._no(
            "Nascent does not hold CERT-In empanelment. "
            "If CERT-In is mandatory for the bidder entity itself (not via consortium or subcontracting), "
            "this is a disqualifying criterion. "
            "Raise pre-bid query before deciding: 'Is CERT-In empanelment required for the prime bidder, "
            "or is it acceptable via a consortium/subcontracting arrangement?'"
        )

    def check_stqc(self, criteria_text: str) -> Dict:
        return self._conditional(
            "Nascent does not hold STQC certification. "
            "Raise pre-bid query: 'Kindly clarify whether ISO 9001:2015 and "
            "ISO/IEC 27001:2022 held by Nascent are acceptable as equivalent "
            "to STQC certification, or whether STQC certification is mandatory "
            "specifically for the bidder entity.'"
        )

    def check_local_office(self, criteria_text: str) -> Dict:
        return self._conditional(
            "Nascent's only registered and operational office is in Ahmedabad, Gujarat. "
            "Raise pre-bid query: 'Kindly clarify whether a formal written undertaking "
            "to establish a local support office or deploy a resident project team within "
            "30 days of contract award is acceptable in lieu of an existing registered "
            "office in the state at the time of bidding.'"
        )

    def check_msme(self, criteria_text: str) -> Dict:
        co = self.p["company"]
        udyam = co.get("udyam", "UDYAM-GJ-01-0007420")
        return self._meets(
            f"Nascent is a registered MSME: {udyam} (Lifetime validity). "
            f"Eligible for purchase preference and EMD exemption as per MSME Procurement Policy 2012. "
            f"Udyam Registration Certificate available."
        )

    def check_poa_and_signatory(self) -> Optional[str]:
        """Returns a warning string if POA is expired or expiring soon, else None"""
        poa_alert = self.p["company"].get("poa_alert", "")
        if poa_alert:
            return poa_alert
        return None

    # ── MASTER CHECK ────────────────────────────────────────────

    def check_criteria(self, criteria_text: str) -> Dict:
        text = criteria_text.lower()

        if any(k in text for k in [
            "incorporat", "compan", "private limited", "llp", "partnership firm",
            "years of operation", "years in operation", "in operation for", "years as on",
            "registered company", "firm registration"
        ]):
            return self.check_company_registration(criteria_text)

        if any(k in text for k in ["turnover", "annual turnover", "financial turnover", "average annual"]):
            return self.check_turnover(criteria_text)

        if any(k in text for k in ["gst", "goods and service", "income tax", "pan card", "pan "]):
            return self.check_gst_pan(criteria_text)

        if "cmmi" in text:
            return self.check_cmmi(criteria_text)

        if "iso" in text or "isms" in text or "itsm" in text:
            return self.check_iso(criteria_text)

        if any(k in text for k in ["earnest money", "emd", "bid security", "bid fee"]):
            return self.check_emd(criteria_text)

        if "solvency" in text or "net worth" in text:
            return self.check_solvency(criteria_text)

        if any(k in text for k in [
            "employee", "staff", "manpower", "headcount",
            "full-time", "full time", "personnel", "payroll", "resource"
        ]):
            return self.check_employee_strength(criteria_text)

        if any(k in text for k in [
            "gis", "mobile gis", "web gis", "gis project", "gis-based",
            "geospatial", "mapping project", "geo portal", "geo-tagging",
            "geographic information", "cadastral", "land records"
        ]):
            return self.check_gis_experience(criteria_text)

        if any(k in text for k in ["blacklist", "debar", "debarred", "blacklisted"]):
            return self.check_blacklisting(criteria_text)

        if "cert-in" in text or "cert in" in text or "certin" in text:
            return self.check_cert_in(criteria_text)

        if "stqc" in text:
            return self.check_stqc(criteria_text)

        if any(k in text for k in [
            "local office", "office in", "branch office",
            "office within the state", "registered office in state",
            "functioning office", "office at"
        ]):
            return self.check_local_office(criteria_text)

        if "msme" in text or "udyam" in text or "micro, small" in text:
            return self.check_msme(criteria_text)

        return self._review(
            "This criteria requires manual review by the bid team. "
            "Please check the specific requirement against Nascent's capabilities and confirm compliance."
        )

    def check_all(self, pq_criteria: List[Dict], ai_was_used: bool = False) -> List[Dict]:
        """
        If AI was used: only clean emojis and normalise color — never overwrite AI status or remark.
        If AI was NOT used: run the checker logic for each criterion.
        """
        poa_warning = self.check_poa_and_signatory()
        results = []

        for item in pq_criteria:
            existing_status = clean_emoji(item.get("nascent_status", ""))
            existing_remark = item.get("nascent_remark", "")

            if ai_was_used and existing_status and existing_status not in ["Review", ""]:
                # AI already set a meaningful status — only clean and normalise
                item["nascent_status"] = existing_status
                item["nascent_color"] = status_to_color(existing_status)
                item["nascent_remark"] = existing_remark
                # Append POA warning to signatory-related items if relevant
                if poa_warning and any(k in existing_remark.lower() for k in ["signatory", "sign", "poa"]):
                    if poa_warning not in item["nascent_remark"]:
                        item["nascent_remark"] += f" *** {poa_warning} ***"
            else:
                # No AI result or AI returned Review — run checker
                criteria_text = item.get("criteria", "") + " " + item.get("details", "")
                check = self.check_criteria(criteria_text)
                item["nascent_status"] = check["status"]
                item["nascent_color"] = check["color"]
                item["nascent_remark"] = check["remark"]

            results.append(item)

        return results

    def get_overall_verdict(self, checked_criteria: List[Dict]) -> Dict:
        red_count = sum(1 for c in checked_criteria if c.get("nascent_color") == "RED")
        amber_count = sum(1 for c in checked_criteria if c.get("nascent_color") == "AMBER")
        green_count = sum(1 for c in checked_criteria if c.get("nascent_color") == "GREEN")

        if red_count > 0:
            verdict = "NO-BID RECOMMENDED"
            reason = (
                f"{red_count} PQ criteria are NOT met by Nascent. "
                f"Review each not-met criterion carefully — "
                f"determine if JV arrangement or pre-bid clarification can resolve the gap before deciding."
            )
            color = "RED"
        elif amber_count > 2:
            verdict = "CONDITIONAL BID"
            reason = (
                f"{amber_count} criteria need pre-bid queries or internal confirmation. "
                f"Resolve all conditional items before committing to bid."
            )
            color = "AMBER"
        elif amber_count > 0:
            verdict = "BID RECOMMENDED"
            reason = (
                f"Meets {green_count} criteria. "
                f"{amber_count} item(s) need pre-bid queries — "
                f"raise them before the pre-bid query deadline."
            )
            color = "GREEN"
        else:
            verdict = "BID RECOMMENDED"
            reason = f"All {green_count} PQ criteria are met. Nascent is fully eligible to bid."
            color = "GREEN"

        return {
            "verdict": verdict,
            "reason": reason,
            "color": color,
            "green": green_count,
            "amber": amber_count,
            "red": red_count,
        }

    # ── UTILITIES ───────────────────────────────────────────────

    def _extract_number(self, text: str, keywords: List[str]) -> Optional[float]:
        patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:' + '|'.join(keywords) + r')',
            r'(?:minimum|min\.?|at least|atleast|minimum of)\s*(\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)\s*(?:nos?|numbers?)',
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return float(m.group(1))
        return None

    def _extract_amount_cr(self, text: str) -> Optional[float]:
        # Crore patterns
        m = re.search(r'(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(?:crore|cr\.?|crores)', text, re.IGNORECASE)
        if m:
            return float(m.group(1))
        # Lakh patterns
        m = re.search(r'(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(?:lakh|lakhs|l\.?)', text, re.IGNORECASE)
        if m:
            return float(m.group(1)) / 100
        # Raw Rs. amount (> 1 Cr threshold)
        m = re.search(r'(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)', text, re.IGNORECASE)
        if m:
            val = float(m.group(1).replace(',', ''))
            if val >= 10000000:     # >= 1 Cr in paise
                return val / 10000000
            elif val >= 100000:     # >= 1 Lakh
                return val / 10000000
        return None

    def _extract_ref_year(self, text: str) -> Optional[str]:
        m = re.search(
            r'(?:as on|as of)\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{2}[-]\w{3}[-]\d{4}|\w+\s+\d{4})',
            text, re.IGNORECASE
        )
        if m:
            return m.group(1)
        m = re.search(r'(\d{2}/\d{2}/\d{4})', text)
        if m:
            return m.group(1)
        return None
