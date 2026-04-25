"""
PDF Merger v1.0 — Nascent Info Technologies Bid/No-Bid System

Merges all submission documents into ONE PDF in the correct submission order:
1. Cover Letter (on letterhead)
2. Bid submission form / Form 1897
3. EMD / MSME Exemption Letter
4. Company documents (COI, PAN, GST, MSME)
5. Financial documents (Turnover certificate, Balance sheets)
6. Legal declarations (Non-blacklisting, Financial standing, MII)
7. Certifications (CMMI, ISO)
8. Experience documents (Work orders, completion certs)
9. HR documents (Employee strength, EPF)
10. Technical Proposal
11. RFP-specific forms / annexures
12. Stamp paper content

Supports: DOCX → PDF conversion + PDF merge
"""

import os
import re
import json
import subprocess
import tempfile
import shutil
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data"
DOCS_DIR   = BASE_DIR / "docs"
TEMP_DIR   = BASE_DIR / "temp"

# ─────────────────────────────────────────────────────────────────
# DOCUMENT ORDER (submission sequence)
# ─────────────────────────────────────────────────────────────────

SUBMISSION_ORDER = [
    # Priority 1 — Bid forms (always first)
    ("cover",        1, ["cover_letter", "bid_letter", "covering"]),
    ("form_1897",    2, ["1897", "affidavit", "emd_affidavit"]),
    ("emd",          3, ["emd", "msme_emd", "earnest_money", "bid_security"]),

    # Priority 2 — Company docs
    ("coi",          10, ["incorporation", "coi", "certificate_of_incorporation"]),
    ("pan",          11, ["pan_card", "pan"]),
    ("gst",          12, ["gst", "gstin"]),
    ("msme",         13, ["msme", "udyam"]),

    # Priority 3 — Financial
    ("turnover",     20, ["turnover", "ca_certificate", "avg_turnover"]),
    ("balance_2223", 21, ["balance_2223", "2022_23", "fy22", "p&l"]),
    ("balance_2324", 22, ["balance_2324", "2023_24", "fy23"]),
    ("balance_2425", 23, ["balance_2425", "2024_25", "fy24"]),
    ("net_worth",    24, ["net_worth", "networth"]),

    # Priority 4 — Legal declarations
    ("non_blacklist",  30, ["non_blacklist", "blacklisting", "debarment", "non_debarment"]),
    ("financial_und",  31, ["financial_standing", "undertaking", "financial_und"]),
    ("mii",            32, ["make_in_india", "mii", "local_content"]),

    # Priority 5 — Certifications
    ("cmmi",    40, ["cmmi"]),
    ("iso9001", 41, ["iso_9001", "iso9001", "quality"]),
    ("iso27001",42, ["iso_27001", "iso27001", "information_security"]),
    ("iso20000",43, ["iso_20000", "iso20000", "itsm"]),

    # Priority 6 — Experience
    ("exp_1", 50, ["work_order", "wo_", "completion_cert", "experience"]),
    ("exp_2", 51, ["exp_", "project_", "client_cert"]),

    # Priority 7 — HR
    ("emp_strength", 60, ["employee_strength", "epf", "hr"]),
    ("team_cv",      61, ["cv", "resume", "team"]),

    # Priority 8 — Technical
    ("tech_proposal", 70, ["technical_proposal", "techproposal", "tech_prop"]),
    ("methodology",   71, ["methodology", "approach", "technical_approach"]),
    ("timeline",      72, ["timeline", "gantt", "schedule"]),

    # Priority 9 — Forms/Annexures (variable, go last before stamp)
    ("form_",   80, ["form_", "annexure", "annex", "appendix", "schedule"]),

    # Priority 10 — Stamp papers (always last)
    ("stamp",   90, ["stamp_paper", "stamp", "non_judicial"]),

    # Priority 99 — Unknown docs (append at end)
    ("other",   99, []),
]


