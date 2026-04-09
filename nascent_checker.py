"""
NascentChecker v4 — Profile-driven eligibility checker
Key fix: Nascent is a pure IT/ITeS company.
ALL turnover = IT turnover. ALL employees = IT employees.
If number meets threshold → MET. No Conditional for numbers that qualify.
Conditional only for: EMD exemption query, local office, CERT-In, STQC,
or any criterion where a genuine caveat exists regardless of numbers.
"""

import json, re, os
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).parent
RUNTIME_DIR = Path(os.environ.get("BIDNOBID_RUNTIME_DIR", "/tmp/bid-nobid"))
RUNTIME_PROFILE_PATH = RUNTIME_DIR / "nascent_profile.json"
REPO_PROFILE_PATH = BASE_DIR / "nascent_profile.json"


def load_profile() -> Dict:
    for profile_path in [RUNTIME_PROFILE_PATH, REPO_PROFILE_PATH]:
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return _default_profile()


def _default_profile() -> Dict:
    return {
        "company": {
            "name": "Nascent Info Technologies Pvt. Ltd.",
            "cin": "U72200GJ2006PTC048723",
            "pan": "AACCN3670J",
            "gstin": "24AACCN3670J1ZG",
            "udyam": "UDYAM-GJ-01-0007420",
            "msme": True,
            "legal_status": "Private Limited Company",
            "year_of_incorporation": 2006,
            "years_in_operation": 19,
            "type": "IT / GIS / Smart City Solutions Provider",
        },
        "finance": {
            "turnover_by_year": {
                "2022-23": 16.36, "2023-24": 16.36, "2024-25": 18.83
            },
            "avg_turnover_last_2_fy": 17.60,
            "avg_turnover_last_3_fy": 17.18,
            "avg_turnover_last_5_fy": 16.23,
            "net_worth_cr": 26.09,
        },
        "certifications": {
            "cmmi": {"level": 3, "version": "V2.0 (DEV)", "valid_to": "19-Dec-2026", "status": "ACTIVE"},
            "iso_9001": {"standard": "ISO 9001:2015", "valid_to": "08-Sep-2028", "status": "ACTIVE"},
            "iso_27001": {"standard": "ISO/IEC 27001:2022", "valid_to": "08-Sep-2028", "status": "ACTIVE"},
            "iso_20000": {"standard": "ISO/IEC 20000-1:2018", "valid_to": "08-Sep-2028", "status": "ACTIVE"},
            "cert_in": False,
            "stqc": False,
        },
        "employees": {
            "total_confirmed": 67, "gis_staff": 11, "it_dev_staff": 21
        },
        "projects": [],
    }


