"""
BidDocGenerator v6 — Full Word Document matching TSECL format
Sections:
  1. Tender Snapshot (full table)
  2. JV / Consortium Conditions
  3. Scope of Work
  4. PQ Criteria (word-for-word + Nascent status + pre-bid query per gap)
  5. TQ Criteria (if present)
  6. Payment Terms & Milestones
  7. Penalty & Risk Summary
  8. Portal vs RFP Discrepancies
  9. Pre-Bid Queries (consolidated)
 10. Bid / No-Bid Recommendation
 11. Immediate Action Items (numbered, dated)
 12. Authorization
"""

from docx import Document
from docx.shared import Pt, RGBColor, Cm, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
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
    "red_bg":   "FCE4D6", "red_text":   "C00000",
    "blue_bg":  "DEEAF1", "blue_text":  "1F497D",
    "orange": "C55A11", "gray": "808080", "dark": "262626",
}

STATUS_STYLE = {
    "GREEN": ("green_bg", "green_text"),
    "AMBER": ("amber_bg", "amber_text"),
    "RED":   ("red_bg",   "red_text"),
    "BLUE":  ("blue_bg",  "blue_text"),
}


# ── helpers ───────────────────────────────────────────────────
def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def set_bg(cell, hex_color):
    tc = cell._tc; tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color.lstrip("#")); shd.set(qn("w:val"), "clear")
    tcPr.append(shd)

def set_borders(cell, color="9DC3E6", size=4):
    tc = cell._tc; tcPr = tc.get_or_add_tcPr()
    tcB = OxmlElement("w:tcBorders")
    for b in ["top","left","bottom","right"]:
        el = OxmlElement("w:"+b)
        el.set(qn("w:val"),"single"); el.set(qn("w:sz"),str(size))
        el.set(qn("w:color"),color.lstrip("#")); tcB.append(el)
    tcPr.append(tcB)

def set_table_borders(table, color="2E75B6"):
    tbl = table._tbl
    tblPr = tbl.find(qn("w:tblPr"))
    if tblPr is None:
        tblPr = OxmlElement("w:tblPr"); tbl.insert(0, tblPr)
    tblB = OxmlElement("w:tblBorders")
    for b in ["top","left","bottom","right","insideH","insideV"]:
        el = OxmlElement("w:"+b)
        el.set(qn("w:val"),"single"); el.set(qn("w:sz"),"4")
        el.set(qn("w:color"),color.lstrip("#")); tblB.append(el)
    tblPr.append(tblB)

def repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader"); trPr.append(tblHeader)

def add_run(para, text, bold=False, size=10, color=None, italic=False):
    r = para.add_run(str(text) if text else "")
    r.font.name = "Calibri"; r.font.size = Pt(size)
    r.font.bold = bold; r.font.italic = italic
    if color:
        c = hex_rgb(color) if isinstance(color, str) else color
        r.font.color.rgb = RGBColor(*c)
    return r

def cell_write(cell, text, bold=False, size=9, color=None, italic=False,
               align=WD_ALIGN_PARAGRAPH.LEFT, pad=5):
    p = cell.paragraphs[0]
    p.alignment = align
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after  = Pt(2)
    p.paragraph_format.left_indent  = Pt(pad)
    add_run(p, text, bold=bold, size=size, color=color, italic=italic)
    return p

def strip_emojis(text):
    if not text: return text
    return _re.sub(
        r"[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF"
        r"\u2300-\u23FF\u2B50-\u2BFF\u2100-\u214F\u0080-\u00FF\u2000-\u206F]",
        "", text).strip()

def clean_status(s):
    s = strip_emojis(str(s))
    for old, new in [
        ("NOT MET","Not Met"),("not met","Not Met"),("CRITICAL","Not Met"),
        ("CONDITIONAL","Conditional"),("conditional","Conditional"),
        ("MET","Met"),("met","Met"),("REVIEW","Review"),
    ]:
        if old.lower() in s.lower(): return new
    if "Met" in s: return "Met"
    if "Not" in s: return "Not Met"
    if "Cond" in s: return "Conditional"
    return "Review"

def status_color(s):
    s = s.lower()
    if "not met" in s or "critical" in s: return "RED"
    if "conditional" in s or "pending" in s: return "AMBER"
    if "met" in s: return "GREEN"
    return "BLUE"