def _score_doc(filename: str) -> int:
    """Return sort priority for a document based on filename."""
    fname = filename.lower().replace(" ", "_").replace("-", "_")
    for _, priority, keywords in SUBMISSION_ORDER:
        if keywords:
            for kw in keywords:
                if kw in fname:
                    return priority
    return 99  # Unknown — append at end


def _sort_documents(file_paths: List[Path]) -> List[Path]:
    """Sort documents in correct submission order."""
    scored = [(path, _score_doc(path.name)) for path in file_paths]
    scored.sort(key=lambda x: (x[1], x[0].name.lower()))
    return [p for p, _ in scored]


# ─────────────────────────────────────────────────────────────────
# DOCX → PDF CONVERSION
# ─────────────────────────────────────────────────────────────────

def _docx_to_pdf(docx_path: Path, out_dir: Path) -> Optional[Path]:
    """Convert a DOCX to PDF using LibreOffice (available on Render/Linux)."""
    try:
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf",
             "--outdir", str(out_dir), str(docx_path)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            pdf_name = docx_path.stem + ".pdf"
            pdf_path = out_dir / pdf_name
            if pdf_path.exists():
                return pdf_path
    except FileNotFoundError:
        logger.warning("LibreOffice not found — trying unoconv")
        try:
            result = subprocess.run(
                ["unoconv", "-f", "pdf", "-o", str(out_dir), str(docx_path)],
                capture_output=True, text=True, timeout=60
            )
            pdf_path = out_dir / (docx_path.stem + ".pdf")
            if pdf_path.exists():
                return pdf_path
        except FileNotFoundError:
            logger.warning("unoconv not found either — cannot convert DOCX")
    except Exception as e:
        logger.error(f"DOCX→PDF conversion failed: {e}")
    return None


# ─────────────────────────────────────────────────────────────────
# PDF MERGE using PyPDF2 (or pypdf)
# ─────────────────────────────────────────────────────────────────

