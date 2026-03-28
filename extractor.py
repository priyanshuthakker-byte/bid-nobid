"""
extractor.py — Tender Document Text Extractor
Reads PDF, DOCX, DOC, TXT, HTML files and returns plain text.
THIS FILE WAS MISSING — which caused blank Word docs and empty AI analysis.
"""
import re
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def read_document(file_path) -> str:
    """
    Read a single document file and return plain text.
    Supports: PDF, DOCX, DOC, TXT, HTML, HTM
    Returns empty string on failure (never raises).
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    text = ""

    try:
        if suffix == ".pdf":
            text = _read_pdf(path)
        elif suffix in (".docx", ".doc"):
            text = _read_docx(path)
        elif suffix in (".txt",):
            text = path.read_text(encoding="utf-8", errors="ignore")
        elif suffix in (".html", ".htm"):
            text = _read_html(path)
        else:
            # Try as plain text
            text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"Could not read {path.name}: {e}")
        text = ""

    return _clean_text(text)


def _read_pdf(path: Path) -> str:
    """Extract text from PDF using PyMuPDF (fitz) or pdfminer fallback."""
    text = ""

    # Try PyMuPDF first (best quality)
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        parts = []
        for page in doc:
            parts.append(page.get_text("text"))
        doc.close()
        text = "\n".join(parts)
        if len(text.strip()) > 100:
            logger.info(f"PyMuPDF extracted {len(text)} chars from {path.name}")
            return text
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"PyMuPDF failed on {path.name}: {e}")

    # Try pdfminer
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        text = pdfminer_extract(str(path))
        if len(text.strip()) > 100:
            logger.info(f"pdfminer extracted {len(text)} chars from {path.name}")
            return text
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfminer failed on {path.name}: {e}")

    # Try pypdf as last resort
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts)
        logger.info(f"pypdf extracted {len(text)} chars from {path.name}")
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pypdf failed on {path.name}: {e}")

    return text

    # OCR fallback for scanned PDFs (text < 200 chars means it's a scan)
    if len(text.strip()) < 200:
        logger.info("Text extraction yielded <200 chars — trying Gemini Vision OCR on " + path.name)
        ocr_text = _ocr_with_gemini(path)
        if ocr_text:
            text = ocr_text

    return text


def _ocr_with_gemini(path) -> str:
    """OCR fallback: converts PDF pages to images, sends to Gemini Vision."""
    import base64, json, urllib.request
    try:
        from ai_analyzer import get_all_api_keys
        api_keys = get_all_api_keys()
        if not api_keys:
            return ""
        api_key = api_keys[0]

        import fitz
        doc = fitz.open(str(path))
        all_text = []

        for page_num in range(min(len(doc), 8)):
            fitz_page = doc[page_num]
            pix = fitz_page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_b64 = base64.b64encode(pix.tobytes("png")).decode()

            payload = {
                "contents": [{"parts": [
                    {"text": "Extract ALL text from this government tender document page. Output only the raw text, preserve tables and numbers exactly."},
                    {"inline_data": {"mime_type": "image/png", "data": img_b64}}
                ]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 4096}
            }
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=" + api_key
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read())
                    page_text = result["candidates"][0]["content"]["parts"][0]["text"]
                    all_text.append("--- Page " + str(page_num+1) + " ---\n" + page_text)
            except Exception as e:
                logger.warning("Gemini OCR page " + str(page_num+1) + " failed: " + str(e))

        doc.close()
        combined = "\n\n".join(all_text)
        if combined.strip():
            logger.info("Gemini Vision OCR extracted " + str(len(combined)) + " chars from " + path.name)
        return combined
    except Exception as e:
        logger.warning("OCR fallback failed: " + str(e))
        return ""


def _read_docx(path: Path) -> str:
    """Extract text from DOCX file."""
    try:
        from docx import Document
        doc = Document(str(path))
        parts = []

        # Paragraphs
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)

        # Tables
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(
                    cell.text.strip() for cell in row.cells if cell.text.strip()
                )
                if row_text:
                    parts.append(row_text)

        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"DOCX read failed on {path.name}: {e}")
        return ""


def _read_html(path: Path) -> str:
    """Strip HTML tags and return plain text."""
    try:
        html = path.read_text(encoding="utf-8", errors="ignore")
        # Remove script and style blocks
        html = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', html,
                      flags=re.DOTALL | re.IGNORECASE)
        # Remove all tags
        text = re.sub(r'<[^>]+>', ' ', html)
        # Decode common HTML entities
        for entity, char in [('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'),
                               ('&gt;', '>'), ('&quot;', '"'), ('&#39;', "'"),
                               ('&rsquo;', "'"), ('&ldquo;', '"'), ('&rdquo;', '"')]:
            text = text.replace(entity, char)
        return text
    except Exception as e:
        logger.warning(f"HTML read failed on {path.name}: {e}")
        return ""


def _clean_text(text: str) -> str:
    """Normalize whitespace, remove junk characters."""
    if not text:
        return ""
    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Remove null bytes and non-printable chars (keep newlines, tabs)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Collapse 3+ consecutive newlines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse multiple spaces
    text = re.sub(r'[ \t]{3,}', '  ', text)
    return text.strip()


class TenderExtractor:
    """
    Extracts structured fields from tender document text.
    Regex-based extraction — AI analysis refines these later.
    """

    def process_documents(self, file_paths: List) -> Dict[str, Any]:
        """
        Process a list of document files.
        Returns dict with all extracted fields.
        """
        if not file_paths:
            return self._empty_result()

        all_text = ""
        for fp in file_paths:
            text = read_document(fp)
            if text:
                all_text += f"\n\n=== {Path(fp).name} ===\n{text}"

        if not all_text.strip():
            logger.warning("No text extracted from any document")
            return self._empty_result()

        logger.info(f"Total extracted text: {len(all_text)} chars from {len(file_paths)} files")
        return self._extract_fields(all_text)

    def _extract_fields(self, text: str) -> Dict[str, Any]:
        """Extract structured fields using regex patterns."""
        t = text  # alias

        result = {
            # Basic info
            "tender_no": self._extract_tender_no(t),
            "org_name": self._extract_org_name(t),
            "tender_name": self._extract_tender_name(t),
            "portal": self._extract_portal(t),

            # Dates
            "bid_start_date": self._extract_date(t, [
                r"(?:start|publish|sale|available).*?date[:\s]+([^\n]{5,40})",
                r"document.*?download.*?from[:\s]+([^\n]{5,40})",
            ]),
            "bid_submission_date": self._extract_date(t, [
                r"(?:last|final|bid\s+submission|closing)\s+date[:\s]+([^\n]{5,50})",
                r"submission.*?(?:deadline|date)[:\s]+([^\n]{5,50})",
                r"submit.*?bid.*?by[:\s]+([^\n]{5,50})",
                r"bid\s+end\s+date[:\s]+([^\n]{5,50})",
            ]),
            "bid_opening_date": self._extract_date(t, [
                r"bid\s+opening\s+date[:\s]+([^\n]{5,50})",
                r"opening\s+of\s+bids?[:\s]+([^\n]{5,50})",
                r"technical\s+bid\s+open[:\s]+([^\n]{5,50})",
            ]),
            "prebid_meeting": self._extract_date(t, [
                r"pre[\s\-]?bid\s+(?:meeting|conference)[:\s]+([^\n]{5,60})",
                r"pre[\s\-]?bid\s+date[:\s]+([^\n]{5,40})",
            ]),
            "prebid_query_date": self._extract_date(t, [
                r"(?:last\s+date.*?)?(?:pre[\s\-]?bid\s+)?quer(?:y|ies).*?(?:by|before|on|date)[:\s]+([^\n]{5,60})",
                r"clarification.*?(?:by|on)[:\s]+([^\n]{5,60})",
            ]),

            # Financial
            "estimated_cost": self._extract_amount(t, [
                r"estimated\s+(?:cost|value|amount)[:\s]+(rs\.?\s*[\d,\.]+\s*(?:crore|cr|lakh|l)?)",
                r"(?:project|contract)\s+value[:\s]+(rs\.?\s*[\d,\.]+\s*(?:crore|cr|lakh|l)?)",
                r"estimated\s+project\s+cost[:\s]+(rs\.?\s*[\d,\.]+\s*(?:crore|cr|lakh|l)?)",
            ]),
            "tender_fee": self._extract_amount(t, [
                r"tender\s+(?:fee|document|processing)[:\s]+(rs\.?\s*[\d,\.]+[^\n]{0,30})",
                r"document\s+(?:fee|cost)[:\s]+(rs\.?\s*[\d,\.]+[^\n]{0,30})",
            ]),
            "emd": self._extract_amount(t, [
                r"emd[:\s]+(rs\.?\s*[\d,\.]+[^\n]{0,40})",
                r"earnest\s+money[:\s]+(rs\.?\s*[\d,\.]+[^\n]{0,40})",
                r"bid\s+security[:\s]+(rs\.?\s*[\d,\.]+[^\n]{0,40})",
            ]),
            "emd_exemption": self._search(t, [
                r"(?:msme|small\s+enterprise)[^\n]{0,100}(?:exempt|waiv)[^\n]{0,100}",
                r"emd\s+exemption[:\s]+([^\n]{10,150})",
            ]),
            "performance_security": self._search(t, [
                r"performance\s+(?:bank\s+)?guarantee[:\s]+([^\n]{5,80})",
                r"performance\s+security\s+(?:deposit)?[:\s]+([^\n]{5,80})",
            ]),

            # Contract details
            "contract_period": self._search(t, [
                r"(?:period|duration)\s+(?:of\s+)?(?:contract|work|project)[:\s]+([^\n]{5,80})",
                r"contract\s+period[:\s]+([^\n]{5,80})",
                r"(?:implementation|completion)\s+period[:\s]+([^\n]{5,80})",
            ]),
            "bid_validity": self._search(t, [
                r"bid\s+validity[:\s]+([^\n]{5,50})",
                r"offer\s+validity[:\s]+([^\n]{5,50})",
                r"validity\s+(?:period\s+)?(?:of\s+bid)?[:\s]+(\d+\s+days?)",
            ]),
            "jv_allowed": self._search(t, [
                r"(?:joint\s+venture|jv|consortium)[^\n]{0,200}(?:permitted|allowed|acceptable|not\s+permitted)[^\n]{0,100}",
                r"(?:sub.?contracting|sub.?contract)[:\s]+([^\n]{5,100})",
            ]),
            "mode_of_selection": self._search(t, [
                r"(?:mode|method|basis)\s+of\s+(?:evaluation|selection)[:\s]+([^\n]{5,80})",
                r"(?:qcbs|l1|quality\s+cum\s+cost|lowest\s+bid)[^\n]{0,50}",
            ]),

            # Contact & location
            "contact": self._search(t, [
                r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}[^\n]{0,80})",
                r"(?:contact|email)[:\s]+([^\n]{5,100})",
            ]),
            "location": self._search(t, [
                r"(?:project\s+)?(?:location|place)[:\s]+([^\n]{5,80})",
                r"(?:work\s+)?(?:site|district|city)[:\s]+([^\n]{5,60})",
            ]),
            "post_implementation": self._search(t, [
                r"(?:post.?implementation|amc|annual\s+maintenance)[:\s]+([^\n]{5,100})",
                r"(?:warranty|defect\s+liability)[:\s]+([^\n]{5,80})",
            ]),

            # PQ/TQ (basic extraction — AI refines these)
            "pq_criteria": self._extract_pq_criteria(t),
            "tq_criteria": [],

            # Additional
            "scope_items": self._extract_scope(t),
            "payment_terms": self._extract_payment_terms(t),
            "tender_type": self._search(t, [
                r"type\s+of\s+(?:contract|tender)[:\s]+([^\n]{5,60})",
                r"(?:rate|lump.?sum|turnkey|item.?rate)\s+contract",
            ]),
        }

        return result

    def _extract_tender_no(self, text: str) -> str:
        patterns = [
            r"(?:tender|rfp|nit|ref|e-tender)\s*(?:no\.?|number)[:\s]+([A-Z0-9][^\n]{3,60})",
            r"(?:tender|rfp|nit)\s*[:/#]\s*([A-Z0-9][^\n]{3,60})",
            r"(?:no\.|number)[:\s]+([A-Z]{2,}[/\-][^\n]{3,40})",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                val = m.group(1).strip().split('\n')[0][:60]
                if len(val) > 3:
                    return val
        return "—"

    def _extract_org_name(self, text: str) -> str:
        patterns = [
            r"(?:issued\s+by|organization|organisation|department|authority)[:\s]+([^\n]{5,80})",
            r"(?:^|\n)(?:to|from)[:\s]+([A-Z][^\n]{10,80})",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()[:80]
                if len(val) > 5:
                    return val
        return "—"

    def _extract_tender_name(self, text: str) -> str:
        patterns = [
            r"(?:subject|tender\s+for|name\s+of\s+(?:work|project))[:\s]+([^\n]{10,150})",
            r"(?:scope\s+of\s+work|project\s+title)[:\s]+([^\n]{10,100})",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()[:150]
                if len(val) > 10:
                    return val
        return "—"

    def _extract_portal(self, text: str) -> str:
        patterns = [
            r"https?://[a-zA-Z0-9\-\.]+(?:\.gov\.in|\.nic\.in|\.org|tender\.[a-z]+)[^\s\"'<]{0,50}",
            r"www\.[a-zA-Z0-9\-\.]+(?:tender|eprocure|etender|eproc)[^\s\"'<]{0,50}",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(0)
        return "—"

    def _extract_date(self, text: str, patterns: list) -> str:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
            if m:
                val = m.group(1).strip()[:60] if m.lastindex else m.group(0).strip()[:60]
                val = val.split('\n')[0].strip()
                if len(val) > 4:
                    return val
        return "—"

    def _extract_amount(self, text: str, patterns: list) -> str:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()[:80] if m.lastindex else m.group(0).strip()[:80]
                if val:
                    # Ensure Rs. prefix
                    if not val.lower().startswith('rs'):
                        val = 'Rs. ' + val
                    return val
        return "—"

    def _search(self, text: str, patterns: list) -> str:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
            if m:
                val = m.group(1).strip()[:200] if m.lastindex else m.group(0).strip()[:200]
                val = val.split('\n')[0].strip()
                if len(val) > 3:
                    return val
        return "—"

    def _extract_pq_criteria(self, text: str) -> list:
        """Extract PQ criteria table rows — basic extraction, AI refines."""
        criteria = []

        # Find PQ section
        pq_section = ""
        for marker in ["Pre-Qualification", "Eligibility Criteria",
                        "Qualifying Criteria", "Technical Eligibility",
                        "Minimum Qualifying Requirements"]:
            idx = text.lower().find(marker.lower())
            if idx != -1:
                pq_section = text[idx:idx+5000]
                break

        if not pq_section:
            return []

        # Extract numbered items from PQ section
        pattern = r"(?:^|\n)\s*(\d+\.?\s+|[a-z]\)\s+|•\s+|\(\d+\)\s*)([^\n]{20,300})"
        matches = re.findall(pattern, pq_section, re.MULTILINE)

        for i, (num, criteria_text) in enumerate(matches[:20]):
            criteria_text = criteria_text.strip()
            if len(criteria_text) < 20:
                continue
            # Skip obvious non-criteria lines
            if any(skip in criteria_text.lower() for skip in
                   ['page', 'table of', 'section', 'signature', 'stamp']):
                continue
            criteria.append({
                "sl_no": str(i + 1),
                "clause_ref": "Refer RFP",
                "criteria": criteria_text,
                "details": "",
                "nascent_status": "Review",
                "nascent_color": "BLUE",
                "nascent_remark": "Requires AI analysis or manual review.",
            })

        return criteria

    def _extract_scope(self, text: str) -> list:
        """Extract scope of work items."""
        scope_section = ""
        for marker in ["Scope of Work", "Scope of Services", "Deliverables",
                        "Work to be Done", "Project Scope"]:
            idx = text.lower().find(marker.lower())
            if idx != -1:
                scope_section = text[idx:idx+4000]
                break

        if not scope_section:
            return []

        items = []
        pattern = r"(?:^|\n)\s*(?:\d+\.|\w\)|\*|•|-)\s+([^\n]{20,300})"
        matches = re.findall(pattern, scope_section, re.MULTILINE)
        for item in matches[:20]:
            item = item.strip()
            if len(item) > 20:
                items.append(item)

        return items[:15]

    def _extract_payment_terms(self, text: str) -> list:
        """Extract payment schedule."""
        pay_section = ""
        for marker in ["Payment Terms", "Payment Schedule", "Payment Milestones"]:
            idx = text.lower().find(marker.lower())
            if idx != -1:
                pay_section = text[idx:idx+2000]
                break

        if not pay_section:
            return []

        items = []
        pattern = r"(?:^|\n)\s*(?:\d+\.|\w\)|\*|•|-)\s+([^\n]{20,300})"
        matches = re.findall(pattern, pay_section, re.MULTILINE)
        for item in matches[:10]:
            item = item.strip()
            if len(item) > 20:
                items.append(item)

        return items[:8]

    def _empty_result(self) -> Dict[str, Any]:
        """Return empty structure when no documents could be read."""
        return {
            "tender_no": "—", "org_name": "—", "tender_name": "—",
            "portal": "—", "bid_start_date": "—", "bid_submission_date": "—",
            "bid_opening_date": "—", "prebid_meeting": "—", "prebid_query_date": "—",
            "estimated_cost": "—", "tender_fee": "—", "emd": "—",
            "emd_exemption": "—", "performance_security": "—",
            "contract_period": "—", "bid_validity": "—",
            "jv_allowed": "—", "mode_of_selection": "—",
            "contact": "—", "location": "—", "post_implementation": "—",
            "tender_type": "—",
            "pq_criteria": [], "tq_criteria": [], "scope_items": [],
            "payment_terms": [],
        }