def flatten_value(val) -> str:
    """Convert any value to a clean readable string for Word cells."""
    if val is None:
        return "—"
    if isinstance(val, str):
        return val.strip() or "—"
    if isinstance(val, dict):
        parts = []
        for k, v in val.items():
            if v is not None and str(v).strip():
                label = k.replace('_', ' ').title()
                parts.append(f"{label}: {v}")
        return " | ".join(parts) if parts else "—"
    if isinstance(val, list):
        return ", ".join(str(i) for i in val if i) or "—"
    return str(val).strip() or "—"


class BidDocGenerator:

    def generate(self, data: Dict[str, Any], output_path: str):
        self.doc = Document()
        self._setup_page()
        self._header_block(data)
        self._section_snapshot(data)             # 1. Tender Snapshot
        self._section_jv_conditions(data)        # 2. JV / Consortium Conditions
        self._section_scope(data)                # 3. Scope of Work
        self._section_pq(data)                   # 4. PQ Criteria
        self._section_tq(data)                   # 5. TQ Criteria (if any)
        self._section_payment(data)              # 6. Payment Terms
        self._section_penalty(data)              # 7. Penalty & Risk
        self._section_portal_discrepancies(data) # 8. Portal vs RFP Discrepancies
        self._section_prebid_queries(data)       # 9. Pre-Bid Queries (consolidated)
        self._section_recommendation(data)       # 10. Verdict
        self._section_action_items(data)         # 11. Immediate Action Items
        self._section_authorization()            # 12. Authorization
        self._footer(data)
        self.doc.save(output_path)

    # ── PAGE SETUP ────────────────────────────────────────────
    def _setup_page(self):
        sec = self.doc.sections[0]
        sec.page_width  = Cm(29.7); sec.page_height = Cm(21.0)
        sec.left_margin = sec.right_margin = Cm(1.8)
        sec.top_margin  = sec.bottom_margin = Cm(1.5)
        self.doc.styles["Normal"].font.name = "Calibri"
        self.doc.styles["Normal"].font.size = Pt(10)

    # ── SECTION HEADING ───────────────────────────────────────
    def _sec_heading(self, number, title, source_note=None):
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after  = Pt(2)
        r = p.add_run(" " + number + ". " + title + " ")
        r.font.name = "Calibri"; r.font.size = Pt(12)
        r.font.bold = True; r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        pPr = p._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), C["dark_blue"]); shd.set(qn("w:val"), "clear")
        pPr.append(shd)
        if source_note:
            p2 = self.doc.add_paragraph()
            p2.paragraph_format.space_before = Pt(0)
            p2.paragraph_format.space_after  = Pt(4)
            add_run(p2, source_note, size=8, italic=True, color=C["mid_blue"])

    # ── HEADER BLOCK ──────────────────────────────────────────
    def _header_block(self, data):
        table = self.doc.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["dark_blue"])

        c0 = table.rows[0].cells[0]; c0.width = Cm(10)
        set_bg(c0, C["dark_blue"]); set_borders(c0, color="FFFFFF", size=6)
        p = c0.paragraphs[0]; p.paragraph_format.space_before = Pt(4)
        add_run(p, "Nascent Info Technologies Pvt. Ltd.", bold=True, size=11, color="FFFFFF")
        p2 = c0.add_paragraph()
        add_run(p2, "A-805, Shapath IV, SG Highway, Prahlad Nagar, Ahmedabad 380015", size=8, color="BDD7EE")
        p3 = c0.add_paragraph()
        add_run(p3, "www.nascentinfo.com | nascent.tender@nascentinfo.com", size=8, color="BDD7EE")
        p4 = c0.add_paragraph(); p4.paragraph_format.space_after = Pt(4)
        add_run(p4, "MSME | CMMI V2.0 L3 | ISO 9001 | ISO 27001 | ISO 20000", size=8, color="FFF2CC")

        c1 = table.rows[0].cells[1]; c1.width = Cm(15.5)
        set_bg(c1, C["mid_blue"]); set_borders(c1, color="FFFFFF", size=6)
        p = c1.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        add_run(p, "BID / NO-BID ANALYSIS", bold=True, size=16, color="FFFFFF")

        tender_title = strip_emojis(data.get("tender_name", data.get("org_name", "")))[:100]
        p2 = c1.add_paragraph(); p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_run(p2, tender_title, size=10, color="DEEAF1")

        verdict_data = data.get("overall_verdict", {})
        _raw = data.get("verdict") or verdict_data.get("verdict", "PENDING REVIEW")
        verdict = strip_emojis(_raw)
        vcolor  = verdict_data.get("color", "BLUE")
        v_txt   = {"GREEN":"FFF2CC","AMBER":"FFF2CC","RED":"FCE4D6","BLUE":"DEEAF1"}.get(vcolor,"DEEAF1")
        p3 = c1.add_paragraph(); p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p3.paragraph_format.space_after = Pt(4)
        add_run(p3, verdict, bold=True, size=13, color=v_txt)

        meta = (
            f"Tender No: {data.get('tender_no','—')} | "
            f"Prepared: {datetime.now().strftime('%d-%b-%Y')} | "
            f"Source: {', '.join(data.get('files_processed', ['Uploaded documents']))[:60]}"
        )
        p4 = c1.add_paragraph(); p4.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p4.paragraph_format.space_after = Pt(4)
        add_run(p4, meta, size=7, color="DEEAF1")
        self.doc.add_paragraph()

    # ── SECTION 1: TENDER SNAPSHOT ────────────────────────────
    def _section_snapshot(self, data):
        self._sec_heading("1", "Tender Snapshot",
                          "Source: NIT + Portal + RFP — all key fields extracted")
        fields = [
            ("Tender No. / NIT No.",     data.get("tender_no", "—")),
            ("Tender ID (Portal)",        data.get("tender_id", "—")),
            ("T247 ID",                   data.get("t247_id", "—")),
            ("Organization / Department", data.get("org_name", "—")),
            ("Sub-Department",            data.get("dept_name", "—")),
            ("Tender Name",               data.get("tender_name", "—")),
            ("Portal / Website",          data.get("portal", "—")),
            ("Form of Contract",          data.get("tender_type", "—")),
            ("No. of Covers / Envelopes", data.get("no_of_covers", "—")),
            ("Bid Submission Start",      data.get("bid_start_date", "—")),
            ("Bid Submission Deadline",   data.get("bid_submission_date", "—")),
            ("Technical Bid Opening",     data.get("bid_opening_date", "—")),
            ("Commercial Bid Opening",    data.get("commercial_opening_date", "—")),
            ("Mode of Selection / Eval.", data.get("mode_of_selection", "—")),
            ("Pre-Bid Meeting",           data.get("prebid_meeting", "Not specified")),
            ("Pre-Bid Query Deadline",    data.get("prebid_query_date", "Not specified")),
            ("Estimated Cost",            data.get("estimated_cost", "Not mentioned — verify portal")),
            ("Tender Fee",                data.get("tender_fee", "—")),
            ("EMD / Bid Security",        data.get("emd", "—")),
            ("EMD Exemption",             data.get("emd_exemption", "—")),
            ("Performance Bank Guarantee",data.get("performance_security", "—")),
            ("Period of Work / Contract", data.get("contract_period", "—")),
            ("Bid Validity",              data.get("bid_validity", "—")),
            ("Post-Implementation / AMC", data.get("post_implementation", "—")),
            ("Technology (Mandatory)",    data.get("technology_mandatory", "—")),
            ("Project Location",          data.get("location", "—")),
            ("Contact Officer",           data.get("contact", "—")),
            ("JV / Consortium",           data.get("jv_allowed", "Not specified — verify T&C")),
        ]
        highlight = {"Bid Submission Deadline","EMD / Bid Security","Tender Fee",
                     "Estimated Cost","Pre-Bid Query Deadline","Pre-Bid Meeting"}
        table = self.doc.add_table(rows=0, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        for idx, (key, raw_val) in enumerate(fields):
            val = flatten_value(raw_val)
            if val == "—": continue  # skip empty rows
            row = table.add_row(); row.height = Cm(0.75)
            bg_l = "D6E4F0" if idx % 2 == 0 else "E8F0F8"
            bg_v = C["white"] if idx % 2 == 0 else C["alt_row"]
            c0 = row.cells[0]; c0.width = Cm(7)
            set_bg(c0, bg_l); set_borders(c0)
            cell_write(c0, key, bold=True, size=9, color=C["dark_blue"])
            c1 = row.cells[1]; c1.width = Cm(18.5)
            set_bg(c1, bg_v); set_borders(c1)
            hl = key in highlight and val not in ("—","Not mentioned — verify portal","Not specified")
            cell_write(c1, val, bold=hl, size=9, color=(C["orange"] if hl else None))
        self.doc.add_paragraph()

    # ── SECTION 2: JV / CONSORTIUM CONDITIONS ─────────────────
    def _section_jv_conditions(self, data):
        jv_conditions = data.get("jv_conditions", [])
        jv_text = data.get("jv_allowed", "—")
        if not jv_conditions and jv_text in ("—", "", "Not specified — verify T&C", None):
            return
        self._sec_heading("2", "JV / Consortium Conditions",
                          "Source: Tender T&C — extracted conditions for joint venture participation")
        if jv_conditions:
            table = self.doc.add_table(rows=0, cols=2)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            set_table_borders(table, color=C["mid_blue"])
            for idx, cond in enumerate(jv_conditions):
                row = table.add_row()
                bg = C["white"] if idx % 2 == 0 else C["alt_row"]
                c0 = row.cells[0]; c0.width = Cm(1.2)
                set_bg(c0, C["label_col"]); set_borders(c0)
                cell_write(c0, str(idx+1), bold=True, size=9, color=C["dark_blue"],
                           align=WD_ALIGN_PARAGRAPH.CENTER)
                c1 = row.cells[1]; c1.width = Cm(24.3)
                set_bg(c1, bg); set_borders(c1)
                cell_write(c1, strip_emojis(str(cond)), size=9)
        else:
            p = self.doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.2)
            add_run(p, jv_text, size=9)
        self.doc.add_paragraph()

    # ── SECTION 3: SCOPE OF WORK ──────────────────────────────
    def _section_scope(self, data):
        self._sec_heading("3", "Scope of Work",
                          "Source: Tender document — deliverables, phases, and technical requirements")
        scope_items = data.get("scope_items", [])
        if not scope_items:
            p = self.doc.add_paragraph()
            add_run(p, "Scope not extracted — refer tender document.", italic=True, size=9)
        else:
            table = self.doc.add_table(rows=0, cols=2)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            set_table_borders(table, color=C["mid_blue"])
            for idx, item in enumerate(scope_items):
                row = table.add_row()
                bg = C["white"] if idx % 2 == 0 else C["alt_row"]
                c0 = row.cells[0]; c0.width = Cm(1.2)
                set_bg(c0, C["label_col"]); set_borders(c0)
                cell_write(c0, str(idx+1), bold=True, size=9, color=C["dark_blue"],
                           align=WD_ALIGN_PARAGRAPH.CENTER)
                c1 = row.cells[1]; c1.width = Cm(24.3)
                set_bg(c1, bg); set_borders(c1)
                cell_write(c1, strip_emojis(str(item)), size=9)
        self.doc.add_paragraph()

    # ── SHARED: CRITERIA TABLE ────────────────────────────────
    def _criteria_table(self, criteria, headers):
        if not criteria:
            p = self.doc.add_paragraph()
            add_run(p, "No criteria extracted — refer tender document.", italic=True, size=9)
            return
        table = self.doc.add_table(rows=1, cols=len(headers))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        hrow = table.rows[0]; repeat_header(hrow)
        for cell, hdr in zip(hrow.cells, headers):
            set_bg(cell, C["dark_blue"]); set_borders(cell, color="FFFFFF", size=4)
            p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(3); p.paragraph_format.space_after = Pt(3)
            add_run(p, hdr, bold=True, size=9, color="FFFFFF")
        for ri, item in enumerate(criteria):
            row = table.add_row()
            bg = C["white"] if ri % 2 == 0 else C["alt_row"]
            raw_status = item.get("nascent_status", "Review")
            sym = clean_status(raw_status)
            sc  = item.get("nascent_color") or status_color(sym)
            s_bg, s_tc = STATUS_STYLE.get(sc, ("blue_bg","blue_text"))
            cells = row.cells
            set_bg(cells[0], C["label_col"]); set_borders(cells[0])
            cell_write(cells[0], str(item.get("sl_no", ri+1)), bold=True, size=9,
                       color=C["dark_blue"], align=WD_ALIGN_PARAGRAPH.CENTER)
            set_bg(cells[1], bg); set_borders(cells[1])
            cell_write(cells[1], item.get("clause_ref","—"), size=8, align=WD_ALIGN_PARAGRAPH.CENTER)
            set_bg(cells[2], bg); set_borders(cells[2])
            cell_write(cells[2], strip_emojis(item.get("criteria","")), size=9)
            set_bg(cells[3], bg); set_borders(cells[3])
            cell_write(cells[3], strip_emojis(item.get("details","")), size=8)
            set_bg(cells[4], C[s_bg]); set_borders(cells[4])
            cell_write(cells[4], sym, bold=True, size=8, color=C[s_tc],
                       align=WD_ALIGN_PARAGRAPH.CENTER)
            set_bg(cells[5], C[s_bg] if sc != "BLUE" else bg); set_borders(cells[5])
            cell_write(cells[5], strip_emojis(item.get("nascent_remark","")), size=8)

    # ── SECTION 4: PQ CRITERIA ────────────────────────────────
    def _section_pq(self, data):
        self._sec_heading("4", "Pre-Qualification (PQ) Criteria",
                          "All criteria reproduced word-for-word from tender | "
                          "Nascent status + pre-bid query drafted for every gap")
        headers = ["Sr.", "Clause /\nPage", "Eligibility Criteria\n(word-for-word from tender)",
                   "Documents Required", "Nascent\nStatus", "Remarks / Pre-Bid Query Draft"]
        self._criteria_table(data.get("pq_criteria",[]), headers)
        self.doc.add_paragraph()

    # ── SECTION 5: TQ CRITERIA (optional) ────────────────────
    def _section_tq(self, data):
        tq = data.get("tq_criteria", [])
        if not tq:
            return
        self._sec_heading("5", "Technical Evaluation (TQ) Criteria",
                          "Scoring criteria from tender — Nascent estimated marks")
        headers = ["Sr.", "Clause /\nPage", "Evaluation Criteria",
                   "Max Marks / Details", "Nascent\nStatus", "Score Justification"]
        self._criteria_table(tq, headers)
        self.doc.add_paragraph()

    # ── SECTION 6: PAYMENT TERMS ──────────────────────────────
    def _section_payment(self, data):
        self._sec_heading("6", "Payment Schedule & Terms",
                          "Source: Tender document — milestone-based payment schedule")
        items = data.get("payment_terms", [])
        if not items:
            items = [
                f"Contract Period: {flatten_value(data.get('contract_period','As per tender'))}",
                f"EMD: {flatten_value(data.get('emd','As per tender'))}",
                f"Performance Security: {flatten_value(data.get('performance_security','As per tender'))}",
                "Payment schedule: Not explicitly extracted — refer tender document.",
            ]
        table = self.doc.add_table(rows=0, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        for idx, item in enumerate(items):
            row = table.add_row()
            bg = C["white"] if idx % 2 == 0 else C["alt_row"]
            c0 = row.cells[0]; c0.width = Cm(1.2)
            set_bg(c0, C["label_col"]); set_borders(c0)
            cell_write(c0, str(idx+1), bold=True, size=9, color=C["dark_blue"],
                       align=WD_ALIGN_PARAGRAPH.CENTER)
            c1 = row.cells[1]; c1.width = Cm(24.3)
            set_bg(c1, bg); set_borders(c1)
            cell_write(c1, strip_emojis(flatten_value(item)), size=9)
        self.doc.add_paragraph()

    # ── SECTION 7: PENALTY & RISK ─────────────────────────────
    def _section_penalty(self, data):
        penalty_clauses = data.get("penalty_clauses", [])
        if not penalty_clauses:
            return
        self._sec_heading("7", "Penalty & Risk Summary",
                          "Source: Tender T&C — LD, blacklisting, and other risk clauses")
        table = self.doc.add_table(rows=1, cols=4)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        hrow = table.rows[0]
        for cell, hdr in zip(hrow.cells, ["Type", "Trigger Condition", "Penalty / Amount", "Max Cap / Clause"]):
            set_bg(cell, C["red_text"]); set_borders(cell, color="FFFFFF")
            p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_run(p, hdr, bold=True, size=9, color="FFFFFF")
        for ri, item in enumerate(penalty_clauses):
            row = table.add_row()
            bg = C["red_bg"] if ri % 2 == 0 else C["white"]
            vals = [
                item.get("type",""), item.get("condition",""),
                item.get("penalty",""),
                (item.get("max_cap","") + " | " + item.get("clause_ref","")).strip(" | ")
            ]
            for cell, val in zip(row.cells, vals):
                set_bg(cell, bg); set_borders(cell)
                cell_write(cell, str(val), size=9)
        self.doc.add_paragraph()

    # ── SECTION 8: PORTAL vs RFP DISCREPANCIES ───────────────
    def _section_portal_discrepancies(self, data):
        discrepancies = data.get("portal_vs_rfp_discrepancies", [])
        if not discrepancies:
            return
        self._sec_heading("8", "Portal vs RFP Discrepancies",
                          "Mismatches between tender portal display and RFP document — each requires a pre-bid query")
        table = self.doc.add_table(rows=1, cols=4)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        hrow = table.rows[0]
        for cell, hdr in zip(hrow.cells,
                             ["Field", "Portal Shows", "RFP States", "Action / Pre-Bid Query"]):
            set_bg(cell, C["dark_blue"]); set_borders(cell, color="FFFFFF")
            p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_run(p, hdr, bold=True, size=9, color="FFFFFF")
        for ri, item in enumerate(discrepancies):
            row = table.add_row()
            bg = C["amber_bg"] if ri % 2 == 0 else C["white"]
            vals = [
                item.get("field",""), item.get("portal_says",""),
                item.get("rfp_says",""), item.get("action","")
            ]
            for ci, (cell, val) in enumerate(zip(row.cells, vals)):
                set_bg(cell, bg if ci < 3 else C["blue_bg"]); set_borders(cell)
                cell_write(cell, str(val), size=9,
                           bold=(ci == 0), color=(C["dark_blue"] if ci == 0 else None))
        self.doc.add_paragraph()

    # ── SECTION 9: PRE-BID QUERIES (CONSOLIDATED) ─────────────
    def _section_prebid_queries(self, data):
        queries = data.get("prebid_queries", [])
        # Also gather queries from PQ gaps
        pq_queries = []
        for item in data.get("pq_criteria", []):
            sc = item.get("nascent_color","BLUE")
            if sc in ("RED","AMBER"):
                remark = item.get("nascent_remark","")
                if remark and len(remark) > 30:
                    pq_queries.append({
                        "clause": item.get("clause_ref","—"),
                        "rfp_text": item.get("criteria","")[:200],
                        "query": remark
                    })
        # Merge: explicit queries first, then PQ-derived
        all_queries = list(queries)
        seen_clauses = {q.get("clause","") for q in all_queries}
        for q in pq_queries:
            if q.get("clause","") not in seen_clauses:
                all_queries.append(q)

        if not all_queries:
            return

        prebid_deadline = data.get("prebid_query_date","—")
        prebid_contact  = flatten_value(data.get("contact","—"))
        # Just use email if available, else name
        contact_raw = data.get("contact", {})
        if isinstance(contact_raw, dict):
            prebid_contact = contact_raw.get("email") or contact_raw.get("name") or "Refer tender document"
        self._sec_heading("9", "Pre-Bid Queries — Consolidated List",
                          f"Submit to: {prebid_contact} | Deadline: {prebid_deadline}")

        table = self.doc.add_table(rows=1, cols=4)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        hrow = table.rows[0]
        for cell, hdr in zip(hrow.cells,
                             ["Q No.", "Clause / Section Ref.", "RFP Text (Clause)", "Query / Clarification"]):
            set_bg(cell, C["dark_blue"]); set_borders(cell, color="FFFFFF")
            p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_run(p, hdr, bold=True, size=9, color="FFFFFF")
        for qi, q in enumerate(all_queries):
            row = table.add_row()
            bg = C["white"] if qi % 2 == 0 else C["alt_row"]
            c0 = row.cells[0]; c0.width = Cm(1.2)
            set_bg(c0, C["label_col"]); set_borders(c0)
            cell_write(c0, f"Q{qi+1}", bold=True, size=9, color=C["dark_blue"],
                       align=WD_ALIGN_PARAGRAPH.CENTER)
            c1 = row.cells[1]; c1.width = Cm(4.0)
            set_bg(c1, bg); set_borders(c1)
            cell_write(c1, strip_emojis(str(q.get("clause","—"))), size=9)
            c2 = row.cells[2]; c2.width = Cm(8.0)
            set_bg(c2, bg); set_borders(c2)
            rfp_txt = q.get("rfp_text", "")
            cell_write(c2, strip_emojis(str(rfp_txt))[:300] if rfp_txt else "—",
                       size=8, italic=True, color=C["gray"])
            c3 = row.cells[3]; c3.width = Cm(12.3)
            set_bg(c3, C["blue_bg"]); set_borders(c3)
            cell_write(c3, strip_emojis(str(q.get("query",""))), size=9)
        self.doc.add_paragraph()

    # ── SECTION 10: BID / NO-BID RECOMMENDATION ───────────────
    def _section_recommendation(self, data):
        self._sec_heading("10", "Bid / No-Bid Recommendation")
        verdict_data = data.get("overall_verdict", {})
        green_count = verdict_data.get("green", 0)
        amber_count = verdict_data.get("amber", 0)
        red_count   = verdict_data.get("red",   0)

        # Score summary row
        stats = [
            (str(green_count), "Criteria Met",   "green_bg", "green_text"),
            (str(amber_count), "Conditional",    "amber_bg", "amber_text"),
            (str(red_count),   "Not Met",        "red_bg",   "red_text"),
        ]
        tbl1 = self.doc.add_table(rows=1, cols=3)
        tbl1.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(tbl1, color=C["mid_blue"])
        for cell, (count, label, bg, tc) in zip(tbl1.rows[0].cells, stats):
            set_bg(cell, C[bg]); set_borders(cell, color="FFFFFF", size=6)
            p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(4)
            add_run(p, count, bold=True, size=20, color=C[tc])
            p2 = cell.add_paragraph(); p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p2.paragraph_format.space_after = Pt(4)
            add_run(p2, label, size=9, color=C[tc])
        self.doc.add_paragraph()

        vcolor = verdict_data.get("color","BLUE")
        s_bg, s_tc = STATUS_STYLE.get(vcolor, ("blue_bg","blue_text"))
        _rv = data.get("verdict") or verdict_data.get("verdict","PENDING REVIEW")
        verdict = strip_emojis(_rv)
        reason  = strip_emojis(data.get("reason") or verdict_data.get("reason",""))

        assessment_rows = [
            ("Financial Eligibility",
             f"Avg Turnover (3yr): Rs. 17.18 Cr | Net Worth: Rs. 26.09 Cr | "
             f"MSME: UDYAM-GJ-01-0007420"),
            ("Company Registration",   "Private Ltd. since 2006 — 19 years in operation"),
            ("CMMI Certification",     "CMMI V2.0 Level 3 — valid till 19-Dec-2026"),
            ("ISO Certifications",     "ISO 9001:2015 | ISO 27001:2022 | ISO 20000-1:2018 — all valid till Sep-2028"),
            ("GIS / IT Experience",    "9 major projects: AMC, JuMC, VMC, KVIC, PCSCL, TCGL, BMC, NSO, NP Lalganj"),
            ("Mobile App Experience",  "KVIC PAN India Mobile GIS | BMC Android+iOS GIS | AMC Heritage App (AR)"),
            ("Employee Strength",      "67 employees: 11 GIS | 21 IT/Dev | QA, PM, BA, Support teams"),
            ("MSME Status",            "UDYAM-GJ-01-0007420 — EMD exemption eligible as per PPP for MSEs 2012"),
            ("PQ Summary",             f"Met: {green_count}   Conditional: {amber_count}   Not Met: {red_count}"),
            ("Key Reasons",
             strip_emojis(" | ".join(data.get("key_reasons",[])))
             if data.get("key_reasons") else reason[:300]),
            ("FINAL RECOMMENDATION",   verdict + ("\n" + reason[:400] if reason else "")),
        ]
        tbl2 = self.doc.add_table(rows=0, cols=2)
        tbl2.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(tbl2, color=C["mid_blue"])
        for ri, (key, val) in enumerate(assessment_rows):
            row = tbl2.add_row()
            is_final = (key == "FINAL RECOMMENDATION")
            c0 = row.cells[0]; c0.width = Cm(7)
            set_bg(c0, C["dark_blue"] if is_final else C["label_col"]); set_borders(c0)
            cell_write(c0, key, bold=True,
                       size=(11 if is_final else 9),
                       color=("FFFFFF" if is_final else C["dark_blue"]))
            c1 = row.cells[1]; c1.width = Cm(18.5)
            set_bg(c1, C[s_bg] if is_final else
                   (C["white"] if ri % 2 == 0 else C["alt_row"])); set_borders(c1)
            cell_write(c1, val, bold=is_final,
                       size=(12 if is_final else 9),
                       color=(C[s_tc] if is_final else None))
        self.doc.add_paragraph()

    # ── SECTION 11: IMMEDIATE ACTION ITEMS ───────────────────
    def _section_action_items(self, data):
        self._sec_heading("11", "Immediate Action Items",
                          "Numbered actions with target dates based on bid deadline")
        action_items = data.get("action_items", [])

        # Build from AI action_items + PQ gaps + notes
        if not action_items:
            # Auto-generate from PQ gaps
            for item in data.get("pq_criteria", []):
                sc = item.get("nascent_color","BLUE")
                if sc in ("RED","AMBER"):
                    action_items.append({
                        "action": f"Resolve: {strip_emojis(item.get('criteria',''))[:100]}",
                        "responsible": "Bid Team",
                        "target_date": "Before pre-bid deadline",
                        "priority": "URGENT" if sc == "RED" else "HIGH"
                    })
            for note in data.get("notes",[]):
                action_items.append({
                    "action": strip_emojis(str(note))[:200],
                    "responsible": "Bid Team",
                    "target_date": "Before submission",
                    "priority": "MEDIUM"
                })

        if not action_items:
            action_items = [{
                "action": "Review tender document completely and confirm bid decision with management",
                "responsible": "Bid Team",
                "target_date": "Immediately",
                "priority": "HIGH"
            }]

        priority_bg = {"URGENT": "red_bg", "HIGH": "amber_bg",
                       "MEDIUM": "blue_bg", "LOW": "white"}
        priority_tc = {"URGENT": "red_text", "HIGH": "amber_text",
                       "MEDIUM": "blue_text", "LOW": "dark"}

        table = self.doc.add_table(rows=1, cols=5)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        hrow = table.rows[0]
        for cell, hdr in zip(hrow.cells,
                             ["#", "Action Required", "Responsible", "Target Date", "Priority"]):
            set_bg(cell, C["dark_blue"]); set_borders(cell, color="FFFFFF")
            p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_run(p, hdr, bold=True, size=9, color="FFFFFF")
        for ai, item in enumerate(action_items[:20]):
            row = table.add_row()
            pri = str(item.get("priority","MEDIUM")).upper()
            bg  = priority_bg.get(pri, "white")
            tc  = priority_tc.get(pri, "dark")
            c0 = row.cells[0]; c0.width = Cm(0.9)
            set_bg(c0, C["label_col"]); set_borders(c0)
            cell_write(c0, str(ai+1), bold=True, size=9, color=C["dark_blue"],
                       align=WD_ALIGN_PARAGRAPH.CENTER)
            c1 = row.cells[1]; c1.width = Cm(13.0)
            set_bg(c1, C[bg]); set_borders(c1)
            cell_write(c1, strip_emojis(str(item.get("action",""))), size=9)
            c2 = row.cells[2]; c2.width = Cm(4.0)
            set_bg(c2, C["alt_row"]); set_borders(c2)
            cell_write(c2, str(item.get("responsible","Bid Team")), size=8)
            c3 = row.cells[3]; c3.width = Cm(4.0)
            set_bg(c3, C["alt_row"]); set_borders(c3)
            cell_write(c3, str(item.get("target_date","")), size=8)
            c4 = row.cells[4]; c4.width = Cm(3.6)
            set_bg(c4, C[bg]); set_borders(c4)
            cell_write(c4, pri, bold=True, size=8, color=C[tc],
                       align=WD_ALIGN_PARAGRAPH.CENTER)
        self.doc.add_paragraph()

    # ── SECTION 12: AUTHORIZATION ─────────────────────────────
    def _section_authorization(self):
        self._sec_heading("12", "Authorization")
        table = self.doc.add_table(rows=2, cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, color=C["mid_blue"])
        for cell, hdr in zip(table.rows[0].cells, ["Prepared By","Reviewed By","Approved By"]):
            set_bg(cell, C["dark_blue"]); set_borders(cell, color="FFFFFF")
            cell_write(cell, hdr, bold=True, size=10, color="FFFFFF",
                       align=WD_ALIGN_PARAGRAPH.CENTER)
        names = ["Parthav Thakkar\nBid Executive\nNascent Info Technologies Pvt. Ltd.", "—", "—"]
        for cell, name in zip(table.rows[1].cells, names):
            set_bg(cell, C["alt_row"]); set_borders(cell)
            cell_write(cell, name, size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
        row3 = table.add_row()
        for cell in row3.cells:
            set_bg(cell, C["white"]); set_borders(cell)
            cell_write(cell, "Date: _______________  | Signature: _____________________",
                       size=9, color=C["gray"], align=WD_ALIGN_PARAGRAPH.CENTER)
        self.doc.add_paragraph()

    # ── FOOTER ────────────────────────────────────────────────
    def _footer(self, data):
        footer = self.doc.sections[0].footer
        fp = footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = fp.add_run(
            "CONFIDENTIAL — Bid/No-Bid Analysis | "
            + data.get("tender_no","—") + " | "
            + "Nascent Info Technologies Pvt. Ltd. | "
            + datetime.now().strftime("%d %b %Y")
            + " | For Internal Use Only"
        )
        r.font.size = Pt(7); r.font.name = "Calibri"
        r.font.color.rgb = RGBColor(0x80,0x80,0x80); r.font.italic = True
