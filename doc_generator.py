"""
BidDocGenerator v5 — Full Word Document
New sections vs v4:
- Section 4: Payment Terms (structured milestone table)
- Section 5: Penalty & Risk Summary (from penalty_clauses)
- Section 6: Manpower Obligations (from manpower_obligations)
- Section 7: Existing Tech Stack (from existing_infrastructure)
- Section 8: Bid/No-Bid Recommendation (was Section 5)
- Section 9: Notes & Action Items
- Section 10: Authorization
"""

from docx import Document
from docx.shared import Pt, RGBColor, Cm, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from datetime import datetime
from typing import Dict, Any, List
import re as _re

C = {
    "dark_blue": "1F497D", "mid_blue": "2E75B6", "light_blue": "BDD7EE",
    "label_col": "D6E4F0", "alt_row": "F2F7FB", "white": "FFFFFF",
    "green_bg": "E2EFDA", "green_text": "375623",
    "amber_bg": "FFF2CC", "amber_text": "7F6000",
    "red_bg": "FCE4D6", "red_text": "C00000",
    "blue_bg": "DEEAF1", "blue_text": "1F497D",
    "orange": "C55A11", "gray": "808080", "dark": "262626",
    "teal_bg": "D9EAD3", "teal_text": "274E13",
    "purple_bg": "E8D5F5", "purple_text": "4A0080",
}

STATUS_STYLE = {
    "GREEN": ("green_bg", "green_text"),
    "AMBER": ("amber_bg", "amber_text"),
    "RED": ("red_bg", "red_text"),
    "BLUE": ("blue_bg", "blue_text"),
}


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def set_bg(cell, hex_color):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color.lstrip("#"))
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def set_borders(cell, color="9DC3E6", size=4):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcB = OxmlElement("w:tcBorders")
    for b in ["top", "left", "bottom", "right"]:
        el = OxmlElement("w:" + b)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:color"), color.lstrip("#"))
        tcB.append(el)
    tcPr.append(tcB)


def set_table_borders(table, color="2E75B6"):
    tbl = table._tbl
    tblPr = tbl.find(qn("w:tblPr"))
    if tblPr is None:
        tblPr = OxmlElement("w:tblPr")
        tbl.insert(0, tblPr)
    tblB = OxmlElement("w:tblBorders")
    for b in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        el = OxmlElement("w:" + b)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), color.lstrip("#"))
        tblB.append(el)
    tblPr.append(tblB)


def repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    trPr.append(tblHeader)


def add_run(para, text, bold=False, size=10, color=None, italic=False):
    r = para.add_run(str(text) if text else "")
    r.font.name = "Calibri"
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    if color:
        c = hex_rgb(color) if isinstance(color, str) else color
        r.font.color.rgb = RGBColor(*c)
    return r


def cell_write(cell, text, bold=False, size=9, color=None, italic=False,
               align=WD_ALIGN_PARAGRAPH.LEFT, pad=5):
    p = cell.paragraphs[0]
    p.alignment = align
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.left_indent = Pt(pad)
    add_run(p, text, bold=bold, size=size, color=color, italic=italic)
    return p


def strip_emojis(text):
    if not text:
        return text
    return _re.sub(
        r"[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF"
        r"\u2300-\u23FF\u2B50-\u2BFF\u2100-\u214F\u0080-\u00FF\u2000-\u206F]",
        "", text
    ).strip()


def clean_status(s):
    s = strip_emojis(str(s))
    for old, new in [
        ("MEETS", "Met"), ("MET", "Met"), ("DOES NOT MEET", "Not Met"),
        ("NOT MET", "Not Met"), ("Critical", "Not Met"), ("CRITICAL", "Not Met"),
        ("CONDITIONAL", "Conditional"), ("Pending", "Conditional"),
        ("REVIEW", "Review"),
    ]:
        if old.lower() in s.lower():
            return new
    if "Met" in s: return "Met"
    if "Not" in s: return "Not Met"
    if "Cond" in s: return "Conditional"
    return "Review"