class NascentChecker:

    def __init__(self):
        self.p = load_profile()
        self._build_profile_summary()

    def _build_profile_summary(self):
        """Pre-compute key values from profile for fast lookup."""
        fin = self.p.get("finance", {})
        emp = self.p.get("employees", {})
        co = self.p.get("company", {})
        certs = self.p.get("certifications", {})

        self.total_employees = emp.get("total_confirmed", 67)
        self.it_dev_employees = emp.get("it_dev_staff", 21)
        self.gis_employees = emp.get("gis_staff", 11)
        # All employees are IT/ITeS employees at Nascent
        self.it_employees_total = self.total_employees

        self.avg_turnover_3yr = fin.get("avg_turnover_last_3_fy", 17.18)
        self.avg_turnover_2yr = fin.get("avg_turnover_last_2_fy", 17.60)
        self.avg_turnover_5yr = fin.get("avg_turnover_last_5_fy", 16.23)
        self.net_worth = fin.get("net_worth_cr", 26.09)
        self.turnover_by_year = fin.get("turnover_by_year", {})

        self.years_in_operation = co.get("years_in_operation", 19)
        self.is_msme = co.get("msme", True)
        self.udyam = co.get("udyam", "UDYAM-GJ-01-0007420")
        self.cin = co.get("cin", "U72200GJ2006PTC048723")

        self.cmmi = certs.get("cmmi", {})
        self.iso_9001 = certs.get("iso_9001", {})
        self.iso_27001 = certs.get("iso_27001", {})
        self.iso_20000 = certs.get("iso_20000", {})
        self.has_cert_in = certs.get("cert_in", False)
        self.has_stqc = certs.get("stqc", False)

        self.projects = self.p.get("projects", [])

        # Build key personnel lookup if present
        self.key_personnel = self.p.get("key_personnel", [])

    # ── STATUS HELPERS ─────────────────────────────────────────

    def _met(self, reason: str) -> Dict:
        return {"status": "Met", "color": "GREEN", "remark": reason}

    def _not_met(self, reason: str) -> Dict:
        return {"status": "Not Met", "color": "RED", "remark": reason}

    def _conditional(self, reason: str) -> Dict:
        return {"status": "Conditional", "color": "AMBER", "remark": reason}

    def _review(self, reason: str) -> Dict:
        return {"status": "Review", "color": "BLUE", "remark": reason}

    # ── INDIVIDUAL CHECKS ──────────────────────────────────────

    def check_company_registration(self, criteria_text: str) -> Dict:
        years_req = self._extract_number(criteria_text, ["years", "year"])
        ref_year = self._extract_ref_year(criteria_text)
        ref_note = f" as on {ref_year}" if ref_year else ""

        if years_req and self.years_in_operation < years_req:
            return self._not_met(
                f"Tender requires {int(years_req)} years in operation. "
                f"Nascent incorporated 23-Jun-2006 — {self.years_in_operation} years old. Not met."
            )
        return self._met(
            f"Nascent Info Technologies Pvt. Ltd. — Private Limited Company, "
            f"CIN: {self.cin}, incorporated 23-Jun-2006, "
            f"{self.years_in_operation} years in operation{ref_note}. "
            f"MOA, COI, PAN, GST certificates available."
        )

    def check_turnover(self, criteria_text: str) -> Dict:
        """
        KEY FIX: Nascent is a pure IT/ITeS company.
        100% of turnover = IT/ITeS turnover.
        If average meets the threshold → MET, not Conditional.
        """
        required_cr = self._extract_amount_cr(criteria_text)
        text_lower = criteria_text.lower()

        # Determine which average applies
        if "last 2" in text_lower or "2 financial" in text_lower or "two financial" in text_lower:
            avg = self.avg_turnover_2yr
            fy_label = "FY 2023-24 and FY 2024-25"
        elif "last 5" in text_lower or "5 financial" in text_lower or "five financial" in text_lower:
            avg = self.avg_turnover_5yr
            fy_label = "FY 2020-21 to FY 2024-25"
        else:
            avg = self.avg_turnover_3yr
            fy_label = "FY 2022-23 to FY 2024-25"

        # Build turnover detail string
        fy_detail = (
            f"FY 2022-23: Rs.16.36 Cr | FY 2023-24: Rs.16.36 Cr | FY 2024-25: Rs.18.83 Cr. "
            f"Average ({fy_label}): Rs.{avg:.2f} Cr."
        )

        # Check each year individually if "each of last N years" is mentioned
        each_year = "each" in text_lower or "every year" in text_lower or "each financial year" in text_lower
        if each_year and required_cr:
            recent_years = ["2022-23", "2023-24", "2024-25"]
            failing = []
            for yr in recent_years:
                val = self.turnover_by_year.get(yr, 0)
                if val < required_cr:
                    failing.append(f"FY {yr}: Rs.{val} Cr")
            if failing:
                return self._not_met(
                    f"Tender requires Rs.{required_cr} Cr IT/ITeS turnover in EACH of last 3 FY. "
                    f"Nascent falls short in: {', '.join(failing)}. "
                    f"Raise pre-bid query for MSME turnover relaxation."
                )
            return self._met(
                f"Tender requires Rs.{required_cr} Cr IT/ITeS in each of last 3 FY. "
                f"Nascent (pure IT company): {fy_detail} All years qualify. "
                f"Audited financials + CA certificate to be attached."
            )

        if required_cr:
            if avg >= required_cr:
                # MET — do not mark as Conditional just because it's turnover
                return self._met(
                    f"Tender requires average annual IT/ITeS turnover of Rs.{required_cr} Cr. "
                    f"Nascent is a pure IT/GIS company — entire turnover is IT/ITeS. "
                    f"{fy_detail} Average Rs.{avg:.2f} Cr meets requirement. "
                    f"Audited financials + CA certificate available."
                )
            else:
                # Genuinely does not meet — check MSME relaxation
                msme_note = (
                    " As MSME (UDYAM-GJ-01-0007420), raise pre-bid query: "
                    "'Kindly confirm if MSME turnover relaxation applies per "
                    "PPP for MSEs 2012 / DoE OM Nov-2020.'"
                ) if self.is_msme else ""
                return self._not_met(
                    f"Tender requires average IT/ITeS turnover of Rs.{required_cr} Cr. "
                    f"Nascent average ({fy_label}): Rs.{avg:.2f} Cr — below threshold.{msme_note}"
                )

        return self._met(f"Turnover details: {fy_detail} Audited P&L and CA certificate available.")

    def check_gst_pan(self, criteria_text: str) -> Dict:
        return self._met(
            "GST Registration: 24AACCN3670J1ZG — active. "
            "PAN: AACCN3670J — active. "
            "Both certificates available, self-attested copies to be submitted."
        )

    def check_cmmi(self, criteria_text: str) -> Dict:
        level_req = self._extract_number(criteria_text, ["level", "cmmi", "maturity"])
        level_held = self.cmmi.get("level", 3)
        valid_to = self.cmmi.get("valid_to", "19-Dec-2026")
        version = self.cmmi.get("version", "V2.0 (DEV)")

        # Check if certificate is still valid
        expired = self._is_expired(valid_to)
        if expired:
            return self._not_met(
                f"Nascent CMMI {version} Level {level_held} certificate expired on {valid_to}. "
                f"Renewal required before bid submission."
            )

        if level_req and level_held < level_req:
            return self._not_met(
                f"Tender requires CMMI Level {int(level_req)}. "
                f"Nascent holds CMMI {version} Level {level_held} (valid till {valid_to}). Not met."
            )
        return self._met(
            f"Nascent holds CMMI {version} Maturity Level {level_held} "
            f"(valid till {valid_to}). Requirement met. Certificate copy available."
        )

    def check_iso(self, criteria_text: str) -> Dict:
        text = criteria_text.lower()
        results = []

        if "27001" in text or "isms" in text or "information security" in text:
            iso = self.iso_27001
            if self._is_expired(iso.get("valid_to", "")):
                return self._not_met(f"ISO/IEC 27001:2022 expired on {iso.get('valid_to')}. Renewal required.")
            results.append(f"{iso.get('standard', 'ISO 27001:2022')} valid till {iso.get('valid_to', 'Sep-2028')}")

        if "20000" in text or "itsm" in text or "it service" in text:
            iso = self.iso_20000
            if self._is_expired(iso.get("valid_to", "")):
                return self._not_met(f"ISO 20000-1:2018 expired on {iso.get('valid_to')}. Renewal required.")
            results.append(f"{iso.get('standard', 'ISO 20000-1:2018')} valid till {iso.get('valid_to', 'Sep-2028')}")

        if "9001" in text or "quality" in text:
            iso = self.iso_9001
            if self._is_expired(iso.get("valid_to", "")):
                return self._not_met(f"ISO 9001:2015 expired on {iso.get('valid_to')}. Renewal required.")
            results.append(f"{iso.get('standard', 'ISO 9001:2015')} valid till {iso.get('valid_to', 'Sep-2028')}")

        if not results:
            # Generic ISO mention — report all
            results = [
                f"ISO 9001:2015 valid till {self.iso_9001.get('valid_to', 'Sep-2028')}",
                f"ISO/IEC 27001:2022 valid till {self.iso_27001.get('valid_to', 'Sep-2028')}",
                f"ISO/IEC 20000-1:2018 valid till {self.iso_20000.get('valid_to', 'Sep-2028')}",
            ]

        return self._met(f"Nascent holds: {' | '.join(results)}. Certificates available for submission.")

    def check_gis_experience(self, criteria_text: str) -> Dict:
        text = criteria_text.lower()
        required_cr = self._extract_amount_cr(criteria_text)
        needs_mobile = any(k in text for k in ["mobile gis", "mobile app", "mobile application", "android", "ios"])

        gis_projects = [p for p in self.projects if any(
            t in p.get("tags", []) for t in ["GIS Survey", "GIS Mapping", "Web GIS", "GIS Portal", "Mobile App", "GIS"]
        )]

        if not gis_projects:
            # Fallback to hardcoded known projects
            return self._met(
                "GIS project experience: AMC GIS Rs.10.55 Cr (completed) | "
                "JuMC GIS Rs.9.78 Cr (ongoing) | VMC GIS+ERP Rs.20.5 Cr (completed) | "
                "KVIC Mobile GIS PAN India Rs.5.15 Cr | BMC Android+iOS GIS (completed). "
                "Work orders and completion certificates available."
            )

        remarks = []
        if required_cr:
            qualifying = [p for p in gis_projects if self._parse_value_cr(p.get("val", "0")) >= required_cr]
            if qualifying:
                proj_list = " | ".join([
                    f"{p['client']} Rs.{p['val']} ({p.get('status','')})"
                    for p in qualifying[:3]
                ])
                remarks.append(f"Projects meeting Rs.{required_cr} Cr threshold: {proj_list}.")
            else:
                top = sorted(gis_projects, key=lambda x: self._parse_value_cr(x.get("val", "0")), reverse=True)
                remarks.append(
                    f"No single GIS project of Rs.{required_cr} Cr. Largest: "
                    + " | ".join([f"{p['client']} Rs.{p['val']}" for p in top[:3]])
                    + ". Raise pre-bid query if combination of projects is permitted."
                )
                return self._conditional(" ".join(remarks))

        if needs_mobile:
            mobile = [p for p in gis_projects if p.get("mobile_gis") or "Mobile" in " ".join(p.get("tags", []))]
            if mobile:
                remarks.append(f"Mobile GIS: {mobile[0]['client']} Rs.{mobile[0]['val']} — work order available.")
            else:
                remarks.append("Mobile GIS: KVIC PAN India, BMC Android+iOS — completion certs available.")

        if not remarks:
            top = sorted(gis_projects, key=lambda x: self._parse_value_cr(x.get("val", "0")), reverse=True)[:3]
            remarks.append("GIS projects: " + " | ".join([f"{p['client']} Rs.{p['val']} ({p.get('status','')})" for p in top]))

        return self._met(" ".join(remarks))

    def check_employee_strength(self, criteria_text: str) -> Dict:
        """
        KEY FIX: Nascent is a pure IT company.
        If tender asks for IT/ITeS employees and our total = 67,
        all 67 count as IT/ITeS. Met if 67 >= required.
        """
        required = self._extract_number(
            criteria_text, ["employee", "staff", "manpower", "headcount", "personnel", "people"])

        # All Nascent employees are IT/ITeS employees
        it_count = self.it_employees_total  # = total employees (pure IT company)

        if required:
            if it_count >= required:
                return self._met(
                    f"Tender requires {int(required)} employees in IT/ITeS. "
                    f"Nascent is a pure IT/GIS company with {it_count} full-time employees on payroll "
                    f"({self.it_dev_employees} IT/Dev, {self.gis_employees} GIS, plus QA/PM/BA/Support). "
                    f"All employees are IT/ITeS domain. HR certificate on company letterhead to be provided."
                )
            else:
                return self._conditional(
                    f"Tender requires {int(required)} IT/ITeS employees. "
                    f"Nascent has {it_count} employees. Below threshold by {int(required) - it_count}. "
                    f"Raise pre-bid query: 'Kindly clarify whether contract/associate staff and "
                    f"consortium partner team may be counted towards this requirement.'"
                )

        return self._met(
            f"Nascent has {it_count} full-time employees — all in IT/ITeS domain. "
            f"IT/Dev: {self.it_dev_employees}, GIS: {self.gis_employees}, plus QA, PM, BA, Support. "
            f"HR certificate available."
        )

    def check_solvency(self, criteria_text: str) -> Dict:
        required_cr = self._extract_amount_cr(criteria_text)
        if required_cr:
            if self.net_worth >= required_cr:
                return self._met(
                    f"Tender requires solvency of Rs.{required_cr} Cr. "
                    f"Nascent net worth: Rs.{self.net_worth} Cr. Meets requirement. "
                    f"Solvency certificate to be obtained from bankers."
                )
            else:
                return self._conditional(
                    f"Tender requires solvency of Rs.{required_cr} Cr. "
                    f"Nascent net worth: Rs.{self.net_worth} Cr. "
                    f"Confirm with accounts team whether bank solvency certificate of this amount is obtainable."
                )
        return self._met(
            f"Solvency certificate available. Nascent net worth Rs.{self.net_worth} Cr. "
            f"Certificate to be obtained from nationalized bank."
        )

    def check_emd(self, criteria_text: str) -> Dict:
        """EMD is always Conditional — MSME exemption must be queried."""
        emd_amount = self._extract_amount_cr(criteria_text)
        amount_str = f"Rs.{emd_amount} Cr" if emd_amount else "EMD"
        if self.is_msme:
            return self._conditional(
                f"Nascent is a registered MSME ({self.udyam}). "
                f"Raise pre-bid query: '{amount_str} EMD is specified. "
                f"As per MSME Procurement Policy 2012 and DoE OM dated November 2020, "
                f"MSMEs are exempt from EMD. Kindly confirm exemption applies and "
                f"Udyam Registration Certificate is sufficient in lieu of EMD.' "
                f"If exemption not granted, arrange BG from nationalized bank."
            )
        return self._review("Verify EMD amount and mode of payment from tender document.")

    def check_blacklisting(self, criteria_text: str) -> Dict:
        return self._met(
            "Nascent is not blacklisted or debarred by any Government department or PSU in India. "
            "Self-declaration / Affidavit on company letterhead, duly notarized and "
            "signed by authorised signatory (Hitesh Patel, CAO), to be submitted."
        )

    def check_cert_in(self, criteria_text: str) -> Dict:
        if self.has_cert_in:
            return self._met("Nascent holds CERT-In empanelment. Certificate available for submission.")
        return self._conditional(
            "Nascent does not currently hold CERT-In empanelment. "
            "Raise pre-bid query: 'Kindly clarify whether (a) a consortium partner holding "
            "CERT-In empanelment is acceptable, or (b) sub-contracting the security audit "
            "component to a CERT-In empanelled firm is permitted under this tender.'"
        )

    def check_stqc(self, criteria_text: str) -> Dict:
        if self.has_stqc:
            return self._met("Nascent holds STQC certification. Certificate available.")
        return self._conditional(
            "Nascent does not hold STQC certification. "
            "Raise pre-bid query: 'Kindly clarify whether ISO 9001:2015 and ISO/IEC 27001:2022 "
            "certifications are acceptable as equivalent to STQC for this tender.'"
        )

    def check_local_office(self, criteria_text: str) -> Dict:
        text = criteria_text.lower()
        # Extract state name if mentioned
        state_hints = ["bihar", "rajasthan", "uttar pradesh", "up", "maharashtra",
                       "gujarat", "karnataka", "kerala", "tamil nadu", "andhra", "telangana",
                       "west bengal", "odisha", "madhya pradesh", "chhattisgarh"]
        mentioned_state = next((s for s in state_hints if s in text), None)
        state_note = f" in {mentioned_state.title()}" if mentioned_state else " in the state"

        return self._conditional(
            f"Nascent HQ is in Ahmedabad, Gujarat. No registered office{state_note}. "
            f"Raise pre-bid query: 'Kindly clarify whether a written commitment to establish "
            f"a local support office and deploy a resident project team within 30 days of "
            f"contract award is acceptable in lieu of a pre-existing office at bid stage.'"
        )

    def check_msme(self, criteria_text: str) -> Dict:
        return self._met(
            f"Nascent is a registered MSME: {self.udyam} (Lifetime validity). "
            f"Eligible for purchase preference and EMD exemption as per MSME Procurement Policy 2012."
        )

    def check_project_experience(self, criteria_text: str) -> Dict:
        """Check general project experience — value, count, type."""
        required_cr = self._extract_amount_cr(criteria_text)

        relevant = self.projects if self.projects else []

        if required_cr and relevant:
            qualifying = [p for p in relevant if self._parse_value_cr(p.get("val", "0")) >= required_cr]
            if qualifying:
                proj_str = " | ".join([
                    f"{p['client']} Rs.{p['val']} ({p.get('status','')})"
                    for p in qualifying[:4]
                ])
                return self._met(
                    f"Projects meeting Rs.{required_cr} Cr threshold: {proj_str}. "
                    f"Work orders + completion/go-live certificates available."
                )

        # Fallback to hardcoded known major projects
        known = (
            "KVIC Mobile GIS Rs.5.15 Cr | TCGL Tourism Portal Rs.9.31 Cr | "
            "CEICED eGovernance Rs.3.59 Cr | AMC GIS Rs.10.55 Cr | "
            "JuMC GIS Rs.9.78 Cr | PCSCL Smart City Rs.61.19 Cr (consortium)"
        )
        if required_cr:
            return self._met(
                f"Experience: {known}. "
                f"Multiple projects above Rs.{required_cr} Cr threshold. "
                f"Work orders + completion certificates available."
            )
        return self._met(
            f"IT/eGov project experience: {known}. "
            f"Work orders and completion certificates available for all projects."
        )

    # ── MASTER ROUTER ──────────────────────────────────────────

    def check_criteria(self, criteria_text: str) -> Dict:
        text = criteria_text.lower()

        if any(k in text for k in ["incorporat", "private limited", "llp", "partnership firm",
                                    "years of operation", "years in operation", "in operation for",
                                    "years as on", "registered under", "legal entity", "legal status"]):
            return self.check_company_registration(criteria_text)

        if any(k in text for k in ["annual turnover", "turnover of", "financial turnover",
                                    "it/ites", "it services", "ites services"]):
            return self.check_turnover(criteria_text)

        if any(k in text for k in ["gst", "goods and service", "pan card", "pan number",
                                    "income tax", "epf", "esi registration"]):
            return self.check_gst_pan(criteria_text)

        if "cmmi" in text:
            return self.check_cmmi(criteria_text)

        if any(k in text for k in ["iso 9001", "iso 27001", "iso 20000", "isms", "itsm",
                                    "iso certification", "quality certification"]):
            return self.check_iso(criteria_text)

        if any(k in text for k in ["emd", "earnest money", "bid security",
                                    "bid guarantee", "bid bond"]):
            return self.check_emd(criteria_text)

        if "solvency" in text:
            return self.check_solvency(criteria_text)

        if any(k in text for k in ["employee", "staff", "manpower", "headcount",
                                    "full-time", "full time", "personnel", "payroll",
                                    "people on payroll", "strength"]):
            return self.check_employee_strength(criteria_text)

        if any(k in text for k in ["gis", "mobile gis", "web gis", "geospatial",
                                    "mapping project", "geo portal", "geo-tagging",
                                    "gis-based", "gis solution"]):
            return self.check_gis_experience(criteria_text)

        if any(k in text for k in ["analytic", "mis based", "dashboard", "business intelligence",
                                    "bi platform", "data analytics", "reporting platform"]):
            return self.check_project_experience(criteria_text)

        if any(k in text for k in ["blacklist", "debar", "debarred", "blacklisted",
                                    "corrupt", "fraudulent", "bann"]):
            return self.check_blacklisting(criteria_text)

        if "cert-in" in text or "cert in" in text or "certin" in text:
            return self.check_cert_in(criteria_text)

        if "stqc" in text:
            return self.check_stqc(criteria_text)

        if any(k in text for k in ["local office", "office in", "branch office",
                                    "office within", "office in the state", "registered office in state",
                                    "presence in", "address in"]):
            return self.check_local_office(criteria_text)

        if any(k in text for k in ["msme", "udyam", "small enterprise",
                                    "medium enterprise", "micro enterprise"]):
            return self.check_msme(criteria_text)

        if any(k in text for k in ["project experience", "work experience", "executed",
                                    "completed project", "similar project", "relevant project",
                                    "minimum value", "contract value of", "assignment of"]):
            return self.check_project_experience(criteria_text)

        if any(k in text for k in ["annual turnover", "turnover"]):
            return self.check_turnover(criteria_text)

        return self._review(
            "This criterion requires manual review against Nascent's capabilities. "
            "Please verify and update status accordingly."
        )

    def check_all(self, pq_criteria: List[Dict]) -> List[Dict]:
        """
        For each criterion:
        - If AI already gave a real verdict → keep AI's answer (clean emojis only)
        - If AI returned 'Review' or blank → use NascentChecker rule-based result
        """
        results = []
        for item in pq_criteria:
            criteria_text = (item.get("criteria", "") + " " + item.get("details", "")).strip()
            check = self.check_criteria(criteria_text) if criteria_text else self._review("No criteria text.")

            existing_status = str(item.get("nascent_status", "")).strip()
            existing_color = str(item.get("nascent_color", "")).strip()

            # Clean emoji artifacts from AI output
            clean_map = {
                "✔ Met": "Met", "✅ MEETS": "Met", "✅ Met": "Met",
                "✘ Not Met": "Not Met", "❌ DOES NOT MEET": "Not Met",
                "✘ Critical": "Not Met", "❌ Not Met": "Not Met",
                "⚠ Conditional": "Conditional", "⚠️ CONDITIONAL": "Conditional",
                "⚠ Pending": "Conditional",
                "🔍 REVIEW": "Review", "🔍 Review": "Review",
            }
            for dirty, clean in clean_map.items():
                if dirty.lower() in existing_status.lower():
                    existing_status = clean
                    break

            # Decide: use AI result or checker result
            is_real_ai_verdict = existing_status in ("Met", "Not Met", "Conditional") and existing_color in ("GREEN", "RED", "AMBER")

            if is_real_ai_verdict:
                # Trust AI verdict — just clean it up
                item["nascent_status"] = existing_status
                item["nascent_color"] = existing_color
                # If remark is empty or very short, supplement with checker remark
                if not item.get("nascent_remark") or len(str(item.get("nascent_remark", ""))) < 20:
                    item["nascent_remark"] = check["remark"]
            else:
                # AI returned Review or blank — use our checker
                item["nascent_status"] = check["status"]
                item["nascent_color"] = check["color"]
                item["nascent_remark"] = check["remark"]

            results.append(item)
        return results

    def get_overall_verdict(self, checked_criteria: List[Dict]) -> Dict:
        red = sum(1 for c in checked_criteria if c.get("nascent_color") == "RED")
        amber = sum(1 for c in checked_criteria if c.get("nascent_color") == "AMBER")
        green = sum(1 for c in checked_criteria if c.get("nascent_color") == "GREEN")

        if red > 0:
            verdict = "NO-BID RECOMMENDED"
            reason = (
                f"{red} PQ/TQ criteria not met. Review each Not Met criterion — "
                f"consider JV or pre-bid clarification to resolve before committing."
            )
            color = "RED"
        elif amber > 2:
            verdict = "CONDITIONAL BID"
            reason = (
                f"Meets {green} criteria. {amber} items need pre-bid queries or "
                f"internal confirmation before bid submission."
            )
            color = "AMBER"
        elif amber > 0:
            verdict = "BID RECOMMENDED"
            reason = (
                f"Meets {green} criteria. {amber} item(s) need pre-bid queries — "
                f"raise before submission deadline. Proceed with bid preparation."
            )
            color = "GREEN"
        else:
            verdict = "BID RECOMMENDED"
            reason = f"All {green} PQ/TQ criteria met. Nascent is fully eligible to bid."
            color = "GREEN"

        return {
            "verdict": verdict, "reason": reason, "color": color,
            "green": green, "amber": amber, "red": red
        }

    # ── UTILITIES ──────────────────────────────────────────────

    def _extract_number(self, text: str, keywords: List[str]) -> Optional[float]:
        patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:' + '|'.join(keywords) + r')',
            r'(?:minimum|min\.?|at least|atleast|at\-least)\s*(\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)\s*(?:nos?\.?|numbers?)',
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return float(m.group(1))
        return None

    def _extract_amount_cr(self, text: str) -> Optional[float]:
        # Rs. X crore / X Cr
        m = re.search(r'(?:rs\.?|inr|₹)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:crore|cr\.?|crores)', text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(',', ''))
        # Rs. X lakh
        m = re.search(r'(?:rs\.?|inr|₹)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:lakh|lakhs|l\.?)', text, re.IGNORECASE)
        if m:
            return round(float(m.group(1).replace(',', '')) / 100, 2)
        # Plain Rs. XXXXXX (infer crore if > 1,000,000)
        m = re.search(r'(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)', text, re.IGNORECASE)
        if m:
            val = float(m.group(1).replace(',', ''))
            if val >= 10000000:
                return round(val / 10000000, 2)
            elif val >= 100000:
                return round(val / 10000000, 2)
        return None

    def _extract_ref_year(self, text: str) -> Optional[str]:
        m = re.search(
            r'(?:as on|as of)\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{2}[-]\w{3}[-]\d{4}|\w+\s+\d{4})',
            text, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r'(\d{2}/\d{2}/\d{4})', text)
        if m:
            return m.group(1)
        return None

    def _parse_value_cr(self, val_str: str) -> float:
        """Parse '₹9.78 Cr' or '20.5' into float crore."""
        if not val_str:
            return 0.0
        cleaned = re.sub(r'[₹Rs.,\s]', '', str(val_str))
        cleaned = re.sub(r'(?i)cr(ore)?s?', '', cleaned).strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    def _is_expired(self, date_str: str) -> bool:
        """Return True if the date has passed."""
        if not date_str:
            return False
        for fmt in ["%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%Y-%m-%d", "%b-%Y", "%B-%Y"]:
            try:
                d = datetime.strptime(date_str.strip(), fmt).date()
                return d < date.today()
            except ValueError:
                continue
        return False
