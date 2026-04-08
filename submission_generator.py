"""
Submission Package Generator
Generates all bid submission documents auto-filled from Nascent Profile + tender data
"""
import json, re
from pathlib import Path
from datetime import datetime

try:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_OK = True
except ImportError:
    DOCX_OK = False

BASE_DIR = Path(__file__).parent
PROFILE_PATH = BASE_DIR / "nascent_profile.json"

# ─── Nascent defaults (fallback if profile not saved) ───
DEFAULT_PROFILE = {
    "name": "Nascent Info Technologies Pvt. Ltd.",
    "cin": "U72200GJ2006PTC048723",
    "pan": "AACCN3670J",
    "gstin": "24AACCN3670J1ZG",
    "msme": "UDYAM-GJ-01-0007420",
    "signatory": "Hitesh Patel",
    "signatory_designation": "Chief Administrative Officer (CAO)",
    "md": "Maulik Bhagat",
    "address": "Ahmedabad, Gujarat - 380 015",
    "turnover_fy1": "16.36", "turnover_fy3_label": "2022-23",
    "turnover_fy2": "16.36", "turnover_fy2_label": "2023-24",
    "turnover_fy3": "18.83", "turnover_fy1_label": "2024-25",
    "net_worth": "26.09",
    "ca_firm": "Anuj J. Sharedalal & Co.",
    "cmmi": "CMMI V2.0 Level 3",
    "cmmi_valid": "19-Dec-2026",
    "iso_9001": "08-Sep-2028",
    "iso_27001": "08-Sep-2028",
    "iso_20000": "08-Sep-2028",
    "total_employees": "67",
    "it_employees": "21",
}

def load_profile():
    if PROFILE_PATH.exists():
        try:
            p = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            # Merge with defaults for any missing keys
            merged = dict(DEFAULT_PROFILE)
            merged.update(p)
            return merged
        except Exception:
            pass
    return DEFAULT_PROFILE

def today_str():
    return datetime.now().strftime("%d %B %Y")

def fy_label(n):
    """Returns FY label like 2022-23"""
    y = datetime.now().year
    labels = [f"{y-3}-{str(y-2)[-2:]}", f"{y-2}-{str(y-1)[-2:]}", f"{y-1}-{str(y)[-2:]}"]
    return labels[n] if n < len(labels) else ""

# ─── Word doc helpers ───
def new_doc():
    doc = Document()
    # Set margins
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3)
        section.right_margin = Cm(2.5)
    # Default font
    doc.styles['Normal'].font.name = 'Calibri'
    doc.styles['Normal'].font.size = Pt(11)
    return doc

def h(doc, text, size=13, bold=True, center=False, color=None):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    if color:
        r.font.color.rgb = RGBColor(*color)
    return p

def para(doc, text, size=11, bold=False, center=False):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.bold = bold
    return p

def letterhead(doc, p_data):
    """Add company letterhead block"""
    h(doc, p_data["name"], size=14, bold=True, center=True, color=(0, 70, 127))
    para(doc, f"CIN: {p_data['cin']}  |  PAN: {p_data['pan']}  |  GSTIN: {p_data['gstin']}", size=9, center=True)
    para(doc, p_data.get("address","Ahmedabad, Gujarat"), size=9, center=True)
    para(doc, f"MSME/UDYAM: {p_data['msme']}", size=9, center=True)
    doc.add_paragraph().add_run("─" * 80).font.size = Pt(8)

def sign_block(doc, p_data):
    """Add signature block"""
    doc.add_paragraph()
    para(doc, "For " + p_data["name"])
    doc.add_paragraph()
    doc.add_paragraph()
    doc.add_paragraph()
    para(doc, "Authorised Signatory")
    para(doc, f"Name: {p_data['signatory']}")
    para(doc, f"Designation: {p_data.get('signatory_designation','CAO')}")
    para(doc, f"Date: {today_str()}")
    para(doc, "Place: Ahmedabad")

def save_doc(doc, path):
    doc.save(str(path))
    return path


# ═══════════════════════════════════════
# DOCUMENT GENERATORS
# ═══════════════════════════════════════