def _merge_pdfs(pdf_paths: List[Path], output_path: Path) -> bool:
    """Merge multiple PDFs into one using PyPDF2."""
    try:
        try:
            from pypdf import PdfWriter, PdfReader
        except ImportError:
            from PyPDF2 import PdfWriter, PdfReader

        writer = PdfWriter()
        for pdf_path in pdf_paths:
            try:
                reader = PdfReader(str(pdf_path))
                for page in reader.pages:
                    writer.add_page(page)
            except Exception as e:
                logger.warning(f"Could not add {pdf_path.name}: {e}")
                continue

        if len(writer.pages) == 0:
            return False

        with open(output_path, "wb") as f:
            writer.write(f)

        logger.info(f"Merged {len(pdf_paths)} PDFs → {output_path.name} ({output_path.stat().st_size // 1024} KB)")
        return True

    except ImportError:
        logger.error("PyPDF2/pypdf not installed. Run: pip install pypdf")
        return False
    except Exception as e:
        logger.error(f"PDF merge failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────
# ADD COVER PAGE TO MERGED PDF
# ─────────────────────────────────────────────────────────────────

def _create_cover_page_pdf(tender_data: dict, output_path: Path) -> Optional[Path]:
    """Create a professional cover page PDF for the submission package."""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.colors import HexColor

        W, H = A4
        c = canvas.Canvas(str(output_path), pagesize=A4)

        navy  = HexColor('#1F3864')
        blue  = HexColor('#2E75B6')
        white = HexColor('#FFFFFF')
        gray  = HexColor('#666666')

        # Dark navy header band
        c.setFillColor(navy)
        c.rect(0, H-120, W, 120, fill=1, stroke=0)

        # Company name
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 20)
        c.drawCentredString(W/2, H-50, "NASCENT INFO TECHNOLOGIES PVT. LTD.")
        c.setFont("Helvetica", 11)
        c.drawCentredString(W/2, H-72, "GIS  •  Smart City  •  Mobile Applications  •  eGovernance  •  ERP")
        c.setFont("Helvetica", 9)
        c.drawCentredString(W/2, H-95, "CMMI V2.0 Level 3  |  ISO 9001 : 2015  |  ISO 27001 : 2022  |  ISO 20000-1 : 2018  |  MSME")

        # Blue accent bar
        c.setFillColor(blue)
        c.rect(0, H-135, W, 15, fill=1, stroke=0)

        # Title
        c.setFillColor(navy)
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(W/2, H-200, "BID SUBMISSION PACKAGE")

        # Divider line
        c.setStrokeColor(navy)
        c.setLineWidth(1.5)
        c.line(60, H-215, W-60, H-215)

        # Tender name
        tender_name = tender_data.get("tender_name",
                      tender_data.get("brief", "Tender"))[:120]
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(blue)
        c.drawCentredString(W/2, H-255, "For")
        c.setFont("Helvetica-Bold", 13)
        c.setFillColor(navy)

        # Word-wrap tender name
        words = tender_name.split()
        lines, line = [], []
        for w in words:
            line.append(w)
            if len(" ".join(line)) > 70:
                lines.append(" ".join(line[:-1]))
                line = [w]
        if line:
            lines.append(" ".join(line))

        y = H-285
        for ln in lines[:4]:
            c.drawCentredString(W/2, y, ln)
            y -= 22

        # Org name
        c.setFont("Helvetica", 12)
        c.setFillColor(gray)
        c.drawCentredString(W/2, y-15,
            tender_data.get("org_name", ""))

        # Info box
        c.setFillColor(HexColor('#EBF3FB'))
        c.rect(60, 280, W-120, 120, fill=1, stroke=0)
        c.setStrokeColor(navy)
        c.setLineWidth(0.5)
        c.rect(60, 280, W-120, 120, fill=0, stroke=1)

        info = [
            ("Tender No.", tender_data.get("tender_no",
             tender_data.get("ref_no", ""))),
            ("Submission Deadline", tender_data.get("bid_submission_date",
             tender_data.get("deadline", ""))),
            ("EMD", tender_data.get("emd", "")),
            ("Bid Validity", tender_data.get("bid_validity", "120 days")),
        ]
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(navy)
        iy = 375
        for label, value in info:
            if value:
                c.drawString(80, iy, f"{label}:")
                c.setFont("Helvetica", 10)
                c.drawString(220, iy, str(value)[:60])
                c.setFont("Helvetica-Bold", 10)
                iy -= 20

        # Submitted by
        c.setFillColor(navy)
        c.rect(0, 0, W, 160, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(W/2, 130, "Submitted by:")
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(W/2, 108, "Nascent Info Technologies Pvt. Ltd.")
        c.setFont("Helvetica", 10)
        c.drawCentredString(W/2, 88,
            "A-805, Shapath IV, SG Highway, Prahlad Nagar, Ahmedabad – 380015, Gujarat")
        c.drawCentredString(W/2, 70,
            "+91-79-40200400  |  nascent.tender@nascentinfo.com  |  www.nascentinfo.com")
        c.setFont("Helvetica-Bold", 10)
        from datetime import date
        c.drawCentredString(W/2, 48, f"Date: {date.today().strftime('%d %B %Y')}")

        c.save()
        return output_path

    except ImportError:
        logger.warning("reportlab not installed — cover page skipped")
        return None
    except Exception as e:
        logger.error(f"Cover page creation failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# MAIN PUBLIC API
# ─────────────────────────────────────────────────────────────────

def merge_submission_package(
    t247_id: str,
    tender_data: dict,
    source_dirs: List[Path],
    output_dir: Path,
    include_cover: bool = True,
) -> dict:
    """
    Merge all submission documents for a tender into one PDF.

    Args:
        t247_id:       Tender ID
        tender_data:   Tender analysis data (for cover page)
        source_dirs:   List of directories containing docs (generated + uploaded)
        output_dir:    Where to save the merged PDF
        include_cover: Whether to prepend a cover page

    Returns:
        dict with status, output_path, page_count, file_count, errors
    """
    result = {
        "status":      "error",
        "output_path": "",
        "page_count":  0,
        "file_count":  0,
        "errors":      [],
        "doc_order":   [],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="merge_", dir=str(TEMP_DIR)))

    try:
        # 1. Collect all document files from all source dirs
        all_files = []
        for src_dir in source_dirs:
            if not src_dir.exists():
                continue
            for ext in ["*.pdf", "*.docx", "*.doc"]:
                all_files.extend(src_dir.glob(ext))
            for ext in ["*.pdf", "*.docx", "*.doc"]:
                all_files.extend(src_dir.rglob(ext))

        # Deduplicate
        seen = set()
        unique_files = []
        for f in all_files:
            if f.name not in seen:
                seen.add(f.name)
                unique_files.append(f)

        if not unique_files:
            result["errors"].append("No documents found to merge")
            return result

        # 2. Sort in submission order
        sorted_files = _sort_documents(unique_files)
        result["doc_order"] = [f.name for f in sorted_files]

        # 3. Convert DOCX → PDF
        pdf_files = []
        for doc_path in sorted_files:
            ext = doc_path.suffix.lower()
            if ext == ".pdf":
                # Copy to tmp dir to avoid issues
                tmp_copy = tmp_dir / doc_path.name
                shutil.copy2(doc_path, tmp_copy)
                pdf_files.append(tmp_copy)
            elif ext in [".docx", ".doc"]:
                converted = _docx_to_pdf(doc_path, tmp_dir)
                if converted:
                    pdf_files.append(converted)
                else:
                    result["errors"].append(
                        f"Could not convert {doc_path.name} to PDF — LibreOffice needed"
                    )

        if not pdf_files:
            result["errors"].append("No PDFs could be prepared for merging")
            return result

        # 4. Optionally prepend cover page
        if include_cover:
            cover_path = tmp_dir / "00_cover_page.pdf"
            cover = _create_cover_page_pdf(tender_data, cover_path)
            if cover:
                pdf_files.insert(0, cover)

        # 5. Merge all PDFs
        safe_id = re.sub(r'[^\w\-]', '_', t247_id)[:40]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        out_filename = f"BidPackage_{safe_id}_{timestamp}.pdf"
        out_path = output_dir / out_filename

        success = _merge_pdfs(pdf_files, out_path)

        if success and out_path.exists():
            # Count pages
            page_count = 0
            try:
                try:
                    from pypdf import PdfReader
                except ImportError:
                    from PyPDF2 import PdfReader
                reader = PdfReader(str(out_path))
                page_count = len(reader.pages)
            except Exception:
                pass

            result.update({
                "status":      "success",
                "output_path": str(out_path),
                "filename":    out_filename,
                "page_count":  page_count,
                "file_count":  len(pdf_files),
                "size_kb":     round(out_path.stat().st_size / 1024, 1),
            })
        else:
            result["errors"].append("PDF merge failed — check LibreOffice installation")

    except Exception as e:
        result["errors"].append(f"Merge error: {str(e)}")
        logger.error(f"Merge failed: {e}", exc_info=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


def get_doc_order_preview(source_dirs: List[Path]) -> list:
    """Preview what order documents will be merged in."""
    all_files = []
    for src_dir in source_dirs:
        if not src_dir.exists():
            continue
        for ext in ["*.pdf", "*.docx", "*.doc"]:
            all_files.extend(src_dir.glob(ext))

    seen = set()
    unique = []
    for f in all_files:
        if f.name not in seen:
            seen.add(f.name)
            unique.append(f)

    sorted_files = _sort_documents(unique)
    return [
        {"filename": f.name, "priority": _score_doc(f.name), "type": f.suffix}
        for f in sorted_files
    ]