def status_color(status_text):
    s = status_text.lower()
    if "not met" in s or "critical" in s: return "RED"
    if "conditional" in s or "pending" in s: return "AMBER"
    if "met" in s: return "GREEN"
    return "BLUE"


class BidDocGenerator:

    def generate(self, data: Dict[str, Any], output_path: str):
        self.doc = Document()
        self._setup_page()
        self._header_block(data)
        self._section_snapshot(data)
        self._section_pq(data)
        self._section_scope(data)
        self._section_payment(data)
        self._section_penalty(data)
        self._section_manpower(data)
        self._section_tech_stack(data)
        self._section_recommendation(data)
        self._section_notes(data)
        self._section_authorization()
        self._footer(data)
        self.doc.save(output_path)

    def _setup_page(self):
        sec = self.doc.sections[0]
        sec.page_width = Cm(29.7)
        sec.page_height = Cm(21.0)
        sec.left_margin = sec.right_margin = Cm(1.8)
        sec.top_margin = sec.bottom_margin = Cm(1.5)
        self.doc.styles["Normal"].font.name = "Calibri"
        self.doc.styles["Normal"].font.size = Pt(10)

    def _sec_heading(self, number, title, source_note=None):
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(" " + number + ". " + title + " ")
        r.font.name = "Calibri"
        r.font.size = Pt(12)
        r.font.bold = True
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        pPr = p._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), C["dark_blue"])
        shd.set(qn("w:val"), "clear")
        pPr.append(shd)
        if source_note:
            p2 = self.doc.add_paragraph()
            p2.paragraph_format.space_before = Pt(0)
            p2.paragraph_format.space_after = Pt(4)
            add_run(p2, source_note, size=8, italic=True, color=C["mid_blue"])

    def _header_block(self, data):
        table = self.doc.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["dark_blue"])
        c0 = table.rows[0].cells[0]
        c0.width = Cm(10)
        set_bg(c0, C["dark_blue"])
        set_borders(c0, color="FFFFFF", size=6)
        p = c0.paragraphs[0]
        p.paragraph_format.space_before = Pt(4)
        add_run(p, "Nascent Info Technologies Pvt. Ltd.", bold=True, size=11, color="FFFFFF")
        p2 = c0.add_paragraph()
        add_run(p2, "A-805, Shapath IV, SG Highway, Prahlad Nagar, Ahmedabad 380015", size=8, color="BDD7EE")
        p3 = c0.add_paragraph()
        add_run(p3, "www.nascentinfo.com | nascent.tender@nascentinfo.com", size=8, color="BDD7EE")
        p4 = c0.add_paragraph()
        p4.paragraph_format.space_after = Pt(4)
        add_run(p4, "MSME | CMMI L3 | ISO 9001 | ISO 27001 | ISO 20000", size=8, color="FFF2CC")

        c1 = table.rows[0].cells[1]
        c1.width = Cm(15.5)
        set_bg(c1, C["mid_blue"])
        set_borders(c1, color="FFFFFF", size=6)
        p = c1.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        add_run(p, "BID / NO-BID FORM", bold=True, size=16, color="FFFFFF")
        tender_title = strip_emojis(data.get("tender_name", data.get("org_name", "")))[:80]
        p2 = c1.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_run(p2, tender_title, size=10, color="DEEAF1")
        verdict_data = data.get("overall_verdict", {})
        verdict = strip_emojis(verdict_data.get("verdict", "PENDING REVIEW"))
        vcolor = verdict_data.get("color", "BLUE")
        v_txt = {"GREEN": "FFF2CC", "AMBER": "FFF2CC", "RED": "FCE4D6", "BLUE": "DEEAF1"}.get(vcolor, "DEEAF1")
        p3 = c1.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p3.paragraph_format.space_after = Pt(6)
        add_run(p3, verdict, bold=True, size=12, color=v_txt)
        p4 = c1.add_paragraph()
        p4.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p4.paragraph_format.space_after = Pt(4)
        add_run(p4, "Prepared: " + datetime.now().strftime("%d-%b-%Y") + " | Nascent Bid Team", size=8, color="DEEAF1")
        self.doc.add_paragraph()

    def _section_snapshot(self, data):
        self._sec_heading("1", "Tender Overview")
        fields = [
            ("Tender No.", data.get("tender_no", "—")),
            ("Tender ID", data.get("tender_id", "—")),
            ("T247 ID", data.get("t247_id", "—")),
            ("Portal / Website", data.get("portal", "—")),
            ("Organization / Department", data.get("org_name", "—")),
            ("Tender Name", data.get("tender_name", "—")),
            ("Form of Contract", data.get("tender_type", "—")),
            ("Bid Submission Start Date", data.get("bid_start_date", "—")),
            ("Bid Submission End Date", data.get("bid_submission_date", "—")),
            ("Bid Opening Date", data.get("bid_opening_date", "—")),
            ("Commercial Opening Date", data.get("commercial_opening_date", "—")),
            ("Mode of Selection", data.get("mode_of_selection", "—")),
            ("Pre-Bid Meeting", data.get("prebid_meeting", "Not specified")),
            ("Pre-Bid Query Deadline", data.get("prebid_query_date", "Not specified")),
            ("Estimated Cost", data.get("estimated_cost", "Not specified in tender — verify from portal")),
            ("Tender Fee", data.get("tender_fee", "Not specified")),
            ("EMD", data.get("emd", "Not specified")),
            ("EMD Exemption", data.get("emd_exemption", "—")),
            ("Performance Bank Guarantee", data.get("performance_security", "As per tender")),
            ("Period of Work", data.get("contract_period", "—")),
            ("Post-Implementation Support", data.get("post_implementation", "—")),
            ("Technology Mandatory", data.get("technology_mandatory", "—")),
            ("Project Location", data.get("location", "—")),
            ("Contact", data.get("contact", "—")),
            ("JV / Consortium Allowed", data.get("jv_allowed", "Not specified — verify from tender T&C")),
        ]
        table = self.doc.add_table(rows=0, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        highlight_keys = ["Bid Submission End Date", "EMD", "Tender Fee", "JV / Consortium Allowed", "Estimated Cost"]
        for idx, (key, val) in enumerate(fields):
            row = table.add_row()
            row.height = Cm(0.75)
            bg_l = "D6E4F0" if idx % 2 == 0 else "E8F0F8"
            bg_v = C["white"] if idx % 2 == 0 else C["alt_row"]
            c0 = row.cells[0]
            c0.width = Cm(7)
            set_bg(c0, bg_l)
            set_borders(c0)
            cell_write(c0, key, bold=True, size=9, color=C["dark_blue"])
            c1 = row.cells[1]
            c1.width = Cm(18.5)
            set_bg(c1, bg_v)
            set_borders(c1)
            hl = key in highlight_keys and val not in ["—", "Not specified", "", "Not specified in tender — verify from portal"]
            cell_write(c1, str(val), bold=hl, size=9, color=(C["orange"] if hl else None))
        self.doc.add_paragraph()

    def _criteria_table(self, criteria, headers, col_w):
        if not criteria:
            p = self.doc.add_paragraph()
            add_run(p, "No criteria extracted — refer tender document.", italic=True, size=9)
            return
        table = self.doc.add_table(rows=1, cols=len(headers))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        hrow = table.rows[0]
        repeat_header(hrow)
        for cell, hdr in zip(hrow.cells, headers):
            set_bg(cell, C["dark_blue"])
            set_borders(cell, color="FFFFFF", size=4)
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(3)
            p.paragraph_format.space_after = Pt(3)
            add_run(p, hdr, bold=True, size=9, color="FFFFFF")
        for ri, item in enumerate(criteria):
            row = table.add_row()
            bg = C["white"] if ri % 2 == 0 else C["alt_row"]
            raw_status = item.get("nascent_status", "Review")
            sym = clean_status(raw_status)
            sc = item.get("nascent_color") or status_color(sym)
            s_bg, s_tc = STATUS_STYLE.get(sc, ("blue_bg", "blue_text"))
            c = row.cells[0]; set_bg(c, C["label_col"]); set_borders(c)
            cell_write(c, str(item.get("sl_no", ri+1)), bold=True, size=9, color=C["dark_blue"], align=WD_ALIGN_PARAGRAPH.CENTER)
            c = row.cells[1]; set_bg(c, bg); set_borders(c)
            cell_write(c, item.get("clause_ref", "—"), size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
            c = row.cells[2]; set_bg(c, bg); set_borders(c)
            cell_write(c, item.get("criteria", ""), size=9)
            c = row.cells[3]; set_bg(c, bg); set_borders(c)
            cell_write(c, item.get("details", ""), size=9)
            c = row.cells[4]; set_bg(c, C[s_bg]); set_borders(c)
            cell_write(c, sym, bold=True, size=8, color=C[s_tc], align=WD_ALIGN_PARAGRAPH.CENTER)
            c = row.cells[5]; set_bg(c, C[s_bg] if sc != "BLUE" else bg); set_borders(c)
            cell_write(c, strip_emojis(item.get("nascent_remark", "")), size=8)

    def _section_pq(self, data):
        self._sec_heading("2", "Pre-Qualification (PQ) Criteria",
                          "Criteria reproduced word-for-word from tender | Nascent status checked against company profile")
        headers = ["Sr.", "Clause No.\nPage No.", "Eligibility Criteria\n(word-for-word from tender)",
                   "Supporting Documents\nRequired", "Nascent\nStatus", "Remarks / Action Required"]
        col_w = [Cm(0.9), Cm(2.3), Cm(8.5), Cm(5.0), Cm(2.0), Cm(6.8)]
        self._criteria_table(data.get("pq_criteria", []), headers, col_w)
        self.doc.add_paragraph()

    def _section_scope(self, data):
        self._sec_heading("3", "Scope of Work",
                          "Source: Tender document — key deliverables and phases")
        scope_items = data.get("scope_items", [])
        if not scope_items:
            p = self.doc.add_paragraph()
            add_run(p, "Refer to tender document for scope of work.", italic=True, size=9)
        else:
            for item in scope_items:
                p = self.doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.2)
                p.paragraph_format.space_after = Pt(3)
                add_run(p, "  ", size=9)
                add_run(p, strip_emojis(str(item)), size=9)
        self.doc.add_paragraph()

    def _section_payment(self, data):
        self._sec_heading("4", "Payment Terms & Milestones",
                          "Source: Tender document — milestone-based payment schedule")
        items = data.get("payment_terms", [])
        if items and isinstance(items[0], dict):
            # Structured milestone table
            table = self.doc.add_table(rows=1, cols=5)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            set_table_borders(table, color=C["mid_blue"])
            hrow = table.rows[0]
            for cell, hdr in zip(hrow.cells, ["Milestone", "Activity", "Timeline", "Payment %", "Notes"]):
                set_bg(cell, C["dark_blue"])
                set_borders(cell, color="FFFFFF")
                p = cell.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                add_run(p, hdr, bold=True, size=9, color="FFFFFF")
            for ri, item in enumerate(items):
                row = table.add_row()
                bg = C["white"] if ri % 2 == 0 else C["alt_row"]
                vals = [
                    item.get("milestone", ""), item.get("activity", ""),
                    item.get("timeline", ""), item.get("payment_percent", ""),
                    item.get("notes", "")
                ]
                for ci, (cell, val) in enumerate(zip(row.cells, vals)):
                    set_bg(cell, bg)
                    set_borders(cell)
                    cell_write(cell, str(val), size=9,
                               bold=(ci == 3), color=(C["dark_blue"] if ci == 3 else None))
        else:
            # Fallback: list format
            fallback = items if items else [
                "Period of work: " + data.get("contract_period", "As per tender"),
                "EMD: " + data.get("emd", "As per tender"),
                "Performance Bank Guarantee: " + data.get("performance_security", "As per tender"),
                "Payment schedule: Not explicitly defined — refer tender document for milestone-based payment terms.",
                "Penalty / LD clause: Refer tender document for applicable clauses.",
            ]
            for item in fallback:
                p = self.doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.2)
                p.paragraph_format.space_after = Pt(3)
                add_run(p, "  ", size=9)
                add_run(p, strip_emojis(str(item)), size=9)
        self.doc.add_paragraph()

    def _section_penalty(self, data):
        """NEW in v5 — Penalty & Risk Summary"""
        penalty_clauses = data.get("penalty_clauses", [])
        if not penalty_clauses:
            return  # Skip section if no data

        self._sec_heading("5", "Penalty & Risk Summary",
                          "Source: Tender Terms & Conditions — all penalty clauses extracted")
        table = self.doc.add_table(rows=1, cols=4)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        hrow = table.rows[0]
        for cell, hdr in zip(hrow.cells, ["Type", "Condition", "Penalty", "Max Cap / Clause"]):
            set_bg(cell, C["red_text"])
            set_borders(cell, color="FFFFFF")
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_run(p, hdr, bold=True, size=9, color="FFFFFF")
        for ri, item in enumerate(penalty_clauses):
            row = table.add_row()
            bg = C["red_bg"] if ri % 2 == 0 else C["white"]
            vals = [
                item.get("type", ""), item.get("condition", ""),
                item.get("penalty", ""),
                (item.get("max_cap", "") + " | " + item.get("clause_ref", "")).strip(" | ")
            ]
            for cell, val in zip(row.cells, vals):
                set_bg(cell, bg)
                set_borders(cell)
                cell_write(cell, str(val), size=9)
        self.doc.add_paragraph()

    def _section_manpower(self, data):
        """NEW in v5 — Manpower Obligations"""
        manpower = data.get("manpower_obligations", [])
        if not manpower:
            return

        self._sec_heading("6", "Manpower Obligations",
                          "Source: Tender Scope — on-site and off-site resource requirements")
        for item in manpower:
            table = self.doc.add_table(rows=0, cols=2)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            set_table_borders(table, color=C["mid_blue"])
            fields = [
                ("Role", item.get("role", "")),
                ("Count", item.get("count", "")),
                ("Type", item.get("type", "")),
                ("Deployment Timeline", item.get("deployment_timeline", "")),
                ("Duration", item.get("duration", "")),
                ("Min Qualification", item.get("min_qualification", "")),
                ("Working Conditions", item.get("working_conditions", "")),
                ("Replacement Policy", item.get("replacement_policy", "")),
                ("Penalty for Absence", item.get("penalty_for_absence", "")),
            ]
            for idx, (key, val) in enumerate(fields):
                row = table.add_row()
                bg_l = C["label_col"] if idx % 2 == 0 else "D6E4F0"
                bg_v = C["white"] if idx % 2 == 0 else C["alt_row"]
                c0 = row.cells[0]; c0.width = Cm(7)
                set_bg(c0, bg_l); set_borders(c0)
                cell_write(c0, key, bold=True, size=9, color=C["dark_blue"])
                c1 = row.cells[1]; c1.width = Cm(18.5)
                set_bg(c1, bg_v); set_borders(c1)
                cell_write(c1, str(val), size=9)
            self.doc.add_paragraph()

    def _section_tech_stack(self, data):
        """NEW in v5 — Existing Technology Stack from Annexure"""
        infra = data.get("existing_infrastructure", {})
        if not infra or not any(infra.values()):
            return

        self._sec_heading("7", "Existing Technology Infrastructure",
                          "Source: Tender Annexure A — existing GIS software, hardware, and data layers")
        table = self.doc.add_table(rows=0, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        fields = [
            ("GIS Software / Server", infra.get("gis_software", "—")),
            ("Backend Language", infra.get("backend", "—")),
            ("Database", infra.get("database", "—")),
            ("API Standards", infra.get("api_standards", "—")),
            ("Server Hardware", infra.get("server_specs", "—")),
            ("GIS Layers / Groups", infra.get("gis_layers", "—")),
            ("Registered Users", infra.get("registered_users", "—")),
            ("Monthly Visitors", infra.get("monthly_visitors", "—")),
        ]
        for idx, (key, val) in enumerate(fields):
            row = table.add_row()
            bg_l = C["label_col"] if idx % 2 == 0 else "D6E4F0"
            bg_v = C["white"] if idx % 2 == 0 else C["alt_row"]
            c0 = row.cells[0]; c0.width = Cm(7)
            set_bg(c0, bg_l); set_borders(c0)
            cell_write(c0, key, bold=True, size=9, color=C["dark_blue"])
            c1 = row.cells[1]; c1.width = Cm(18.5)
            set_bg(c1, bg_v); set_borders(c1)
            cell_write(c1, str(val), size=9)
        self.doc.add_paragraph()

    def _section_recommendation(self, data):
        self._sec_heading("8", "Bid / No-Bid Recommendation")
        verdict_data = data.get("overall_verdict", {})
        green_count = verdict_data.get("green", 0)
        amber_count = verdict_data.get("amber", 0)
        red_count = verdict_data.get("red", 0)

        stats = [
            (str(green_count), "Criteria Met", "green_bg", "green_text"),
            (str(amber_count), "Conditional", "amber_bg", "amber_text"),
            (str(red_count), "Not Met", "red_bg", "red_text"),
        ]
        table = self.doc.add_table(rows=1, cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        for cell, (count, label, bg, tc) in zip(table.rows[0].cells, stats):
            set_bg(cell, C[bg]); set_borders(cell, color="FFFFFF", size=6)
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(4)
            add_run(p, count, bold=True, size=18, color=C[tc])
            p2 = cell.add_paragraph()
            p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p2.paragraph_format.space_after = Pt(4)
            add_run(p2, label, size=9, color=C[tc])
        self.doc.add_paragraph()

        vcolor = verdict_data.get("color", "BLUE")
        s_bg, s_tc = STATUS_STYLE.get(vcolor, ("blue_bg", "blue_text"))
        verdict = strip_emojis(verdict_data.get("verdict", "PENDING REVIEW"))
        reason = strip_emojis(verdict_data.get("reason", ""))

        assessment_rows = [
            ("Financial Eligibility", "Average turnover Rs. 17.18 Cr (last 3 FY). Net worth Rs. 26.09 Cr."),
            ("Company Registration", "Private Limited Company since 2006. 19 years in operation."),
            ("CMMI Certification", "CMMI V2.0 Level 3 — valid till 19-Dec-2026."),
            ("ISO Certifications", "ISO 9001:2015, ISO 27001:2022, ISO 20000-1:2018 — all valid till Sep-2028."),
            ("GIS / IT Experience", "9 major projects — AMC, JuMC, VMC, KVIC, PCSCL, TCGL, BMC."),
            ("Mobile GIS Experience", "KVIC mobile GIS (PAN India), BMC mobile app, AMC Heritage App."),
            ("Employee Strength", "67 employees — 11 GIS, 21 IT/Dev, plus QA, PM, BA teams."),
            ("MSME Status", "UDYAM-GJ-01-0007420 — eligible for EMD exemption."),
            ("PQ Criteria Summary", f"Met: {green_count}  Conditional: {amber_count}  Not Met: {red_count}"),
            ("FINAL RECOMMENDATION", verdict + "\n" + reason),
        ]
        tbl2 = self.doc.add_table(rows=0, cols=2)
        tbl2.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(tbl2, color=C["mid_blue"])
        for ri, (key, val) in enumerate(assessment_rows):
            row = tbl2.add_row()
            is_final = (key == "FINAL RECOMMENDATION")
            c0 = row.cells[0]; c0.width = Cm(8)
            set_bg(c0, C["dark_blue"] if is_final else C["label_col"]); set_borders(c0)
            cell_write(c0, key, bold=True, size=10 if is_final else 9,
                       color=("FFFFFF" if is_final else C["dark_blue"]))
            c1 = row.cells[1]; c1.width = Cm(17.5)
            set_bg(c1, C[s_bg] if is_final else (C["white"] if ri % 2 == 0 else C["alt_row"])); set_borders(c1)
            cell_write(c1, val, bold=is_final, size=11 if is_final else 9,
                       color=(C[s_tc] if is_final else None))
        self.doc.add_paragraph()

    def _section_notes(self, data):
        self._sec_heading("9", "Notes & Action Items",
                          "Items to be resolved before committing to bid.")
        action_items = []
        for item in data.get("pq_criteria", []):
            sc = item.get("nascent_color", "BLUE")
            if sc in ["RED", "AMBER"]:
                priority = "URGENT" if sc == "RED" else "ACTION"
                crit = strip_emojis(item.get("criteria", ""))[:80]
                remark = strip_emojis(item.get("nascent_remark", ""))[:200]
                action_items.append({
                    "priority": priority, "text": crit, "detail": remark,
                    "color": C["red_text"] if sc == "RED" else C["amber_text"],
                    "bg": "red_bg" if sc == "RED" else "amber_bg",
                })
        for note in data.get("notes", []):
            note = strip_emojis(str(note))
            is_risk = any(k in note.lower() for k in ["penalty", "blacklist", "disqualif", "liquidated"])
            action_items.append({
                "priority": "RISK" if is_risk else "AWARENESS",
                "text": note[:200], "detail": "",
                "color": C["red_text"] if is_risk else C["dark_blue"],
                "bg": "red_bg" if is_risk else "blue_bg",
            })
        if not action_items:
            action_items.append({
                "priority": "NOTE",
                "text": "No specific risk flags detected. Please review tender document manually before bidding.",
                "detail": "", "color": C["dark_blue"], "bg": "blue_bg",
            })
        table = self.doc.add_table(rows=0, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        for i, item in enumerate(action_items[:15]):
            row = table.add_row()
            cell = row.cells[0]
            set_bg(cell, C[item["bg"]]); set_borders(cell, color="9DC3E6")
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.left_indent = Pt(6)
            label = str(i+1) + ". " + item["priority"] + " — "
            add_run(p, label, bold=True, size=9, color=item["color"])
            add_run(p, item["text"], size=9)
            if item.get("detail"):
                p2 = cell.add_paragraph()
                p2.paragraph_format.left_indent = Pt(20)
                p2.paragraph_format.space_after = Pt(4)
                add_run(p2, item["detail"], size=8, italic=True)
        self.doc.add_paragraph()

    def _section_authorization(self):
        self._sec_heading("10", "Authorization")
        table = self.doc.add_table(rows=2, cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        for cell, hdr in zip(table.rows[0].cells, ["Prepared By", "Reviewed By", "Approved By"]):
            set_bg(cell, C["dark_blue"]); set_borders(cell, color="FFFFFF")
            cell_write(cell, hdr, bold=True, size=10, color="FFFFFF", align=WD_ALIGN_PARAGRAPH.CENTER)
        names = ["Parthav Thakkar\nBid Executive\nNascent Info Technologies Pvt. Ltd.", "—", "—"]
        for cell, name in zip(table.rows[1].cells, names):
            set_bg(cell, C["alt_row"]); set_borders(cell)
            cell_write(cell, name, size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
        row3 = table.add_row()
        for cell in row3.cells:
            set_bg(cell, C["white"]); set_borders(cell)
            cell_write(cell, "Date: _______________", size=9, color=C["gray"], align=WD_ALIGN_PARAGRAPH.CENTER)
        self.doc.add_paragraph()

    def _footer(self, data):
        footer = self.doc.sections[0].footer
        fp = footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = fp.add_run(
            "CONFIDENTIAL — Bid/No-Bid Form | " +
            data.get("tender_no", "—") + " | " +
            "Nascent Info Technologies Pvt. Ltd. | " +
            datetime.now().strftime("%d %b %Y") +
            " | For Internal Use Only"
        )
        r.font.size = Pt(7)
        r.font.name = "Calibri"
        r.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
        r.font.italic = True
