"""
nascent_checker.py — Nascent Profile PQ/TQ Checker
Checks each criterion against Nascent's profile and returns pass/fail + verdict.
Used as fallback when AI analysis is unavailable.
"""
import json
from pathlib import Path
from typing import List, Dict

PROFILE_PATH = Path(__file__).parent / "nascent_profile.json"


def load_profile() -> dict:
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


class NascentChecker:
    def __init__(self):
        self.profile = load_profile()
        fin = self.profile.get("finance", {})
        self.turnover = fin.get("avg_turnover_cr", fin.get("avg_turnover_last_3_fy", 17.18))
        try:
            self.turnover = float(self.turnover)
        except Exception:
            self.turnover = 17.18
        emp = self.profile.get("employees", {})
        self.emp_count = int(emp.get("total", self.profile.get("company", {}).get("total_employees", 67)))
        self.gis_staff = int(emp.get("gis_staff", 11))
        self.it_staff = int(emp.get("it_dev_staff", 21))
        certs = self.profile.get("certifications", {})
        self.has_cmmi = bool(certs.get("cmmi"))
        self.has_iso9001 = bool(certs.get("iso_9001"))
        self.has_iso27001 = bool(certs.get("iso_27001"))
        self.has_iso20000 = bool(certs.get("iso_20000"))
        self.is_msme = True

    def check_criterion(self, criterion: Dict) -> Dict:
        """Check a single criterion and return updated dict with status/color."""
        label = str(criterion.get("label", criterion.get("name", ""))).lower()
        desc  = str(criterion.get("description", criterion.get("desc", ""))).lower()
        text  = label + " " + desc

        # Turnover / financial criteria
        if any(k in text for k in ["turnover", "annual revenue", "financial"]):
            import re
            nums = re.findall(r"(\d+\.?\d*)\s*(?:cr|crore)", text)
            if nums:
                req_cr = float(nums[0])
                if self.turnover >= req_cr:
                    return {**criterion, "status": "PASS", "color": "GREEN",
                            "reason": f"Nascent avg turnover ₹{self.turnover}Cr ≥ required ₹{req_cr}Cr"}
                elif self.turnover >= req_cr * 0.75:
                    return {**criterion, "status": "CONDITIONAL", "color": "AMBER",
                            "reason": f"Turnover ₹{self.turnover}Cr — borderline (required ₹{req_cr}Cr)"}
                else:
                    return {**criterion, "status": "FAIL", "color": "RED",
                            "reason": f"Turnover ₹{self.turnover}Cr — below required ₹{req_cr}Cr"}

        # Employee count
        if any(k in text for k in ["employee", "staff", "manpower", "workforce"]):
            import re
            nums = re.findall(r"(\d+)\s*(?:employee|staff|manpower)", text)
            if nums:
                req = int(nums[0])
                if self.emp_count >= req:
                    return {**criterion, "status": "PASS", "color": "GREEN",
                            "reason": f"Nascent has {self.emp_count} employees ≥ required {req}"}
                else:
                    return {**criterion, "status": "FAIL", "color": "RED",
                            "reason": f"Only {self.emp_count} employees — required {req}"}

        # Certifications
        if "cmmi" in text:
            status = "PASS" if self.has_cmmi else "FAIL"
            return {**criterion, "status": status, "color": "GREEN" if status == "PASS" else "RED",
                    "reason": "Nascent holds CMMI V2.0 Level 3" if status == "PASS" else "CMMI not held"}
        if "iso 9001" in text or "iso9001" in text:
            status = "PASS" if self.has_iso9001 else "FAIL"
            return {**criterion, "status": status, "color": "GREEN" if status == "PASS" else "RED",
                    "reason": "ISO 9001:2015 held" if status == "PASS" else "ISO 9001 not held"}
        if "iso 27001" in text or "iso27001" in text:
            status = "PASS" if self.has_iso27001 else "FAIL"
            return {**criterion, "status": status, "color": "GREEN" if status == "PASS" else "RED",
                    "reason": "ISO 27001:2022 held" if status == "PASS" else "ISO 27001 not held"}
        if "iso 20000" in text or "iso20000" in text:
            status = "PASS" if self.has_iso20000 else "FAIL"
            return {**criterion, "status": status, "color": "GREEN" if status == "PASS" else "RED",
                    "reason": "ISO 20000-1:2018 held" if status == "PASS" else "ISO 20000 not held"}

        # MSME
        if "msme" in text:
            return {**criterion, "status": "PASS", "color": "GREEN",
                    "reason": "MSME registered — UDYAM-GJ-01-0007420"}

        # CERT-In / STQC — Nascent does NOT hold
        if "cert-in" in text or "stqc" in text or "meity empanel" in text:
            return {**criterion, "status": "FAIL", "color": "RED",
                    "reason": "Nascent is not CERT-In / STQC empanelled — raise pre-bid query"}

        # GIS experience
        if any(k in text for k in ["gis", "geospatial", "mapping", "geo"]):
            return {**criterion, "status": "PASS", "color": "GREEN",
                    "reason": "Nascent has 15+ years GIS experience (AMC, VMC, JuMC, PCSCL, KVIC)"}

        # Mobile / app experience
        if any(k in text for k in ["mobile app", "android", "ios", "flutter"]):
            return {**criterion, "status": "PASS", "color": "GREEN",
                    "reason": "Nascent delivers Android, iOS and Flutter mobile applications"}

        # Smart city / e-governance
        if any(k in text for k in ["smart city", "e-governance", "egovernance", "erp"]):
            return {**criterion, "status": "PASS", "color": "GREEN",
                    "reason": "Nascent has delivered Smart City (PCSCL 61 Cr), e-Gov portals"}

        # Similar work value
        if any(k in text for k in ["similar work", "similar project", "similar assignment"]):
            import re
            nums = re.findall(r"(\d+\.?\d*)\s*(?:cr|crore|lakh)", text)
            if nums:
                multiplier = 1 if "cr" in text else 0.01
                req_cr = float(nums[0]) * multiplier
                projects = self.profile.get("projects", [])
                max_proj = max((float(p.get("value_cr", p.get("value_lakhs", 0) or 0)) for p in projects
                                if p.get("value_cr") or p.get("value_lakhs")), default=0)
                if max_proj >= req_cr:
                    return {**criterion, "status": "PASS", "color": "GREEN",
                            "reason": f"Largest project ₹{max_proj:.1f}Cr ≥ required ₹{req_cr:.1f}Cr"}
                else:
                    return {**criterion, "status": "CONDITIONAL", "color": "AMBER",
                            "reason": f"Max project ₹{max_proj:.1f}Cr — verify if meets similar work requirement"}

        # Default — needs review
        return {**criterion, "status": "REVIEW", "color": "AMBER",
                "reason": "Auto-check not available for this criterion — manual review needed"}

    def check_all(self, criteria: List[Dict]) -> List[Dict]:
        return [self.check_criterion(c) for c in criteria]

    def get_overall_verdict(self, criteria: List[Dict]) -> Dict:
        if not criteria:
            return {"verdict": "REVIEW", "color": "BLUE", "reason": "No criteria to evaluate"}
        statuses = [c.get("status", "REVIEW") for c in criteria]
        fail_count = statuses.count("FAIL")
        cond_count = statuses.count("CONDITIONAL")
        pass_count = statuses.count("PASS")
        total = len(statuses)

        if fail_count > 0:
            fails = [c.get("label","") for c in criteria if c.get("status") == "FAIL"]
            return {
                "verdict": "NO-BID",
                "color": "RED",
                "reason": f"{fail_count} criterion/criteria not met: {', '.join(fails[:3])}",
                "green": pass_count, "amber": cond_count, "red": fail_count
            }
        if cond_count > 0:
            return {
                "verdict": "CONDITIONAL",
                "color": "AMBER",
                "reason": f"{cond_count} conditional item(s) — verify before bidding",
                "green": pass_count, "amber": cond_count, "red": 0
            }
        return {
            "verdict": "BID",
            "color": "GREEN",
            "reason": f"All {total} criteria passed — Nascent is eligible",
            "green": pass_count, "amber": 0, "red": 0
        }