def gen_cover_letter(tender, p_data, out_path):
    doc = new_doc()
    letterhead(doc, p_data)
    doc.add_paragraph()
    para(doc, f"Date: {today_str()}", bold=True)
    doc.add_paragraph()
    para(doc, "To,")
    para(doc, tender.get("org_name", "[Organization Name]"), bold=True)
    para(doc, tender.get("location", ""))
    doc.add_paragraph()
    para(doc, f"Sub: Submission of Bid for {tender.get('tender_name', tender.get('brief',''))}")
    para(doc, f"Ref: Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    para(doc, "Dear Sir/Madam,")
    doc.add_paragraph()
    body = (
        f"We, {p_data['name']}, are pleased to submit our bid in response to the above-mentioned "
        f"tender issued by your esteemed organization.\n\n"
        f"We have carefully studied all tender documents and confirm that our bid is in complete "
        f"conformance with all terms, conditions, and technical specifications mentioned in the RFP.\n\n"
        f"We look forward to the opportunity to serve your organization and remain committed to "
        f"delivering the highest quality of services."
    )
    for line in body.split("\n\n"):
        para(doc, line)
        doc.add_paragraph()
    para(doc, "Thanking you,")
    para(doc, "Yours faithfully,")
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_non_blacklisting(tender, p_data, out_path):
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "DECLARATION: NON-BLACKLISTING / NON-DEBARMENT", size=12, center=True)
    doc.add_paragraph()
    para(doc, f"Ref: Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    body = (
        f"We, {p_data['name']} (CIN: {p_data['cin']}), hereby solemnly declare and affirm that:\n\n"
        f"1. Our firm has NOT been blacklisted, debarred, or declared ineligible by any Central/State "
        f"Government, PSU, Local Body, or any Public Authority in India or abroad.\n\n"
        f"2. No criminal proceedings have been initiated or are pending against our firm or its Directors/Partners.\n\n"
        f"3. We have not been convicted of any offence relating to professional conduct, financial irregularity, "
        f"or corrupt practices.\n\n"
        f"4. All information provided in this bid is true and correct to the best of our knowledge.\n\n"
        f"We understand that if any of the above declarations are found to be false, our bid shall be "
        f"liable for rejection and we may be subject to legal action."
    )
    for line in body.split("\n\n"):
        para(doc, line)
        doc.add_paragraph()
    doc.add_paragraph()
    para(doc, "Verified at Ahmedabad on this " + today_str())
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_msme_declaration(tender, p_data, out_path):
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "DECLARATION: MSME / EMD EXEMPTION", size=12, center=True)
    doc.add_paragraph()
    para(doc, f"Ref: Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    body = (
        f"We, {p_data['name']}, hereby declare that:\n\n"
        f"1. Our firm is a registered Micro/Small/Medium Enterprise under the MSMED Act, 2006.\n\n"
        f"2. Our UDYAM Registration Number is: {p_data['msme']}\n\n"
        f"3. In accordance with Government of India policy for procurement from MSMEs and the "
        f"relevant provisions of the tender document, we are entitled to exemption from payment "
        f"of Earnest Money Deposit (EMD).\n\n"
        f"4. We hereby claim exemption from EMD amounting to {tender.get('emd','as mentioned in tender')} "
        f"for this tender.\n\n"
        f"5. A copy of our valid UDYAM Registration Certificate is enclosed."
    )
    for line in body.split("\n\n"):
        para(doc, line)
        doc.add_paragraph()
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_financial_standing(tender, p_data, out_path):
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "FINANCIAL STANDING CERTIFICATE", size=12, center=True)
    para(doc, "(Annual Turnover Statement)", size=10, center=True)
    doc.add_paragraph()
    para(doc, f"Ref: Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    para(doc, f"We hereby certify that the annual turnover of {p_data['name']} for the last three financial years is as follows:")
    doc.add_paragraph()
    # Turnover table
    table = doc.add_table(rows=5, cols=3)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "Sr. No."
    hdr_cells[1].text = "Financial Year"
    hdr_cells[2].text = "Annual Turnover (Rs. in Crores)"
    for cell in hdr_cells:
        for para_ in cell.paragraphs:
            for run in para_.runs:
                run.bold = True
    rows_data = [
        ("1", fy_label(0) or "2022-23", f"Rs. {p_data.get('turnover_fy1','16.36')} Crores"),
        ("2", fy_label(1) or "2023-24", f"Rs. {p_data.get('turnover_fy2','16.36')} Crores"),
        ("3", fy_label(2) or "2024-25", f"Rs. {p_data.get('turnover_fy3','18.83')} Crores"),
    ]
    for i, (sr, fy, tv) in enumerate(rows_data, 1):
        row = table.rows[i].cells
        row[0].text = sr; row[1].text = fy; row[2].text = tv
    # Average
    avg_row = table.rows[4].cells
    try:
        avg = round((float(p_data.get('turnover_fy1',16.36)) + float(p_data.get('turnover_fy2',16.36)) + float(p_data.get('turnover_fy3',18.83))) / 3, 2)
    except Exception:
        avg = "—"
    avg_row[0].text = ""; avg_row[1].text = "Average (3 Years)"; avg_row[2].text = f"Rs. {avg} Crores"
    for cell in avg_row:
        for p_ in cell.paragraphs:
            for r in p_.runs:
                r.bold = True
    doc.add_paragraph()
    para(doc, f"Net Worth as on last audited Balance Sheet: Rs. {p_data.get('net_worth','26.09')} Crores")
    doc.add_paragraph()
    para(doc, f"Certified by: {p_data.get('ca_firm','[CA Firm Name]')}")
    para(doc, "(Chartered Accountant — to be signed and stamped by CA)")
    doc.add_paragraph()
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_employee_strength(tender, p_data, out_path):
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "DECLARATION: EMPLOYEE STRENGTH", size=12, center=True)
    doc.add_paragraph()
    para(doc, f"Ref: Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    para(doc, f"We hereby declare that {p_data['name']} employs the following full-time professionals as on {today_str()}:")
    doc.add_paragraph()
    table = doc.add_table(rows=1, cols=3)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text = "Category"; hdr[1].text = "Number of Employees"; hdr[2].text = "Role"
    for c in hdr:
        for p_ in c.paragraphs:
            for r in p_.runs:
                r.bold = True
    staff = [
        ("IT / Software Development", p_data.get("it_employees","21"), "Developers, Architects, QA"),
        ("GIS Specialists", "11", "GIS Analysts, Survey Engineers"),
        ("Project Managers / BAs", "8", "PM, BA, Coordinators"),
        ("Support & Operations", "5", "Help Desk, Server Admin"),
        ("Finance & Admin", "10", "Finance, HR, Admin"),
        ("Management", "2", "Directors / CAO"),
        ("TOTAL", p_data.get("total_employees","67"), "All Employees"),
    ]
    for row_data in staff:
        row = table.add_row().cells
        row[0].text = row_data[0]; row[1].text = str(row_data[1]); row[2].text = row_data[2]
    doc.add_paragraph()
    para(doc, "All IT professional employees are engaged in software development, maintenance, or SDLC activities.")
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_integrity_pact(tender, p_data, out_path):
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "INTEGRITY PACT / NO-CANVASSING DECLARATION", size=12, center=True)
    doc.add_paragraph()
    para(doc, f"Ref: Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    clauses = [
        "We have NOT made and will NOT make any offer, payment, gift, or other advantage to any officer or employee of the Purchaser in connection with this tender or any resulting contract.",
        "We confirm that no officer of the Purchaser is interested in this bid, whether as proprietor, director, partner, or in any other capacity.",
        "We have not canvassed or attempted to canvass the support of any government official or authority in relation to this tender.",
        "We will not engage in any collusion, bid rigging, or anti-competitive behavior with other bidders.",
        "We will maintain complete confidentiality of all information provided to us during the tender process.",
        "We agree that any breach of this pact shall render our bid liable to rejection and we may be debarred from future tenders.",
    ]
    for i, clause in enumerate(clauses, 1):
        para(doc, f"{i}. {clause}")
        doc.add_paragraph()
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_mii_declaration(tender, p_data, out_path):
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "MAKE IN INDIA (MII) DECLARATION", size=12, center=True)
    doc.add_paragraph()
    para(doc, f"Ref: Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    body = (
        f"We, {p_data['name']}, hereby declare that:\n\n"
        f"1. Our company is incorporated and registered in India under the Companies Act.\n\n"
        f"2. The software/services offered by us under this bid are developed/designed in India "
        f"by Indian professionals employed by our company.\n\n"
        f"3. Our company qualifies as a 'Class 1 Local Supplier' / 'Class 2 Local Supplier' "
        f"as defined under the Public Procurement (Preference to Make in India) Order, 2017 "
        f"and subsequent amendments.\n\n"
        f"4. The local content in our products/services exceeds the applicable minimum threshold "
        f"as prescribed by the relevant nodal ministry.\n\n"
        f"5. We understand that any false declaration will lead to disqualification and blacklisting."
    )
    for line in body.split("\n\n"):
        para(doc, line)
        doc.add_paragraph()
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_bidder_details(tender, p_data, out_path):
    """Bidder Details Form — standard format"""
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "BIDDER DETAILS FORM", size=12, center=True)
    doc.add_paragraph()
    para(doc, f"Tender No.: {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    para(doc, f"Organization: {tender.get('org_name','')}", bold=True)
    doc.add_paragraph()
    table = doc.add_table(rows=1, cols=2)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text = "Particulars"; hdr[1].text = "Details"
    for c in hdr:
        for p_ in c.paragraphs:
            for r_ in p_.runs:
                r_.bold = True
    fields = [
        ("Name of Bidder", p_data["name"]),
        ("Type of Organization", "Private Limited Company"),
        ("Date of Incorporation", "23 June 2006"),
        ("CIN Number", p_data["cin"]),
        ("PAN Number", p_data["pan"]),
        ("GSTIN", p_data["gstin"]),
        ("MSME / UDYAM No.", p_data["msme"]),
        ("Registered Address", p_data.get("address","Ahmedabad, Gujarat")),
        ("Name of MD / CEO", p_data["md"]),
        ("Authorized Signatory", p_data["signatory"]),
        ("Designation", p_data.get("signatory_designation","CAO")),
        ("Email", "info@nascentinfo.com"),
        ("Phone", "+91-79-XXXXXXXX"),
        ("Website", "www.nascentinfo.com"),
        ("CMMI Certification", f"{p_data.get('cmmi','CMMI Level 3')} (Valid till {p_data.get('cmmi_valid','19-Dec-2026')})"),
        ("ISO 9001:2015", f"Certified (Valid till {p_data.get('iso_9001','08-Sep-2028')})"),
        ("ISO 27001:2022", f"Certified (Valid till {p_data.get('iso_27001','08-Sep-2028')})"),
        ("Total Employees", p_data.get("total_employees","67")),
        ("IT / Dev Employees", p_data.get("it_employees","21")),
        ("Annual Turnover FY 2024-25", f"Rs. {p_data.get('turnover_fy3','18.83')} Crores"),
        ("Net Worth", f"Rs. {p_data.get('net_worth','26.09')} Crores"),
    ]
    for label, val in fields:
        row = table.add_row().cells
        row[0].text = label; row[1].text = val
    doc.add_paragraph()
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_project_experience(tender, p_data, out_path):
    """Project experience table matched to PQ criteria"""
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "PROJECT EXPERIENCE STATEMENT", size=12, center=True)
    doc.add_paragraph()
    para(doc, f"Ref: Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    para(doc, "We hereby furnish details of our key projects demonstrating relevant experience:")
    doc.add_paragraph()
    table = doc.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    headers = ["Sr.", "Client", "Project Name", "Scope", "Value (Cr)", "Status"]
    for i, hdr_text in enumerate(headers):
        table.rows[0].cells[i].text = hdr_text
        for p_ in table.rows[0].cells[i].paragraphs:
            for r in p_.runs:
                r.bold = True
    projects = [
        ("1","Ahmedabad MC (AMC)","GIS System","Web GIS, Property Tax, Asset Management","10.55","Completed"),
        ("2","PCSCL (Pimpri-Chinchwad)","Smart City GIS+ERP","City-wide GIS, ERP Modules, Dashboard","61.19","Ongoing"),
        ("3","KVIC (Central PSU)","Geo Portal + Mobile GIS","GIS Portal, Mobile App, PAN India geo-tagging","5.15","Completed"),
        ("4","Tourism Corp Gujarat (TCGL)","Tourism Portal","Web Portal, GIS, Booking, Payment Gateway","9.31","Completed"),
        ("5","Junagadh MC (JuMC)","GIS System","Web GIS, Survey, Property Mapping","9.78","Ongoing"),
        ("6","Vadodara MC (VMC)","GIS + ERP","Web GIS, ERP Modules","20.50","Completed"),
        ("7","Bhavnagar MC (BMC)","GIS Mobile App","Mobile GIS, Web GIS","4.20","Completed"),
        ("8","Ahmedabad MC (AMC)","Heritage App","Mobile App, AR/QR","4.72","Completed"),
        ("9","CEICED (Gujarat State)","eGov Portal","Web Portal, Mobile App","3.59","Ongoing"),
    ]
    for proj in projects:
        row = table.add_row().cells
        for i, val in enumerate(proj):
            row[i].text = val
    doc.add_paragraph()
    para(doc, "Completion certificates / work order copies available for submission upon request.")
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_prebid_letter(tender, p_data, queries, out_path):
    """Pre-bid query letter"""
    doc = new_doc()
    letterhead(doc, p_data)
    doc.add_paragraph()
    para(doc, f"Date: {today_str()}", bold=True)
    doc.add_paragraph()
    para(doc, "To,")
    para(doc, tender.get("org_name","[Organization]"), bold=True)
    doc.add_paragraph()
    para(doc, f"Sub: Pre-Bid Queries for Tender No. {tender.get('tender_no', tender.get('ref_no',''))}", bold=True)
    doc.add_paragraph()
    para(doc, "Dear Sir/Madam,")
    para(doc, "With reference to the above tender, we request clarification on the following points:")
    doc.add_paragraph()
    table = doc.add_table(rows=1, cols=3)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text = "Sr."; hdr[1].text = "Clause Ref."; hdr[2].text = "Query"
    for c in hdr:
        for p_ in c.paragraphs:
            for r in p_.runs:
                r.bold = True
    for i, q in enumerate(queries[:15], 1):
        qt = q.get("query",q) if isinstance(q,dict) else str(q)
        cl = q.get("clause","—") if isinstance(q,dict) else "—"
        row = table.add_row().cells
        row[0].text = str(i); row[1].text = cl; row[2].text = qt
    doc.add_paragraph()
    para(doc, "We request your kind clarification at the earliest.")
    sign_block(doc, p_data)
    return save_doc(doc, out_path)


def gen_checklist(tender, p_data, docs_generated, out_path):
    """Submission checklist"""
    doc = new_doc()
    letterhead(doc, p_data)
    h(doc, "SUBMISSION CHECKLIST", size=12, center=True)
    para(doc, f"Tender: {tender.get('tender_name', tender.get('brief',''))}", center=True)
    para(doc, f"Tender No.: {tender.get('tender_no', tender.get('ref_no',''))}", center=True)
    doc.add_paragraph()
    table = doc.add_table(rows=1, cols=4)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text = "Sr."; hdr[1].text = "Document"; hdr[2].text = "Status"; hdr[3].text = "Remarks"
    for c in hdr:
        for p_ in c.paragraphs:
            for r in p_.runs:
                r.bold = True
    items = [
        ("Cover Letter", "AUTO-GENERATED",""),
        ("Non-Blacklisting Declaration","AUTO-GENERATED",""),
        ("MSME / EMD Exemption","AUTO-GENERATED",""),
        ("Financial Standing / Turnover","AUTO-GENERATED","CA signature needed"),
        ("Employee Strength Declaration","AUTO-GENERATED",""),
        ("Integrity Pact","AUTO-GENERATED",""),
        ("Make in India Declaration","AUTO-GENERATED",""),
        ("Bidder Details Form","AUTO-GENERATED",""),
        ("Project Experience Table","AUTO-GENERATED",""),
        ("EMD / DD / Bank Guarantee","MANUAL","Arrange before deadline"),
        ("Completion Certificates","MANUAL","Collect from clients"),
        ("CMMI Certificate Copy","MANUAL","Attach certified copy"),
        ("ISO Certificate Copies","MANUAL","Attach certified copies"),
        ("CA Turnover Certificate","MANUAL","CA signature + stamp"),
        ("Solvency Certificate","MANUAL","From your bank"),
        ("DSC Token","MANUAL","Register on portal"),
        ("Pre-Bid Query Letter", "AUTO-GENERATED" if queries_exist(tender) else "IF NEEDED",""),
    ]
    for i, (doc_name, status, remark) in enumerate(items, 1):
        row = table.add_row().cells
        row[0].text = str(i); row[1].text = doc_name; row[2].text = status; row[3].text = remark
    doc.add_paragraph()
    para(doc, f"Generated on: {today_str()}")
    return save_doc(doc, out_path)

def queries_exist(tender):
    return bool(tender.get("prebid_queries"))


# ═══════════════════════════════════════
# MAIN: Generate Full Package
# ═══════════════════════════════════════

def generate_submission_package(tender: dict, output_dir: Path) -> dict:
    """Generate all submission documents. Returns dict of generated files."""
    if not DOCX_OK:
        return {"error": "python-docx not installed"}

    p_data = load_profile()
    output_dir.mkdir(exist_ok=True, parents=True)

    tender_no_safe = re.sub(r'[^\w\-]', '_', tender.get('tender_no', tender.get('t247_id','tender')))[:30]
    pkg_dir = output_dir / f"SubmissionPackage_{tender_no_safe}"
    pkg_dir.mkdir(exist_ok=True, parents=True)

    files = {}
    errors = []

    generators = [
        ("01_CoverLetter",         gen_cover_letter),
        ("02_NonBlacklisting",     gen_non_blacklisting),
        ("03_MSME_EMD_Exemption",  gen_msme_declaration),
        ("04_FinancialStanding",   gen_financial_standing),
        ("05_EmployeeStrength",    gen_employee_strength),
        ("06_IntegrityPact",       gen_integrity_pact),
        ("07_MII_Declaration",     gen_mii_declaration),
        ("08_BidderDetails",       gen_bidder_details),
        ("09_ProjectExperience",   gen_project_experience),
    ]

    for fname, gen_fn in generators:
        try:
            path = pkg_dir / f"{fname}.docx"
            gen_fn(tender, p_data, path)
            files[fname] = str(path)
        except Exception as e:
            errors.append(f"{fname}: {str(e)}")

    # Pre-bid letter if queries exist
    queries = tender.get("prebid_queries", [])
    if queries:
        try:
            path = pkg_dir / "10_PreBidQueryLetter.docx"
            gen_prebid_letter(tender, p_data, queries, path)
            files["10_PreBidLetter"] = str(path)
        except Exception as e:
            errors.append(f"PreBidLetter: {str(e)}")

    # Checklist
    try:
        path = pkg_dir / "00_SubmissionChecklist.docx"
        gen_checklist(tender, p_data, files, path)
        files["00_Checklist"] = str(path)
    except Exception as e:
        errors.append(f"Checklist: {str(e)}")

    # Create ZIP
    import zipfile
    zip_path = output_dir / f"SubmissionPackage_{tender_no_safe}.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for fpath in pkg_dir.glob("*.docx"):
            zf.write(fpath, fpath.name)

    return {
        "status": "success",
        "files": list(files.keys()),
        "count": len(files),
        "zip_file": f"SubmissionPackage_{tender_no_safe}.zip",
        "pkg_dir": str(pkg_dir),
        "errors": errors,
    }
