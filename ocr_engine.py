"""
OCR Engine — extracts text from scanned PDFs using pytesseract.
Called automatically by extractor.py when normal PDF text extraction
returns empty or near-empty text (< 100 chars per page).

Render.com free plan: pytesseract + poppler-utils are installable.
Add to requirements.txt: pytesseract, Pillow
System dep in render.yaml: tesseract-ocr, poppler-utils
"""

import subprocess
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TESSERACT_OK = None   # None = untested, True = available, False = not available
_POPPLER_OK = None


def _check_tesseract() -> bool:
    global _TESSERACT_OK
    if _TESSERACT_OK is not None:
        return _TESSERACT_OK
    try:
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True, timeout=5
        )
        _TESSERACT_OK = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _TESSERACT_OK = False
    if not _TESSERACT_OK:
        logger.warning("Tesseract not found — OCR unavailable. Install: apt-get install tesseract-ocr")
    return _TESSERACT_OK


def _check_poppler() -> bool:
    global _POPPLER_OK
    if _POPPLER_OK is not None:
        return _POPPLER_OK
    try:
        subprocess.run(
            ["pdftoppm", "-h"],
            capture_output=True, timeout=5
        )
        _POPPLER_OK = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _POPPLER_OK = False
    if not _POPPLER_OK:
        logger.warning("pdftoppm not found — OCR unavailable. Install: apt-get install poppler-utils")
    return _POPPLER_OK


def is_available() -> bool:
    """Return True if OCR pipeline (tesseract + poppler) is functional."""
    return _check_tesseract() and _check_poppler()


def ocr_pdf(pdf_path: Path, max_pages: int = 40, dpi: int = 150) -> str:
    """
    Extract text from a scanned PDF using OCR.
    Rasterizes pages with pdftoppm then runs tesseract on each page image.
    Returns concatenated text from all pages.
    """
    if not is_available():
        logger.warning(f"OCR skipped for {pdf_path.name} — tools not available")
        return ""

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning("pytesseract or Pillow not installed — pip install pytesseract Pillow")
        return ""

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return ""

    logger.info(f"[OCR] Starting OCR on {pdf_path.name} (max {max_pages} pages, {dpi} DPI)")

    all_text = []

    with tempfile.TemporaryDirectory(prefix="ocr_") as tmpdir:
        tmp = Path(tmpdir)
        prefix = str(tmp / "page")

        # Rasterize PDF pages to JPEG
        try:
            cmd = [
                "pdftoppm",
                "-jpeg",
                "-r", str(dpi),
                "-l", str(max_pages),
                str(pdf_path),
                prefix
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                logger.error(f"[OCR] pdftoppm failed: {result.stderr.decode()[:200]}")
                return ""
        except subprocess.TimeoutExpired:
            logger.error("[OCR] pdftoppm timed out")
            return ""
        except Exception as e:
            logger.error(f"[OCR] pdftoppm error: {e}")
            return ""

        # Get list of generated images, sorted by page number
        images = sorted(tmp.glob("*.jpg")) + sorted(tmp.glob("*.jpeg"))
        logger.info(f"[OCR] Rasterized {len(images)} pages")

        if not images:
            return ""

        # OCR each page
        for i, img_path in enumerate(images[:max_pages]):
            try:
                img = Image.open(img_path)
                # Tesseract config: page segmentation mode 3 (auto), English
                config = "--psm 3 -l eng"
                text = pytesseract.image_to_string(img, config=config)
                if text.strip():
                    all_text.append(f"\n--- Page {i+1} ---\n{text}")
                img.close()
            except Exception as e:
                logger.warning(f"[OCR] Page {i+1} failed: {e}")
                continue

    combined = "\n".join(all_text)
    logger.info(f"[OCR] Extracted {len(combined)} chars from {pdf_path.name}")
    return combined


def ocr_pdf_page_range(pdf_path: Path, start_page: int, end_page: int, dpi: int = 150) -> str:
    """OCR a specific page range — useful for PQ/TQ sections."""
    if not is_available():
        return ""

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""

    pdf_path = Path(pdf_path)
    all_text = []

    with tempfile.TemporaryDirectory(prefix="ocr_range_") as tmpdir:
        tmp = Path(tmpdir)
        prefix = str(tmp / "page")
        try:
            cmd = [
                "pdftoppm", "-jpeg", "-r", str(dpi),
                "-f", str(start_page), "-l", str(end_page),
                str(pdf_path), prefix
            ]
            subprocess.run(cmd, capture_output=True, timeout=60, check=True)
        except Exception as e:
            logger.error(f"[OCR] Range rasterize failed: {e}")
            return ""

        images = sorted(tmp.glob("*.jpg")) + sorted(tmp.glob("*.jpeg"))
        for i, img_path in enumerate(images):
            try:
                img = Image.open(img_path)
                text = pytesseract.image_to_string(img, config="--psm 3 -l eng")
                if text.strip():
                    all_text.append(f"\n--- Page {start_page + i} ---\n{text}")
                img.close()
            except Exception as e:
                logger.warning(f"[OCR] Page {start_page+i} OCR failed: {e}")

    return "\n".join(all_text)


def needs_ocr(text: str, page_count: int = 1) -> bool:
    """
    Determine if a PDF needs OCR.
    A scanned PDF typically returns < 100 chars per page from normal extraction.
    """
    if not text or not text.strip():
        return True
    chars_per_page = len(text.strip()) / max(page_count, 1)
    return chars_per_page < 100


def get_pdf_page_count(pdf_path: Path) -> int:
    """Get number of pages in a PDF using pdfinfo."""
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":")[1].strip())
    except Exception:
        pass
    return 1
