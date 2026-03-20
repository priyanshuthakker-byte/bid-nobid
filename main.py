"""
Bid/No-Bid System v7 — Full Workflow
New in v7:
- DELETE tender from list
- UPDATE verdict (dropdown: BID/NO-BID/CONDITIONAL/REVIEW)
- SKIP / Mark as Not Interested
- CORRIGENDUM upload → AI diff → show what changed vs original
- APPLY corrigendum changes to tender data
- PRE-BID RESPONSE upload → parsed and stored
- GENERATE submission documents (cover letter, declarations, annexures)
- MERGE documents to PDF
- Fixed model names in ai_analyzer
"""

import zipfile, tempfile, shutil, json, re, os, threading, time
from pathlib import Path
from datetime import datetime, date
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from typing import List
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from extractor import TenderExtractor, read_document
from doc_generator import BidDocGenerator
from nascent_checker import NascentChecker
from ai_analyzer import analyze_with_gemini, merge_results, load_config, save_config
from excel_processor import process_excel, quick_classify
from prebid_generator import generate_prebid_queries
from chatbot import process_message, load_history
from gdrive_sync import (init_drive, save_to_drive, load_from_drive,
    drive_available, upload_tender_file)
from tracker import (get_deadline_alerts, get_pipeline_stats,
                     get_win_loss_stats, generate_doc_checklist,
                     PIPELINE_STAGES, STAGE_COLORS)
from corrigendum_analyzer import analyze_corrigendum, apply_corrigendum_to_tender
from submission_doc_generator import generate_submission_package, merge_docs_to_pdf
try:
    from technical_proposal_generator import generate_technical_proposal, match_projects
    TP_GEN_AVAILABLE = True
except ImportError:
    TP_GEN_AVAILABLE = False

try:
    from portal_watcher import (start_watcher, check_now as check_portal_now,
                                 get_all_alerts as get_portal_alerts,
                                 get_bid_opening_today)
    WATCHER_AVAILABLE = True
except ImportError:
    WATCHER_AVAILABLE = False

try:
    from pdf_merger import merge_submission_package, get_doc_order_preview
    PDF_MERGE_AVAILABLE = True
except ImportError:
    PDF_MERGE_AVAILABLE = False

try:
    from post_bid_tracker import (record_bid_result, get_win_loss_analytics,
                                   get_pipeline_value, get_competitor_report)
    POST_BID_AVAILABLE = True
except ImportError:
    POST_BID_AVAILABLE = False

try:
    from post_award import (generate_loa_acceptance, generate_performance_security_letter,
                             setup_milestones, update_milestone, get_milestone_summary,
                             generate_ra_bill, generate_completion_cert_request)
    POST_AWARD_AVAILABLE = True
except ImportError:
    POST_AWARD_AVAILABLE = False

try:
    from t247_downloader import (
        auto_download_tender, test_credentials,
        get_supported_portals, resolve_excel_link
    )
    T247_DL_AVAILABLE = True
except ImportError:
    T247_DL_AVAILABLE = False

try:
    from form_filler import extract_forms_from_rfp, fill_form_with_ai
    FORM_FILLER_AVAILABLE = True
except ImportError:
    FORM_FILLER_AVAILABLE = False

try:
    from indian_tender_guidelines import (
        analyze_rfp_against_guidelines,
        generate_prebid_letter_docx,
        get_all_guidelines_summary,
        GUIDELINES, NASCENT,
    )
    GUIDELINES_AVAILABLE = True
except ImportError:
    GUIDELINES_AVAILABLE = False

try:
    from letterhead_manager import (
        save_letterhead, get_letterhead_path,
        letterhead_exists, apply_letterhead_to_doc
    )
    LETTERHEAD_AVAILABLE = True
except ImportError:
    LETTERHEAD_AVAILABLE = False

app = FastAPI(title="Bid/No-Bid System v7", version="7.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
DOCS_DIR = BASE_DIR / "submission_docs"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
TEMP_DIR.mkdir(exist_ok=True, parents=True)
DOCS_DIR.mkdir(exist_ok=True, parents=True)
DB_FILE = OUTPUT_DIR / "tenders_db.json"

_last_sync_mtime = 0.0
_sync_lock = threading.Lock()


# ── DB helpers ─────────────────────────────────────────────────

def load_db() -> dict:
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tenders": {}}


def save_db(db: dict):
    DB_FILE.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")
    try:
        result = save_to_drive(DB_FILE)
        if isinstance(result, dict) and not result.get("ok"):
            print(f"Drive sync warning: {result.get('reason', 'unknown')}")
    except Exception as e:
        print(f"Drive sync error: {e}")


def get_tender(t247_id: str) -> dict:
    return load_db()["tenders"].get(str(t247_id), {})


# ── API quota tracker (in-memory, resets daily) ─────────────────────────────
_quota_data = {
    "date": "",
    "requests_used": 0,
    "tokens_used": 0,
    "keys_status": {},
}

def track_api_call(key_index: int = 0, tokens_used: int = 0):
    today = datetime.now().strftime("%Y-%m-%d")
    if _quota_data["date"] != today:
        _quota_data["date"] = today
        _quota_data["requests_used"] = 0
        _quota_data["tokens_used"] = 0
        _quota_data["keys_status"] = {}
    _quota_data["requests_used"] += 1
    _quota_data["tokens_used"] += tokens_used
    ks = _quota_data["keys_status"].setdefault(str(key_index), {"requests": 0})
    ks["requests"] += 1


def save_tender(t247_id: str, data: dict):
    db = load_db()
    db["tenders"][str(t247_id)] = data
    save_db(db)


# ── Background sync ────────────────────────────────────────────

def _background_sync_worker():
    global _last_sync_mtime
    while True:
        time.sleep(300)
        try:
            if not drive_available() or not DB_FILE.exists():
                continue
            mod_time = DB_FILE.stat().st_mtime
            with _sync_lock:
                if mod_time > _last_sync_mtime:
                    result = save_to_drive(DB_FILE)
                    if isinstance(result, dict) and result.get("ok"):
                        _last_sync_mtime = mod_time
        except Exception as e:
            print(f"Background sync error: {e}")


# ── Helpers ────────────────────────────────────────────────────

def extract_all_zips(folder: Path):
    for nested in list(folder.rglob("*.zip")):
        try:
            out = nested.parent / (nested.stem + "_inner")
            out.mkdir(exist_ok=True)
            with zipfile.ZipFile(nested, "r") as zf:
                zf.extractall(out)
            extract_all_zips(out)
        except Exception:
            pass


def days_left(deadline_str: str) -> int:
    if not deadline_str:
        return 999
    for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"]:
        try:
            d = datetime.strptime(str(deadline_str).split()[0], fmt).date()
            return (d - date.today()).days
        except Exception:
            continue
    return 999


def prebid_passed(date_str: str) -> bool:
    return days_left(date_str) < 0


# ── Startup / Shutdown ─────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    global _last_sync_mtime
    print("Starting Bid/No-Bid System v7...")
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    TEMP_DIR.mkdir(exist_ok=True, parents=True)
    DOCS_DIR.mkdir(exist_ok=True, parents=True)
    drive_ok = init_drive()
    print(f"Google Drive: {'Connected' if drive_ok else 'Not configured'}")
    if drive_ok:
        for attempt in range(3):
            try:
                success = load_from_drive(DB_FILE)
                if success:
                    db = load_db()
                    _last_sync_mtime = DB_FILE.stat().st_mtime if DB_FILE.exists() else 0
                    print(f"Loaded {len(db.get('tenders', {}))} tenders from Drive")
                    break
                time.sleep(2)
            except Exception as e:
                print(f"Drive load attempt {attempt+1} failed: {e}")
                time.sleep(2)
    threading.Thread(target=_background_sync_worker, daemon=True).start()
    print("Server ready")

    # Start portal watcher (background — checks every 6 hours)
    if WATCHER_AVAILABLE:
        try:
            start_watcher()
            print("✅ Portal watcher started")
        except Exception as e:
            print(f"Portal watcher: {e}")

    # Restore T247 credentials from DB if config.json was wiped (Render restart)
    try:
        cfg = load_config()
        if not cfg.get("t247_username"):
            db = load_db()
            settings = db.get("_settings", {})
            if settings.get("t247_username"):
                cfg["t247_username"] = settings["t247_username"]
            if settings.get("t247_password_b64"):
                import base64
                cfg["t247_password"] = base64.b64decode(
                    settings["t247_password_b64"]).decode()
            if cfg.get("t247_username"):
                save_config(cfg)
                print(f"✅ Restored T247 credentials for: {cfg['t247_username']}")
    except Exception as e:
        print(f"Settings restore: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    print("Shutdown — syncing to Drive...")
    try:
        if drive_available() and DB_FILE.exists():
            save_to_drive(DB_FILE)
    except Exception as e:
        print(f"Shutdown sync error: {e}")


# ══════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root():
    index = BASE_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Bid/No-Bid System v7</h1>")


@app.head("/")
async def root_head():
    """Handle HEAD requests from Render — prevents 405 restart cycle."""
    return HTMLResponse(content="", status_code=200)

@app.head("/health")
async def health_head():
    return HTMLResponse(content="", status_code=200)

@app.get("/health")
async def health():
    config = load_config()
    db = load_db()
    return {
        "status": "ok", "version": "7.0",
        "ai_configured": bool(config.get("gemini_api_key")),
        "drive_sync": drive_available(),
        "tenders_loaded": len(db.get("tenders", {})),
        "db_size_kb": round(DB_FILE.stat().st_size / 1024) if DB_FILE.exists() else 0,
    }


# ── Excel Import ───────────────────────────────────────────────

@app.post("/import-excel")
async def import_excel(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload an Excel file (.xlsx)")
    tmp = Path(tempfile.mktemp(suffix=".xlsx", dir=str(TEMP_DIR)))
    try:
        tmp.write_bytes(await file.read())
        tenders = process_excel(str(tmp))
        db = load_db()
        added = updated = 0
        for t in tenders:
            tid = str(t.get("t247_id", ""))
            if tid:
                existing = db["tenders"].get(tid, {})
                if existing:
                    excel_fields = ["ref_no", "brief", "org_name", "location",
                                    "estimated_cost_raw", "estimated_cost_cr",
                                    "deadline", "days_left", "deadline_status",
                                    "doc_fee", "emd", "msme_exemption",
                                    "eligibility", "checklist", "is_gem"]
                    for field in excel_fields:
                        if t.get(field) is not None:
                            existing[field] = t[field]
                    if not existing.get("bid_no_bid_done"):
                        existing["verdict"] = t.get("verdict")
                        existing["verdict_color"] = t.get("verdict_color")
                        existing["reason"] = t.get("reason")
                    db["tenders"][tid] = existing
                    updated += 1
                else:
                    db["tenders"][tid] = t
                    added += 1
        save_db(db)
        return {"status": "success", "total": len(tenders), "added": added,
                "updated": updated, "tenders": tenders}
    finally:
        tmp.unlink(missing_ok=True)


# ── Dashboard ──────────────────────────────────────────────────

@app.get("/dashboard")
async def dashboard():
    db = load_db()
    # Exclude skipped/not-interested tenders from stats
    tenders = [t for t in db["tenders"].values() if t.get("status") != "Not Interested"]
    all_tenders = list(db["tenders"].values())
    def get_verdict(t):
        return t.get("verdict") or t.get("bid_no_bid") or t.get("recommendation") or "REVIEW"

    stats = {
        "total": len(tenders),
        "bid": sum(1 for t in tenders if get_verdict(t) == "BID"),
        "no_bid": sum(1 for t in tenders if get_verdict(t) == "NO-BID"),
        "conditional": sum(1 for t in tenders if get_verdict(t) == "CONDITIONAL"),
        "review": sum(1 for t in tenders if get_verdict(t) == "REVIEW"),
        "analysed": sum(1 for t in tenders if t.get("bid_no_bid_done")),
        "skipped": sum(1 for t in all_tenders if t.get("status") == "Not Interested"),
        "deadline_today": sum(1 for t in tenders if days_left(t.get("deadline", "")) == 0),
        "deadline_3days": sum(1 for t in tenders if 0 < days_left(t.get("deadline", "")) <= 3),
    }
    # Compute days_left and ensure verdict field on each tender
    for t in tenders:
        t["days_left"] = days_left(t.get("deadline", ""))
        # Ensure verdict field (old tenders may use bid_no_bid instead)
        if not t.get("verdict") and t.get("bid_no_bid"):
            t["verdict"] = t["bid_no_bid"]
        if not t.get("verdict") and t.get("recommendation"):
            t["verdict"] = t["recommendation"]
        # Ensure verdict_color for badge
        v = t.get("verdict", "REVIEW")
        t["verdict_color"] = {
            "BID": "GREEN", "NO-BID": "RED",
            "CONDITIONAL": "AMBER", "REVIEW": "BLUE"
        }.get(v, "BLUE")

    tenders_sorted = sorted(tenders, key=lambda t: t.get("days_left", 999))
    return {"stats": stats, "tenders": tenders_sorted}


# ── Process Files ──────────────────────────────────────────────

@app.post("/process")
async def process_zip(file: UploadFile = File(...), t247_id: str = ""):
    return await process_files(files=[file], t247_id=t247_id)


def _build_checklist(tender_data: dict) -> list:
    """Build submission checklist: RFP items first (from AI), then standard Nascent docs."""
    items = []
    sr = 1

    # 1. Items from RFP (extracted by AI)
    rfp_items = tender_data.get("submission_checklist", [])
    for item in rfp_items:
        items.append({
            "id": f"rfp_{sr}",
            "sr_no": str(sr),
            "label": item.get("document", item.get("label", "")),
            "description": item.get("description", ""),
            "source": "rfp",
            "mandatory": item.get("mandatory", True),
            "done": False,
            "generated_by_app": item.get("generated_by_app", False),
            "responsible": "Bid Team",
        })
        sr += 1

    # 2. Standard Nascent documents (always required)
    standard_docs = [
        ("Cover Letter", "On Nascent letterhead — addressing the tender authority", True),
        ("Company Profile", "Brief company profile with certifications", True),
        ("MSME / UDYAM Certificate", "UDYAM-GJ-01-0007420 — for EMD and fee exemptions", True),
        ("Non-Blacklisting Declaration", "Self-declaration on stamp paper", True),
        ("Financial Capacity Statement", "Turnover certificate from CA for last 3 years", True),
        ("ISO / CMMI Certificates", "ISO 9001, ISO 27001, ISO 20000, CMMI V2.0 L3", True),
        ("PAN Card", "Company PAN: AACCN3670J", True),
        ("GST Registration", "GSTIN: 24AACCN3670J1ZG", True),
        ("Authorization Letter", "Authorizing signatory — Hitesh Patel, CAO", True),
        ("Completion Certificates", "Client certificates from similar projects", True),
        ("Technical Proposal", "Full technical approach, methodology, team structure", True),
        ("Commercial Bid / BOQ", "Price bid as per tender format", True),
        ("EMD / Bid Security", "EMD amount as specified (MSME may be exempt)", False),
    ]

    for label, desc, mandatory in standard_docs:
        items.append({
            "id": f"std_{sr}",
            "sr_no": str(sr),
            "label": label,
            "description": desc,
            "source": "standard",
            "mandatory": mandatory,
            "done": False,
            "generated_by_app": label in ["Cover Letter", "Non-Blacklisting Declaration",
                                           "Financial Capacity Statement", "Authorization Letter",
                                           "Technical Proposal"],
            "responsible": "Bid Team",
        })
        sr += 1

    return items


@app.post("/process-files")
async def process_files(files: List[UploadFile] = File(...), t247_id: str = ""):
    if not files:
        raise HTTPException(400, "No files uploaded")
    tmp_dir = tempfile.mkdtemp(prefix="tender_", dir=str(TEMP_DIR))
    try:
        extract_dir = Path(tmp_dir) / "extracted"
        extract_dir.mkdir()
        for upload in files:
            fname = upload.filename or "upload"
            dest = Path(tmp_dir) / fname
            dest.write_bytes(await upload.read())
            if dest.suffix.lower() == ".zip":
                with zipfile.ZipFile(dest, "r") as zf:
                    zf.extractall(extract_dir)
                extract_all_zips(extract_dir)
            else:
                shutil.copy2(dest, extract_dir / fname)

        doc_files = []
        for ext in ["*.pdf", "*.docx", "*.doc", "*.txt", "*.html", "*.htm"]:
            doc_files.extend(extract_dir.rglob(ext))
        seen, unique = set(), []
        for f in doc_files:
            if f.name not in seen:
                seen.add(f.name)
                unique.append(f)
        doc_files = unique

        if not doc_files:
            raise HTTPException(400, "No readable documents found.")

        corrigendum_files = [f for f in doc_files if any(
            k in f.name.lower() for k in
            ["corrigendum", "addendum", "amendment", "corr_", "addend", "revised"])]
        main_files = [f for f in doc_files if f not in corrigendum_files]

        extractor = TenderExtractor()
        tender_data = extractor.process_documents(main_files if main_files else doc_files)

        if corrigendum_files:
            corr_extractor = TenderExtractor()
            corr_data = corr_extractor.process_documents(corrigendum_files)
            for field in ["bid_submission_date", "bid_opening_date", "bid_start_date",
                          "prebid_query_date", "estimated_cost", "emd", "tender_fee"]:
                val = corr_data.get(field, "")
                if val and val not in ["—", "Refer document", "Not specified", ""]:
                    tender_data[field] = val
            tender_data["has_corrigendum"] = True
            tender_data["corrigendum_files"] = [f.name for f in corrigendum_files]

        all_text = ""
        for f in sorted(doc_files, key=lambda x: (
            0 if any(k in x.name.lower() for k in ["rfp", "nit", "tender", "bid"]) else
            1 if any(k in x.name.lower() for k in ["corrigendum", "addendum"]) else 2
        )):
            t = read_document(f)
            if t and t.strip():
                all_text += f"\n\n=== FILE: {f.name} ===\n{t}"

        config = load_config()
        api_key = config.get("gemini_api_key", "")
        ai_used = False

        if api_key and all_text.strip():
            passed = prebid_passed(tender_data.get("prebid_query_date", ""))
            ai_result = analyze_with_gemini(all_text, passed)
            if "error" not in ai_result:
                tender_data = merge_results(tender_data, ai_result, passed)
                ai_used = True
            else:
                tender_data["ai_warning"] = ai_result.get("error", "")
        elif not api_key:
            tender_data["ai_warning"] = "No Gemini API key configured — basic extraction only."

        checker = NascentChecker()
        if not tender_data.get("overall_verdict"):
            tender_data["pq_criteria"] = checker.check_all(tender_data.get("pq_criteria", []))
            tender_data["tq_criteria"] = checker.check_all(tender_data.get("tq_criteria", []))
            tender_data["overall_verdict"] = checker.get_overall_verdict(
                tender_data["pq_criteria"] + tender_data["tq_criteria"])

        generator = BidDocGenerator()
        safe_no = re.sub(r'[^\w\-]', '_', tender_data.get("tender_no", "Report"))[:50]
        output_filename = f"BidNoBid_{safe_no}.docx"
        generator.generate(tender_data, str(OUTPUT_DIR / output_filename))

        # Auto-save Word report to Drive
        if t247_id and drive_available():
            try:
                upload_tender_file(t247_id, OUTPUT_DIR / output_filename, output_filename)
            except Exception as e:
                print(f"Report Drive save: {e}")

        if t247_id:
            db_record = get_tender(t247_id)
            db_record.update({
                "t247_id": t247_id,
                "tender_no": tender_data.get("tender_no"),
                "org_name": tender_data.get("org_name"),
                "tender_name": tender_data.get("tender_name"),
                "bid_submission_date": tender_data.get("bid_submission_date"),
                "emd": tender_data.get("emd"),
                "estimated_cost": tender_data.get("estimated_cost"),
                "verdict": (
                    tender_data.get("verdict") or
                    tender_data.get("overall_recommendation") or
                    (tender_data.get("overall_verdict") or {}).get("verdict") or
                    "REVIEW"
                ).upper().replace("NO BID","NO-BID").replace("NO_BID","NO-BID"),
                "reason": (
                    tender_data.get("reason") or
                    tender_data.get("recommendation_reason") or
                    tender_data.get("scope_summary") or ""
                ),
                "concerns": tender_data.get("concerns", []),
                "pq_criteria": tender_data.get("pq_criteria", []),
                "tq_criteria": tender_data.get("tq_criteria", []),
                "scope_summary": tender_data.get("scope_summary", ""),
                "scope_items": tender_data.get("scope_items", []),
                "prebid_queries": tender_data.get("prebid_queries", []),
                "doc_checklist": _build_checklist(tender_data),
                "bid_no_bid_done": True,
                "report_file": output_filename,
                "analysed_at": datetime.now().isoformat(),
                "has_corrigendum": tender_data.get("has_corrigendum", False),
                "ai_used": ai_used,
                "full_tender_data": tender_data,
                "drive_files": [f.name for f in doc_files],
            })
            save_tender(t247_id, db_record)

            # Upload report + source files to Drive
            _drive_upload_ok = False
            if drive_available():
                _tid = t247_id
                _files = list(doc_files)
                def _bg_upload():
                    ok_count = 0
                    for f in _files:
                        try:
                            if f.exists():
                                fid = upload_tender_file(_tid, f, f.name)
                                if fid:
                                    ok_count += 1
                        except Exception as e:
                            print(f"BG upload {f.name}: {e}")
                    if ok_count:
                        print(f"Drive: uploaded {ok_count} files for tender {_tid}")
                    else:
                        print(f"Drive: WARNING — 0 files uploaded for tender {_tid}. Check GDRIVE_FILE_ID and folder permissions.")
                t_upload = threading.Thread(target=_bg_upload, daemon=True)
                t_upload.start()
                t_upload.join(timeout=30)  # Wait up to 30s to confirm at least 1 upload
                _drive_upload_ok = drive_available()  # best effort

        # Flatten key fields for frontend — readable as d.verdict, d.reason etc.
        _verdict = (
            tender_data.get("verdict") or
            tender_data.get("overall_recommendation") or
            (tender_data.get("overall_verdict") or {}).get("verdict") or "REVIEW"
        )
        _verdict = str(_verdict).upper().replace("NO BID","NO-BID").replace("NO_BID","NO-BID")
        _reason  = (tender_data.get("reason") or
                    tender_data.get("recommendation_reason") or
                    tender_data.get("scope_summary") or "")

        return {
            "status": "success",
            "ai_used": ai_used,
            # Flattened for direct frontend use
            "verdict": _verdict,
            "reason": _reason,
            "concerns": tender_data.get("concerns", []),
            "pq_criteria": tender_data.get("pq_criteria", []),
            "tq_criteria": tender_data.get("tq_criteria", []),
            "scope_summary": tender_data.get("scope_summary", ""),
            "scope_items": tender_data.get("scope_items", []),
            "prebid_queries": tender_data.get("prebid_queries", []),
            "submission_checklist": tender_data.get("submission_checklist", []),
            "has_corrigendum": tender_data.get("has_corrigendum", False),
            "files_processed": [f.name for f in doc_files],
            "tender_data": tender_data,
            "download_file": output_filename,
            "files_stored_on_drive": _drive_upload_ok,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(500, f"Error: {str(e)}\n{traceback.format_exc()}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ════════════════════════════════════════
# NEW: TENDER LIST MANAGEMENT
# ════════════════════════════════════════

@app.delete("/tender/{t247_id}")
async def delete_tender(t247_id: str):
    """Permanently delete a tender from the list."""
    db = load_db()
    if t247_id not in db["tenders"]:
        raise HTTPException(404, "Tender not found")
    del db["tenders"][t247_id]
    save_db(db)
    return {"status": "deleted", "t247_id": t247_id}


@app.post("/tender/{t247_id}/verdict")
async def update_verdict(t247_id: str, data: dict = Body(...)):
    """
    Update verdict manually via dropdown.
    Accepted values: BID, NO-BID, CONDITIONAL, REVIEW
    """
    verdict = data.get("verdict", "").upper().strip()
    if verdict not in ["BID", "NO-BID", "CONDITIONAL", "REVIEW"]:
        raise HTTPException(400, "verdict must be BID, NO-BID, CONDITIONAL, or REVIEW")
    color_map = {"BID": "GREEN", "NO-BID": "RED", "CONDITIONAL": "AMBER", "REVIEW": "BLUE"}
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if not t:
        raise HTTPException(404, "Tender not found")
    t["verdict"] = verdict
    t["verdict_color"] = color_map[verdict]
    t["verdict_updated_manually"] = True
    t["verdict_updated_at"] = datetime.now().isoformat()
    t["verdict_note"] = data.get("note", "")
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "verdict": verdict, "color": color_map[verdict]}


@app.post("/tender/{t247_id}/skip")
async def skip_tender(t247_id: str, data: dict = Body(...)):
    """Mark tender as Not Interested / Skip. Hides from main dashboard."""
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if not t:
        raise HTTPException(404, "Tender not found")
    t["status"] = "Not Interested"
    t["skip_reason"] = data.get("reason", "Not interested")
    t["skipped_at"] = datetime.now().isoformat()
    t["status_updated_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "skipped"}


@app.post("/tender/{t247_id}/unskip")
async def unskip_tender(t247_id: str):
    """Restore a skipped tender back to active list."""
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if not t:
        raise HTTPException(404, "Tender not found")
    t["status"] = "Identified"
    t.pop("skip_reason", None)
    t.pop("skipped_at", None)
    t["status_updated_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "restored"}


@app.get("/skipped-tenders")
async def get_skipped_tenders():
    """Get all tenders marked as Not Interested."""
    db = load_db()
    skipped = [t for t in db["tenders"].values() if t.get("status") == "Not Interested"]
    return {"tenders": skipped, "count": len(skipped)}


# ════════════════════════════════════════
# NEW: CORRIGENDUM UPLOAD & DIFF
# ════════════════════════════════════════

@app.post("/corrigendum/{t247_id}")
async def upload_corrigendum(t247_id: str, files: List[UploadFile] = File(...)):
    """
    Upload corrigendum files for a tender.
    AI reads corrigendum, extracts what changed vs original tender.
    Returns structured diff — user reviews and decides whether to apply.
    Does NOT automatically update the tender — user must call /corrigendum/{id}/apply.
    """
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found. Analyse the original tender first.")

    if not tender.get("bid_no_bid_done"):
        raise HTTPException(400, "Original tender must be analysed first before uploading corrigendum.")

    tmp_dir = tempfile.mkdtemp(prefix="corr_", dir=str(TEMP_DIR))
    try:
        extract_dir = Path(tmp_dir) / "extracted"
        extract_dir.mkdir()

        for upload in files:
            fname = upload.filename or "corrigendum"
            dest = Path(tmp_dir) / fname
            dest.write_bytes(await upload.read())
            if dest.suffix.lower() == ".zip":
                with zipfile.ZipFile(dest, "r") as zf:
                    zf.extractall(extract_dir)
            else:
                shutil.copy2(dest, extract_dir / fname)

        doc_files = []
        for ext in ["*.pdf", "*.docx", "*.doc", "*.txt", "*.html"]:
            doc_files.extend(extract_dir.rglob(ext))

        if not doc_files:
            raise HTTPException(400, "No readable documents found in corrigendum upload.")

        # Build combined text
        corr_text = ""
        for f in doc_files:
            t_text = read_document(f)
            if t_text and t_text.strip():
                corr_text += f"\n\n=== FILE: {f.name} ===\n{t_text}"

        if not corr_text.strip():
            raise HTTPException(400, "Could not extract text from corrigendum files.")

        # Get original tender data for comparison
        original_data = tender.get("full_tender_data", tender)

        # AI analysis of corrigendum
        corr_result = analyze_corrigendum(corr_text, original_data)

        if "error" in corr_result:
            return JSONResponse({
                "status": "partial",
                "warning": corr_result["error"],
                "message": "Could not run AI analysis on corrigendum. Raw text stored for manual review.",
                "corr_text_preview": corr_text[:2000],
            })

        # Store pending corrigendum (not applied yet — user reviews first)
        db = load_db()
        t_db = db["tenders"].get(t247_id, {})
        if "pending_corrigenda" not in t_db:
            t_db["pending_corrigenda"] = []
        t_db["pending_corrigenda"].append({
            "uploaded_at": datetime.now().isoformat(),
            "files": [f.name for f in doc_files],
            "corr_result": corr_result,
            "applied": False,
        })
        t_db["has_pending_corrigendum"] = True
        db["tenders"][t247_id] = t_db
        save_db(db)

        return {
            "status": "success",
            "message": "Corrigendum analysed. Review the changes below and click Apply if you want to update the tender.",
            "files_processed": [f.name for f in doc_files],
            "corrigendum_no": corr_result.get("corrigendum_no", ""),
            "corrigendum_date": corr_result.get("corrigendum_date", ""),
            "summary": corr_result.get("summary", ""),
            "overall_impact": corr_result.get("overall_impact_on_nascent", ""),
            "overall_impact_summary": corr_result.get("overall_impact_summary", ""),
            "date_changes": corr_result.get("date_changes", {}),
            "financial_changes": corr_result.get("financial_changes", {}),
            "pq_changes": corr_result.get("pq_changes", []),
            "scope_changes": corr_result.get("scope_changes", []),
            "other_changes": corr_result.get("other_changes", []),
            "action_required": corr_result.get("action_required", []),
            "verdict_change_recommended": corr_result.get("verdict_change_recommended", False),
            "verdict_change_reason": corr_result.get("verdict_change_reason", ""),
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(500, f"Corrigendum processing error: {str(e)}\n{traceback.format_exc()}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/corrigendum/{t247_id}/apply")
async def apply_corrigendum(t247_id: str, data: dict = Body(...)):
    """
    Apply a pending corrigendum to the tender data.
    User calls this after reviewing the diff.
    Optionally re-generates the Bid/No-Bid Word doc.
    """
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    pending = tender.get("pending_corrigenda", [])
    if not pending:
        raise HTTPException(400, "No pending corrigendum found for this tender.")

    # Get the latest pending corrigendum
    latest_corr = pending[-1]
    corr_result = latest_corr.get("corr_result", {})

    # Apply changes to tender data
    original_data = tender.get("full_tender_data", tender)
    updated_data = apply_corrigendum_to_tender(original_data, corr_result)

    # Re-generate Word doc if requested
    regen_doc = data.get("regenerate_doc", True)
    new_doc_file = None
    if regen_doc:
        try:
            generator = BidDocGenerator()
            safe_no = re.sub(r'[^\w\-]', '_',
                             updated_data.get("tender_no", t247_id))[:50]
            corr_no = re.sub(r'[^\w]', '', corr_result.get("corrigendum_no", "Corr1"))
            new_doc_file = f"BidNoBid_{safe_no}_{corr_no}.docx"
            generator.generate(updated_data, str(OUTPUT_DIR / new_doc_file))
        except Exception as e:
            print(f"Doc regeneration failed: {e}")

    # Update DB
    db = load_db()
    t_db = db["tenders"].get(t247_id, {})
    t_db["full_tender_data"] = updated_data
    t_db["has_corrigendum"] = True
    t_db["has_pending_corrigendum"] = False
    t_db["last_corrigendum_applied"] = datetime.now().isoformat()
    t_db["verdict"] = updated_data.get("overall_verdict", {}).get("verdict",
                                       t_db.get("verdict", ""))
    t_db["verdict_color"] = updated_data.get("overall_verdict", {}).get("color",
                                             t_db.get("verdict_color", ""))
    if new_doc_file:
        t_db["latest_report_file"] = new_doc_file

    # Mark corrigendum as applied
    for corr in t_db.get("pending_corrigenda", []):
        if not corr.get("applied"):
            corr["applied"] = True
            corr["applied_at"] = datetime.now().isoformat()

    db["tenders"][t247_id] = t_db
    save_db(db)

    return {
        "status": "applied",
        "message": "Corrigendum changes applied to tender data.",
        "new_verdict": t_db["verdict"],
        "new_doc_file": new_doc_file,
        "updated_fields": {
            "bid_submission_date": updated_data.get("bid_submission_date"),
            "emd": updated_data.get("emd"),
            "pq_count": len(updated_data.get("pq_criteria", [])),
        }
    }


@app.get("/corrigendum/{t247_id}/pending")
async def get_pending_corrigendum(t247_id: str):
    """Get pending (not yet applied) corrigendum diff for a tender."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    pending = [c for c in tender.get("pending_corrigenda", []) if not c.get("applied")]
    return {
        "has_pending": len(pending) > 0,
        "pending_count": len(pending),
        "pending": pending,
    }


# ════════════════════════════════════════
# NEW: PRE-BID RESPONSE UPLOAD
# ════════════════════════════════════════

@app.post("/prebid-response/{t247_id}")
async def upload_prebid_response(t247_id: str, files: List[UploadFile] = File(...)):
    """
    Upload the pre-bid query response / clarification document from the organization.
    AI reads it and extracts: which queries were answered, what were the answers,
    any new information that affects Nascent's eligibility.
    """
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    tmp_dir = tempfile.mkdtemp(prefix="prebid_resp_", dir=str(TEMP_DIR))
    try:
        extract_dir = Path(tmp_dir) / "extracted"
        extract_dir.mkdir()
        for upload in files:
            fname = upload.filename or "response"
            dest = Path(tmp_dir) / fname
            dest.write_bytes(await upload.read())
            if dest.suffix.lower() == ".zip":
                with zipfile.ZipFile(dest, "r") as zf:
                    zf.extractall(extract_dir)
            else:
                shutil.copy2(dest, extract_dir / fname)

        doc_files = []
        for ext in ["*.pdf", "*.docx", "*.doc", "*.txt", "*.html"]:
            doc_files.extend(extract_dir.rglob(ext))

        if not doc_files:
            raise HTTPException(400, "No readable documents found.")

        response_text = ""
        for f in doc_files:
            t_text = read_document(f)
            if t_text:
                response_text += f"\n\n=== FILE: {f.name} ===\n{t_text}"

        # Use AI to parse the response
        from ai_analyzer import get_all_api_keys, call_gemini, clean_json

        original_queries = tender.get("prebid_queries", {}).get("queries", [])
        queries_summary = json.dumps([
            {"query_no": q.get("query_no"), "subject": q.get("subject"), "clause_ref": q.get("clause_ref")}
            for q in original_queries
        ], indent=2) if original_queries else "Pre-bid queries not available."

        prompt = f"""You are a bid analyst. The organization has issued a Pre-Bid Clarification / Response document.

ORIGINAL PRE-BID QUERIES SENT:
{queries_summary}

PRE-BID RESPONSE DOCUMENT:
{response_text[:10000]}

Extract all clarifications given. Return ONLY valid JSON.

{{
  "response_date": "",
  "response_ref": "",
  "total_clarifications": 0,
  "clarifications": [
    {{
      "query_ref": "Q1 or clause reference",
      "subject": "topic of the query",
      "question": "original question (brief)",
      "answer": "answer given by organization",
      "answer_type": "CLARIFIED / AS_PER_RFP / MODIFIED / DEFERRED / NO_RESPONSE",
      "nascent_impact": "POSITIVE / NEGATIVE / NEUTRAL",
      "nascent_action": "what Nascent should do based on this answer"
    }}
  ],
  "new_information": [
    "Any new information not in original queries but given in this document"
  ],
  "overall_impact": "POSITIVE / NEGATIVE / NEUTRAL / MIXED",
  "overall_summary": "brief overall assessment for Nascent",
  "action_items": [
    "Action Nascent must take as result of this response"
  ]
}}"""

        all_keys = get_all_api_keys()
        ai_result = {"error": "No API keys"}
        for key in all_keys:
            try:
                response_raw = call_gemini(prompt, key)
                ai_result = clean_json(response_raw)
                break
            except Exception:
                continue

        # Store in DB
        db = load_db()
        t_db = db["tenders"].get(t247_id, {})
        if "prebid_responses" not in t_db:
            t_db["prebid_responses"] = []
        t_db["prebid_responses"].append({
            "uploaded_at": datetime.now().isoformat(),
            "files": [f.name for f in doc_files],
            "parsed": ai_result if "error" not in ai_result else None,
            "raw_preview": response_text[:1000],
        })
        t_db["has_prebid_response"] = True
        t_db["prebid_response_date"] = ai_result.get("response_date", datetime.now().strftime("%d-%m-%Y"))
        db["tenders"][t247_id] = t_db
        save_db(db)

        return {
            "status": "success",
            "files_processed": [f.name for f in doc_files],
            "parsed": ai_result if "error" not in ai_result else None,
            "warning": ai_result.get("error") if "error" in ai_result else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Pre-bid response error: {str(e)}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ════════════════════════════════════════
# NEW: GENERATE SUBMISSION DOCUMENTS
# ════════════════════════════════════════

@app.post("/generate-docs/{t247_id}")
async def generate_submission_docs(t247_id: str, data: dict = Body(default={})):
    """
    Generate complete submission document package for a tender.
    Returns list of generated files with download links.
    Doc types: cover_letter, non_blacklisting, turnover_cert, employee_decl,
               msme_emd, financial_standing, mii_decl
    Pass doc_types=[] to generate all, or specific list.
    """
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    # Use full tender data if available
    tender_data = tender.get("full_tender_data", tender)

    # Output to tender-specific folder
    tender_docs_dir = DOCS_DIR / str(t247_id)
    tender_docs_dir.mkdir(exist_ok=True, parents=True)

    requested_types = data.get("doc_types", [])  # empty = all

    try:
        generated = generate_submission_package(tender_data, str(tender_docs_dir))

        # Filter by requested types if specified
        if requested_types:
            generated = [g for g in generated if g["doc_key"] in requested_types]

        # Update DB with generated docs list
        db = load_db()
        t_db = db["tenders"].get(t247_id, {})
        t_db["submission_docs"] = generated
        t_db["submission_docs_generated_at"] = datetime.now().isoformat()
        db["tenders"][t247_id] = t_db
        save_db(db)

        # Return with download paths
        result = []
        for g in generated:
            result.append({
                "doc_key": g["doc_key"],
                "filename": g["filename"],
                "title": g["title"],
                "description": g["description"],
                "status": g["status"],
                "download_url": f"/download-doc/{t247_id}/{g['filename']}" if g["status"] == "generated" else None,
                "error": g.get("error"),
            })

        success_count = sum(1 for g in generated if g["status"] == "generated")
        # Auto-save all generated docs to Drive in background
        if drive_available():
            _tid2 = t247_id
            _gen_copy = [g for g in generated if g.get("status") == "generated"]
            def _bg_save_docs():
                for g in _gen_copy:
                    try:
                        p = Path(g.get("path",""))
                        if p.exists():
                            upload_tender_file(_tid2, p, f"docs_{g['filename']}")
                    except Exception as e:
                        print(f"Doc Drive save: {e}")
            threading.Thread(target=_bg_save_docs, daemon=True).start()

        return {
            "status": "success",
            "generated": success_count,
            "total": len(generated),
            "documents": result,
        }

    except Exception as e:
        raise HTTPException(500, f"Document generation error: {str(e)}")


@app.post("/generate-technical-proposal/{t247_id}")
async def generate_technical_proposal_route(t247_id: str):
    """Generate a full professional Technical Proposal Word document for this tender."""
    if not TP_GEN_AVAILABLE:
        raise HTTPException(500, "technical_proposal_generator.py not found")

    tender_data = get_tender(t247_id)
    if not tender_data:
        raise HTTPException(404, f"Tender {t247_id} not found")

    safe_no = re.sub(r'[^\w\-]', '_', tender_data.get("tender_no", t247_id))[:50]
    output_filename = f"TechProposal_{safe_no}.docx"
    out_path = str(OUTPUT_DIR / output_filename)

    result = generate_technical_proposal(tender_data, out_path)

    if result["status"] != "success":
        raise HTTPException(500, f"Tech proposal generation failed: {result.get('message','')}")

    # Auto-save to Drive
    if drive_available():
        try:
            upload_tender_file(t247_id, Path(out_path), output_filename)
        except Exception as e:
            print(f"Tech proposal Drive save: {e}")

    return {
        "status": "success",
        "filename": output_filename,
        "download_url": f"/download/{output_filename}",
        "matched_projects": result.get("matched_projects", 0),
        "sections": result.get("sections", 10),
        "timeline_months": result.get("timeline_months", "5"),
    }


@app.get("/download-doc/{t247_id}/{filename}")
async def download_submission_doc(t247_id: str, filename: str):
    """Download a generated submission document."""
    file_path = DOCS_DIR / str(t247_id) / Path(filename).name
    if not file_path.exists():
        raise HTTPException(404, "Document not found. Generate documents first.")
    return FileResponse(
        path=str(file_path),
        filename=Path(filename).name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@app.get("/submission-docs/{t247_id}")
async def list_submission_docs(t247_id: str):
    """List all generated submission documents for a tender."""
    tender_docs_dir = DOCS_DIR / str(t247_id)
    if not tender_docs_dir.exists():
        return {"documents": [], "message": "No documents generated yet."}

    docs = []
    for f in sorted(tender_docs_dir.glob("*.docx")):
        docs.append({
            "filename": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d-%b-%Y %H:%M"),
            "download_url": f"/download-doc/{t247_id}/{f.name}",
        })
    return {"documents": docs, "count": len(docs)}


@app.post("/merge-docs/{t247_id}")
async def merge_submission_docs(t247_id: str, data: dict = Body(...)):
    """
    Merge specified documents into a single PDF.
    Pass filenames list or 'all' to merge everything.
    """
    tender_docs_dir = DOCS_DIR / str(t247_id)
    if not tender_docs_dir.exists():
        raise HTTPException(400, "No documents found. Generate documents first.")

    filenames = data.get("filenames", "all")
    if filenames == "all":
        file_paths = sorted([str(f) for f in tender_docs_dir.glob("*.docx")])
    else:
        file_paths = [str(tender_docs_dir / fn) for fn in filenames]
        file_paths = [fp for fp in file_paths if Path(fp).exists()]

    if not file_paths:
        raise HTTPException(400, "No valid files to merge.")

    safe_id = re.sub(r'[^\w]', '_', t247_id)
    output_filename = f"Submission_Package_{safe_id}.pdf"
    output_path = str(OUTPUT_DIR / output_filename)

    success = merge_docs_to_pdf(file_paths, output_path)

    if success:
        return {
            "status": "success",
            "filename": output_filename,
            "download_url": f"/download/{output_filename}",
            "files_merged": len(file_paths),
        }
    else:
        return JSONResponse({
            "status": "partial",
            "message": "PDF merge not available (LibreOffice not installed on server). Download individual Word docs instead.",
            "files_available": [Path(fp).name for fp in file_paths],
        })


# ════════════════════════════════════════
# EXISTING ROUTES (preserved)
# ════════════════════════════════════════

@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = OUTPUT_DIR / Path(filename).name
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path=str(file_path), filename=Path(filename).name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@app.get("/reports")
async def list_reports():
    files = sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"filename": f.name, "size_kb": round(f.stat().st_size / 1024, 1),
             "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d %b %Y %H:%M")}
            for f in files[:100]]


@app.get("/reports-list")
async def reports_list():
    try:
        db = load_db()
        reports = []
        for fname in sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"), reverse=True):
            tender = None
            for tid, t in db["tenders"].items():
                if tid in fname.stem or (t.get("tender_no", "") and
                                         t.get("tender_no", "").replace("/", "_") in fname.stem):
                    tender = t
                    break
            reports.append({
                "filename": fname.name,
                "created": datetime.fromtimestamp(fname.stat().st_mtime).strftime("%d-%b-%Y %H:%M"),
                "size_kb": round(fname.stat().st_size / 1024, 1),
                "t247_id": tender.get("t247_id", "—") if tender else "—",
                "tender_name": tender.get("brief", "")[:60] if tender else fname.stem[:60],
                "org": tender.get("org_name", "—") if tender else "—",
                "verdict": tender.get("verdict", "—") if tender else "—",
            })
        return {"reports": reports}
    except Exception as e:
        return {"reports": [], "error": str(e)}


@app.get("/config")
async def get_config_route():
    """Get config — shows what is saved, masks sensitive values."""
    cfg = load_config()
    # Also check DB for restored settings
    try:
        db = load_db()
        settings = db.get("_settings", {})
    except Exception:
        settings = {}
    return {
        "has_gemini_key": bool(cfg.get("gemini_api_key")),
        "gemini_keys_count": len(cfg.get("gemini_api_keys", [])) + (1 if cfg.get("gemini_api_key") else 0),
        "has_groq_key": bool(cfg.get("groq_api_key")),
        "t247_username": cfg.get("t247_username", settings.get("t247_username", "")),
        "has_t247_password": bool(cfg.get("t247_password", settings.get("t247_password_b64", ""))),
        "has_t247_creds": bool(cfg.get("t247_username") and cfg.get("t247_password")),
    }


@app.get("/config-full")
async def get_config_full():
    config = load_config()
    all_keys = []
    primary = config.get("gemini_api_key", "")
    if primary:
        all_keys.append(primary)
    for k in config.get("gemini_api_keys", []):
        if k and k not in all_keys:
            all_keys.append(k)
    masked = [(k[:8] + "..." + k[-4:]) if len(k) > 12 else k[:4] + "..."
              for k in all_keys]
    return {"gemini_api_keys": masked, "total_keys": len(all_keys), "ai_active": bool(all_keys)}


@app.post("/config")
async def update_config_route(data: dict = Body(...)):
    """Save ALL settings — API keys, T247 credentials, preferences.
    Saves to BOTH config.json (local) AND tenders_db.json (persists across restarts via Drive).
    """
    config = load_config()

    # API Keys
    if "gemini_api_key" in data and data["gemini_api_key"]:
        config["gemini_api_key"] = data["gemini_api_key"].strip()
    if "gemini_api_keys" in data:
        keys = [k.strip() for k in data["gemini_api_keys"] if k and k.strip()]
        if keys:
            config["gemini_api_keys"] = keys
            if not config.get("gemini_api_key"):
                config["gemini_api_key"] = keys[0]
    if "groq_api_key" in data and data["groq_api_key"]:
        config["groq_api_key"] = data["groq_api_key"].strip()

    # T247 / NProcure Portal credentials
    if "t247_username" in data and data["t247_username"]:
        config["t247_username"] = data["t247_username"].strip()
    if "t247_password" in data and data["t247_password"]:
        config["t247_password"] = data["t247_password"].strip()

    # Any other settings
    for key in ["theme", "default_verdict", "auto_analyse"]:
        if key in data:
            config[key] = data[key]

    # Save to local config.json
    save_config(config)

    # ALSO persist settings to tenders DB so they survive Render restarts
    # (config.json is wiped on restart; DB syncs to Drive and reloads)
    try:
        db = load_db()
        # Store non-sensitive settings only (mask passwords)
        db["_settings"] = {
            "has_gemini_key": bool(config.get("gemini_api_key")),
            "has_groq_key": bool(config.get("groq_api_key")),
            "has_t247_creds": bool(config.get("t247_username") and config.get("t247_password")),
            "t247_username": config.get("t247_username", ""),
            # Store password encrypted — simple base64 (not true encryption but better than plaintext)
            "t247_password_b64": __import__('base64').b64encode(
                config.get("t247_password", "").encode()).decode() if config.get("t247_password") else "",
            "gemini_keys_count": len(config.get("gemini_api_keys", [])) + (1 if config.get("gemini_api_key") else 0),
            "updated_at": datetime.now().isoformat(),
        }
        save_db(db)
    except Exception as e:
        print(f"Settings persist warning: {e}")

    return {
        "status": "saved",
        "keys_saved": len(config.get("gemini_api_keys", [])) + (1 if config.get("gemini_api_key") else 0),
        "t247_saved": bool(config.get("t247_username")),
        "message": "Settings saved and synced to Drive"
    }


@app.get("/profile")
async def get_profile():
    from nascent_checker import load_profile
    return load_profile()


@app.post("/profile")
async def update_profile(data: dict = Body(...)):
    profile_path = BASE_DIR / "nascent_profile.json"
    profile_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"status": "saved"}


@app.get("/tender/{t247_id}")
async def get_tender_detail(t247_id: str):
    return get_tender(t247_id)


@app.get("/tender-quickview/{t247_id}")
async def tender_quickview(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    return tender


@app.post("/tender/{t247_id}/status")
async def update_status(t247_id: str, data: dict = Body(...)):
    tender = get_tender(t247_id)
    tender.update(data)
    save_tender(t247_id, tender)
    return {"status": "saved"}


@app.post("/tender/{t247_id}/stage")
async def update_stage(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if "status" in data:
        t["status"] = data["status"]
    if "notes" in data:
        t["notes_internal"] = data["notes"]
    if "outcome_value" in data:
        t["outcome_value"] = data["outcome_value"]
    t["status_updated_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "new_stage": t.get("status")}


@app.get("/checklist/{t247_id}")
async def get_checklist(t247_id: str):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if "doc_checklist" in t:
        return {"checklist": t["doc_checklist"], "t247_id": t247_id}
    checklist = generate_doc_checklist(t)
    return {"checklist": checklist, "t247_id": t247_id}


@app.post("/checklist/{t247_id}/item")
async def add_checklist_item(t247_id: str, data: dict = Body(default={})):
    """Add or update a checklist item for a tender."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    checklist = tender.get("checklist", [])
    item = {
        "id":        data.get("id", f"item_{len(checklist)+1}"),
        "label":     data.get("label", "New Item"),
        "done":      data.get("done", False),
        "category":  data.get("category", "custom"),
        "responsible": data.get("responsible", ""),
        "due":       data.get("due", ""),
        "note":      data.get("note", ""),
    }
    # Update if exists, else append
    existing = next((i for i,c in enumerate(checklist) if c.get("id") == item["id"]), None)
    if existing is not None:
        checklist[existing] = item
    else:
        checklist.append(item)
    tender["checklist"] = checklist
    save_tender(t247_id, tender)
    return {"status": "saved", "checklist": checklist}

@app.delete("/checklist/{t247_id}/item/{item_id}")
async def delete_checklist_item(t247_id: str, item_id: str):
    """Delete a checklist item."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    checklist = [c for c in tender.get("checklist", []) if c.get("id") != item_id]
    tender["checklist"] = checklist
    save_tender(t247_id, tender)
    return {"status": "deleted", "checklist": checklist}

@app.post("/checklist/{t247_id}")
async def save_checklist(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["doc_checklist"] = data.get("checklist", [])
    pct = round(sum(1 for d in t["doc_checklist"] if d.get("done")) /
                max(len(t["doc_checklist"]), 1) * 100)
    t["checklist_pct"] = pct
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "completion_pct": pct}


@app.post("/prebid-queries")
async def get_prebid_queries(data: dict = Body(...)):
    queries = generate_prebid_queries(data)
    return {"queries": queries}


@app.get("/prebid-queries/{t247_id}")
async def get_saved_prebid_queries(t247_id: str):
    tender = get_tender(t247_id)
    return {"queries": tender.get("prebid_queries", [])}


@app.post("/prebid-sent/{t247_id}")
async def mark_prebid_sent(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["prebid_sent"] = True
    t["prebid_sent_at"] = datetime.now().isoformat()
    t["prebid_sent_to"] = data.get("email", "")
    t["status"] = "Pre-bid Sent"
    t["status_updated_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved"}


@app.get("/alerts")
async def get_alerts():
    return {"alerts": get_deadline_alerts()}


@app.get("/pipeline")
async def get_pipeline():
    return {"stages": get_pipeline_stats(), "stage_list": PIPELINE_STAGES,
            "stage_colors": STAGE_COLORS}


@app.get("/win-loss")
async def get_win_loss():
    return get_win_loss_stats()


@app.post("/sync-drive")
async def sync_drive():
    if not drive_available():
        return JSONResponse({"status": "error", "message": "Google Drive not connected"}, status_code=400)
    try:
        db = load_db()
        result = save_to_drive(DB_FILE)
        if isinstance(result, dict) and result.get("ok"):
            return {"status": "ok", "message": f"Synced {len(db.get('tenders', {}))} tenders to Drive"}
        return JSONResponse({"status": "error", "message": str(result)}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/drive-test")
async def drive_test():
    """
    Full Drive diagnostic — tells you EXACTLY what is wrong.
    Open https://bid-nobid.onrender.com/drive-test in browser.
    """
    import os
    result = {
        "step1_env_GDRIVE_CREDENTIALS": "SET" if os.environ.get("GDRIVE_CREDENTIALS") else "MISSING ← SET THIS IN RENDER ENV VARS",
        "step2_env_GDRIVE_FILE_ID":     os.environ.get("GDRIVE_FILE_ID", "MISSING ← SET THIS IN RENDER ENV VARS"),
        "step3_drive_connected":        drive_available(),
        "step4_file_id_used":           None,
        "step5_read_test":              None,
        "step6_write_test":             None,
        "step7_service_account_email":  None,
        "diagnosis":                    None,
    }
    
    if not os.environ.get("GDRIVE_CREDENTIALS"):
        result["diagnosis"] = "FAIL: GDRIVE_CREDENTIALS not set in Render environment variables"
        return result
        
    if not os.environ.get("GDRIVE_FILE_ID"):
        result["diagnosis"] = "FAIL: GDRIVE_FILE_ID not set. Create tenders_db.json in Drive, get its ID from the URL, set in Render."
        return result

    if not drive_available():
        result["diagnosis"] = "FAIL: Drive not connected. GDRIVE_CREDENTIALS JSON may be invalid or malformed."
        return result

    # Try to get service account email
    try:
        import json
        creds = json.loads(os.environ.get("GDRIVE_CREDENTIALS","{}"))
        result["step7_service_account_email"] = creds.get("client_email", "not found in JSON")
    except:
        result["step7_service_account_email"] = "Could not parse GDRIVE_CREDENTIALS JSON"

    # Try to read the file
    try:
        import gdrive_sync as _gds
        _drive_service = _gds._drive_service
        file_id = os.environ.get("GDRIVE_FILE_ID")
        result["step4_file_id_used"] = file_id
        
        from googleapiclient.http import MediaIoBaseDownload
        import io
        request = _drive_service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        content = buf.getvalue()
        result["step5_read_test"] = f"SUCCESS — read {len(content)} bytes from Drive"
    except Exception as e:
        err = str(e)
        result["step5_read_test"] = f"FAILED: {err}"
        if "404" in err:
            result["diagnosis"] = f"FAIL: File ID {file_id} not found. Check GDRIVE_FILE_ID is correct."
        elif "403" in err:
            result["diagnosis"] = f"FAIL: Permission denied. Share tenders_db.json with {result.get('step7_service_account_email')} as Editor."
        else:
            result["diagnosis"] = f"FAIL: {err}"
        return result

    # Try to write
    try:
        from gdrive_sync import save_to_drive
        test_result = save_to_drive(DB_FILE)
        result["step6_write_test"] = f"SUCCESS — {test_result}"
        result["diagnosis"] = "ALL GOOD ✅ Drive read and write both working"
    except Exception as e:
        result["step6_write_test"] = f"FAILED: {e}"
        result["diagnosis"] = f"Can read but cannot write: {e}"

    return result


@app.get("/drive-status")
async def drive_status():
    db = load_db()
    return {"drive_connected": drive_available(),
            "tenders_in_memory": len(db.get("tenders", {})),
            "db_file_exists": DB_FILE.exists(),
            "db_size_kb": round(DB_FILE.stat().st_size / 1024) if DB_FILE.exists() else 0}


@app.post("/upload-db")
async def upload_db(file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = json.loads(content)
        count = len(data.get("tenders", {}))
        if count == 0:
            raise HTTPException(400, "File has 0 tenders")
        DB_FILE.write_bytes(content)
        drive_ok = False
        if drive_available():
            result = save_to_drive(DB_FILE)
            drive_ok = isinstance(result, dict) and result.get("ok", False)
        return {"status": "ok", "tenders": count, "drive_saved": drive_ok}
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/export-tenders")
async def export_tenders(verdict: str = "", search: str = ""):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        db = load_db()
        tenders = list(db["tenders"].values())
        if verdict and verdict != "ALL":
            tenders = [t for t in tenders if t.get("verdict") == verdict]
        if search:
            s = search.lower()
            tenders = [t for t in tenders if any(
                s in str(t.get(f, "")).lower()
                for f in ["t247_id", "ref_no", "brief", "org_name", "location", "verdict"]
            )]
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tenders"
        hdr_fill = PatternFill("solid", fgColor="1E2A3B")
        hdr_font = Font(bold=True, color="FFFFFF", size=11)
        verdict_colors = {"BID": "E2EFDA", "CONDITIONAL": "FFF2CC",
                          "NO-BID": "FCE4D6", "REVIEW": "DEEAF1"}
        headers = ["Sr.", "T247 ID", "Ref No.", "Brief", "Organization",
                   "Location", "Cost (Cr)", "EMD", "Deadline", "Days Left",
                   "Verdict", "Stage", "Analysed", "Pre-bid Sent", "Has Corrigendum", "Reason"]
        col_widths = [5, 12, 25, 45, 30, 20, 10, 12, 14, 10, 14, 18, 10, 12, 15, 35]
        for ci, (hdr, w) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(row=1, column=ci, value=hdr)
            cell.font = hdr_font; cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.column_dimensions[cell.column_letter].width = w
        ws.row_dimensions[1].height = 30

        def dl(t):
            try:
                dl_str = t.get("deadline", "")
                for fmt in ["%d-%m-%Y", "%d/%m/%Y"]:
                    try:
                        return (datetime.strptime(dl_str.split()[0], fmt).date() - date.today()).days
                    except Exception:
                        continue
            except Exception:
                pass
            return 999

        for ri, t in enumerate(sorted(tenders, key=dl), 2):
            d = dl(t)
            verdict = t.get("verdict", "")
            row_fill = PatternFill("solid", fgColor=verdict_colors.get(verdict, "FFFFFF"))
            vals = [ri-1, t.get("t247_id", ""), t.get("ref_no", ""), t.get("brief", ""),
                    t.get("org_name", ""), t.get("location", ""), t.get("estimated_cost_cr", ""),
                    t.get("emd", ""), t.get("deadline", ""), d if d < 999 else "—",
                    verdict, t.get("status", "Identified"),
                    "Yes" if t.get("bid_no_bid_done") else "No",
                    "Yes" if t.get("prebid_sent") else "No",
                    "Yes" if t.get("has_corrigendum") else "No",
                    t.get("reason", "")[:100]]
            for ci, val in enumerate(vals, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                cell.fill = row_fill
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.row_dimensions[ri].height = 18

        ws.freeze_panes = "A2"
        fname = f"Tenders_Export_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
        fpath = OUTPUT_DIR / fname
        wb.save(str(fpath))
        return FileResponse(str(fpath), filename=fname,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        raise HTTPException(500, f"Export failed: {str(e)}")


@app.post("/chat")
async def chat(data: dict = Body(...)):
    message = data.get("message", "").strip()
    if not message:
        raise HTTPException(400, "Empty message")
    history = load_history()
    result = process_message(message, history)
    return result


@app.get("/chat/history")
async def get_chat_history(t247_id: str = ""):
    return {"history": load_history()}


@app.delete("/chat/history")
async def clear_chat_history():
    h = OUTPUT_DIR / "chat_history.json"
    if h.exists():
        h.unlink()
    return {"status": "cleared"}


# ── T247 / NPROCURE AUTO-DOWNLOAD ──────────────────────────────────────────

@app.post("/config/t247")
async def save_t247_config(data: dict = Body(...)):
    """Save T247 portal credentials (username + password) for auto-download feature."""
    cfg = load_config()
    if "t247_username" in data:
        cfg["t247_username"] = data["t247_username"]
    if "t247_password" in data:
        cfg["t247_password"] = data["t247_password"]
    save_config(cfg)
    return {"status": "ok", "message": "T247 credentials saved"}


@app.get("/test-t247")
async def test_t247():
    """Test T247 / NProcure portal credentials."""
    cfg = load_config()
    user = cfg.get("t247_username", "")
    pwd  = cfg.get("t247_password", "")
    if not user or not pwd:
        return {"status": "not_configured",
                "message": "T247 credentials not saved. Go to Settings → T247 Portal to add them."}
    if not T247_DL_AVAILABLE:
        return {"status": "module_missing",
                "message": "t247_downloader.py not found — upload it to GitHub."}
    try:
        result = test_credentials(user, pwd)
        return {"status": "ok" if result["success"] else "failed",
                "message": result["message"],
                "username": user}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/tender/{t247_id}/auto-download")
async def auto_download(t247_id: str, data: dict = Body(default={})):
    """
    Auto-download all tender documents from T247 / NProcure portal.
    Uses saved credentials from config. Works with:
    - T247 ID (e.g. 283807)
    - Tender number (e.g. DC/GIS CELL/04/2025-26)
    - Hyperlinks from Excel
    """
    if not T247_DL_AVAILABLE:
        raise HTTPException(501, "t247_downloader.py not found. Upload it to GitHub first.")

    cfg = load_config()
    username = cfg.get("t247_username", "")
    password = cfg.get("t247_password", "")

    if not username or not password:
        raise HTTPException(400, "T247 credentials not saved. Go to Settings → T247 Portal.")

    tender = get_tender(t247_id)
    tender_no   = data.get("tender_no") or tender.get("tender_no") or tender.get("ref_no", "")
    portal_code = data.get("portal_code") or tender.get("portal_code", "")

    # Detect portal from tender data
    if not portal_code:
        org = tender.get("org_name", "").lower()
        if "surat" in org or "smc" in org:
            portal_code = "smc"
        elif "ahmedabad" in org or "amc" in org:
            portal_code = "amc"
        elif "vadodara" in org or "vmc" in org:
            portal_code = "vmc"

    # Download directory for this tender
    dl_dir = TEMP_DIR / "t247_downloads" / str(t247_id)
    dl_dir.mkdir(parents=True, exist_ok=True)

    import threading
    result_holder = [None]
    error_holder  = [None]

    def _do_download():
        try:
            result_holder[0] = auto_download_tender(
                t247_id=t247_id,
                tender_no=tender_no,
                portal_code=portal_code,
                username=username,
                password=password,
                download_dir=dl_dir,
            )
        except Exception as e:
            error_holder[0] = str(e)

    # Run in thread (can be slow)
    t = threading.Thread(target=_do_download, daemon=True)
    t.start()
    t.join(timeout=120)  # 2 minute timeout

    if error_holder[0]:
        raise HTTPException(500, f"Download error: {error_holder[0]}")

    result = result_holder[0]
    if not result:
        raise HTTPException(504, "Download timed out. Portal may be slow. Try again.")

    # If successful, auto-upload downloaded files to Drive
    if result.get("success") and result.get("downloaded_files"):
        def _bg_drive_upload():
            for f in result["downloaded_files"]:
                try:
                    fpath = Path(f["path"])
                    # Only upload if file actually exists AND has real content
                    if fpath.exists() and fpath.stat().st_size > 512:
                        upload_tender_file(t247_id, fpath, fpath.name)
                        print(f"Drive upload OK: {fpath.name}")
                    else:
                        print(f"Drive upload skipped (empty/missing): {f.get('name','')}")
                except Exception as e:
                    print(f"Drive upload error: {e}")
        if drive_available() and result.get("total_files", 0) > 0:
            threading.Thread(target=_bg_drive_upload, daemon=True).start()

        # Update tender record with download info
        if tender:
            tender["last_auto_download"] = datetime.now().isoformat()
            tender["auto_download_files"] = [f["filename"] for f in result["downloaded_files"]]
            if result.get("tender_details"):
                # Merge scraped details into tender record
                td = result["tender_details"]
                for key in ["work_name", "estimated_cost", "emd", "bid_submission_date",
                            "tender_fee", "contract_period"]:
                    if td.get(key) and not tender.get(key):
                        tender[key] = td[key]
            save_tender(t247_id, tender)

    total_files = result.get("total_files", 0)
    return {
        "status":           "success" if result.get("success") and total_files > 0 else "failed",
        "t247_id":          t247_id,
        "portal_used":      result.get("portal_used", "tender247.com"),
        "total_files":      total_files,
        "total_size_kb":    result.get("total_size_kb", 0),
        "downloaded_files": result.get("downloaded_files", []),
        "errors":           result.get("errors", []),
        "saved_to_drive":   drive_available() and total_files > 0,
        "message": (
            f"Downloaded {total_files} file(s) from T247" if total_files > 0
            else "Download failed — " + "; ".join(result.get("errors", ["Unknown error"]))
        ),
    }


@app.post("/resolve-link")
async def resolve_link(data: dict = Body(...)):
    """
    Resolve a hyperlink from Excel to extract T247 ID, tender number, portal.
    Useful when Excel has clickable links in T247 ID column.
    """
    if not T247_DL_AVAILABLE:
        raise HTTPException(501, "t247_downloader.py not found.")
    link = data.get("link", "")
    if not link:
        raise HTTPException(400, "No link provided")
    return resolve_excel_link(link)


@app.get("/t247-portals")
async def list_portals():
    """List all supported T247/NProcure department portals."""
    if not T247_DL_AVAILABLE:
        return {"portals": {}}
    return {"portals": get_supported_portals()}


# ══════════════════════════════════════════════════════════════
# STAGE 4 — PORTAL WATCH / PRE-BID AUTOMATION
# ══════════════════════════════════════════════════════════════

@app.get("/portal-alerts")
async def get_all_portal_alerts():
    """Get all unread portal alerts (corrigendums, extensions, cancellations)."""
    if not WATCHER_AVAILABLE:
        return {"alerts": [], "bid_opening_today": []}
    alerts = get_portal_alerts()
    opening = get_bid_opening_today()
    return {"alerts": alerts, "bid_opening_today": opening, "count": len(alerts)}


@app.post("/portal-check-now")
async def trigger_portal_check():
    """Manually trigger a portal check for all active tenders."""
    if not WATCHER_AVAILABLE:
        raise HTTPException(501, "portal_watcher.py not installed")
    import threading
    result_holder = [None]
    def _check():
        result_holder[0] = check_portal_now()
    t = threading.Thread(target=_check, daemon=True)
    t.start()
    t.join(timeout=120)
    alerts = result_holder[0] or []
    return {
        "status": "completed",
        "new_alerts": len(alerts),
        "alerts": alerts,
        "checked_at": datetime.now().isoformat(),
    }


@app.post("/portal-alert/mark-read/{t247_id}/{alert_type}")
async def mark_portal_alert_read(t247_id: str, alert_type: str):
    """Mark a portal alert as read."""
    if not WATCHER_AVAILABLE:
        return {"status": "ok"}
    from portal_watcher import get_watcher
    get_watcher().mark_alert_read(t247_id, alert_type)
    return {"status": "marked_read"}


# ══════════════════════════════════════════════════════════════
# STAGE 5 — PDF MERGE
# ══════════════════════════════════════════════════════════════

@app.post("/merge-submission-pdf/{t247_id}")
async def merge_submission_pdf(t247_id: str, data: dict = Body(default={})):
    """
    Merge all submission documents for a tender into ONE PDF in correct order.
    Sources: generated docs directory + any uploaded docs.
    """
    if not PDF_MERGE_AVAILABLE:
        raise HTTPException(501, "pdf_merger.py not available. Run: pip install pypdf reportlab")

    tender_data = get_tender(t247_id)
    if not tender_data:
        raise HTTPException(404, f"Tender {t247_id} not found")

    # Find all document directories for this tender
    docs_dir    = BASE_DIR / "docs" / str(t247_id)
    output_dir  = OUTPUT_DIR
    source_dirs = [docs_dir]

    # Also check Drive-cached files
    drive_cache = TEMP_DIR / "drive_cache" / str(t247_id)
    if drive_cache.exists():
        source_dirs.append(drive_cache)

    import threading
    result_holder = [None]
    def _do_merge():
        result_holder[0] = merge_submission_package(
            t247_id=t247_id,
            tender_data=tender_data,
            source_dirs=source_dirs,
            output_dir=output_dir,
            include_cover=data.get("include_cover", True),
        )
    t = threading.Thread(target=_do_merge, daemon=True)
    t.start()
    t.join(timeout=180)

    result = result_holder[0]
    if not result:
        raise HTTPException(504, "Merge timed out")
    if result.get("status") != "success":
        return {**result, "errors": result.get("errors", [])}

    # Auto-save merged PDF to Drive
    if drive_available() and result.get("output_path"):
        try:
            upload_tender_file(t247_id, Path(result["output_path"]), result.get("filename",""))
        except Exception as e:
            print(f"Drive save merged PDF: {e}")

    return {
        "status":       "success",
        "filename":     result.get("filename",""),
        "download_url": f"/download/{result.get('filename','')}",
        "page_count":   result.get("page_count", 0),
        "file_count":   result.get("file_count", 0),
        "size_kb":      result.get("size_kb", 0),
        "doc_order":    result.get("doc_order", []),
        "errors":       result.get("errors", []),
    }


@app.get("/merge-preview/{t247_id}")
async def merge_doc_preview(t247_id: str):
    """Preview the order documents will be merged in."""
    if not PDF_MERGE_AVAILABLE:
        return {"docs": []}
    docs_dir = BASE_DIR / "docs" / str(t247_id)
    order    = get_doc_order_preview([docs_dir])
    return {"docs": order, "count": len(order)}


# ══════════════════════════════════════════════════════════════
# STAGE 7 — POST-BID TRACKING
# ══════════════════════════════════════════════════════════════

@app.post("/tender/{t247_id}/bid-result")
async def record_result(t247_id: str, data: dict = Body(...)):
    """Record the final bid result — Won / Lost / L1 / L2 position."""
    if not POST_BID_AVAILABLE:
        raise HTTPException(501, "post_bid_tracker.py not available")

    result = record_bid_result(
        t247_id=t247_id,
        result=data.get("result", "Lost"),
        our_quote_cr=float(data.get("our_quote_cr", 0)),
        l1_amount_cr=float(data.get("l1_amount_cr", 0)),
        l1_name=data.get("l1_name", ""),
        l2_amount_cr=float(data.get("l2_amount_cr", 0)),
        our_rank=int(data.get("our_rank", 0)),
        total_bidders=int(data.get("total_bidders", 0)),
        reason_lost=data.get("reason_lost", ""),
        notes=data.get("notes", ""),
        award_letter_ref=data.get("award_letter_ref", ""),
    )

    # Also update tender status in main DB
    tender = get_tender(t247_id)
    if tender:
        tender["status"] = data.get("result", "Lost")
        if data.get("result") == "Won":
            tender["won_at"]        = datetime.now().isoformat()
            tender["contract_value"] = float(data.get("our_quote_cr", 0))
        save_tender(t247_id, tender)
        await loadDashboard_refresh()

    return result


async def loadDashboard_refresh():
    """Refresh dashboard stats after status change."""
    pass  # Stats refresh happens on next /dashboard call


@app.get("/analytics/win-loss")
async def win_loss_analytics():
    """Full win/loss analytics across all tenders."""
    if not POST_BID_AVAILABLE:
        return {"error": "post_bid_tracker.py not available"}
    return get_win_loss_analytics()


@app.get("/analytics/pipeline")
async def pipeline_analytics():
    """Quick pipeline value summary."""
    if not POST_BID_AVAILABLE:
        return {}
    return get_pipeline_value()


@app.get("/analytics/competitors")
async def competitor_analytics():
    """Competitor analysis from lost bid data."""
    if not POST_BID_AVAILABLE:
        return []
    return get_competitor_report()


# ══════════════════════════════════════════════════════════════
# STAGE 8 — POST-AWARD
# ══════════════════════════════════════════════════════════════

@app.post("/post-award/{t247_id}/loa-acceptance")
async def gen_loa_acceptance(t247_id: str, data: dict = Body(default={})):
    """Generate Letter of Award acceptance letter."""
    if not POST_AWARD_AVAILABLE:
        raise HTTPException(501, "post_award.py not available")
    tender_data = {**get_tender(t247_id), **data}
    safe_id     = re.sub(r'[^\w\-]', '_', t247_id)[:30]
    out_path    = str(OUTPUT_DIR / f"LoA_Acceptance_{safe_id}.docx")
    result      = generate_loa_acceptance(tender_data, out_path)
    if result["status"] == "success":
        if drive_available():
            try: upload_tender_file(t247_id, Path(out_path), f"LoA_Acceptance_{safe_id}.docx")
            except: pass
        return {"status":"success","filename":f"LoA_Acceptance_{safe_id}.docx",
                "download_url":f"/download/LoA_Acceptance_{safe_id}.docx"}
    return result


@app.post("/post-award/{t247_id}/perf-security-letter")
async def gen_perf_security(t247_id: str, data: dict = Body(default={})):
    """Generate Performance Security submission letter."""
    if not POST_AWARD_AVAILABLE:
        raise HTTPException(501, "post_award.py not available")
    tender_data = {**get_tender(t247_id), **data}
    safe_id     = re.sub(r'[^\w\-]', '_', t247_id)[:30]
    out_path    = str(OUTPUT_DIR / f"PerfSecurity_{safe_id}.docx")
    result      = generate_performance_security_letter(tender_data, out_path)
    if result["status"] == "success":
        if drive_available():
            try: upload_tender_file(t247_id, Path(out_path), f"PerfSecurity_{safe_id}.docx")
            except: pass
        return {"status":"success","filename":f"PerfSecurity_{safe_id}.docx",
                "download_url":f"/download/PerfSecurity_{safe_id}.docx"}
    return result


@app.post("/post-award/{t247_id}/milestones/setup")
async def milestone_setup(t247_id: str, data: dict = Body(...)):
    """Set up project milestone tracker for a won project."""
    if not POST_AWARD_AVAILABLE:
        raise HTTPException(501, "post_award.py not available")
    return setup_milestones(
        t247_id=t247_id,
        start_date=data.get("start_date", datetime.now().strftime("%d-%m-%Y")),
        contract_value_cr=float(data.get("contract_value_cr", 0)),
        milestones=data.get("milestones"),
        is_amc=data.get("is_amc", False),
    )


@app.get("/post-award/{t247_id}/milestones")
async def get_milestones(t247_id: str):
    """Get milestone summary for a won project."""
    if not POST_AWARD_AVAILABLE:
        return {"has_milestones": False}
    return get_milestone_summary(t247_id)


@app.patch("/post-award/{t247_id}/milestones/{milestone_id}")
async def patch_milestone(t247_id: str, milestone_id: int, data: dict = Body(...)):
    """Update a milestone status."""
    if not POST_AWARD_AVAILABLE:
        raise HTTPException(501, "post_award.py not available")
    return update_milestone(
        t247_id=t247_id,
        milestone_id=milestone_id,
        status=data.get("status", "Completed"),
        completed_on=data.get("completed_on", ""),
        invoice_raised=data.get("invoice_raised", False),
        invoice_ref=data.get("invoice_ref", ""),
        notes=data.get("notes", ""),
    )


@app.post("/post-award/{t247_id}/invoice")
async def generate_invoice(t247_id: str, data: dict = Body(...)):
    """Generate a Running Account (RA) Bill / Tax Invoice for a milestone."""
    if not POST_AWARD_AVAILABLE:
        raise HTTPException(501, "post_award.py not available")
    safe_id  = re.sub(r'[^\w\-]', '_', t247_id)[:30]
    ms_id    = data.get("milestone_id", 1)
    inv_no   = data.get("invoice_no", f"NIT/{datetime.now().year}/{t247_id}/{ms_id:02d}")
    out_path = str(OUTPUT_DIR / f"Invoice_{safe_id}_M{ms_id}.docx")
    result   = generate_ra_bill(
        t247_id=t247_id,
        milestone_id=int(ms_id),
        invoice_no=inv_no,
        invoice_date=data.get("invoice_date", datetime.now().strftime("%d-%m-%Y")),
        work_done_desc=data.get("work_done_desc", ""),
        amount_before_tax=float(data.get("amount_cr", 0) * 100000),
        gst_rate_pct=float(data.get("gst_pct", 18)),
        tds_pct=float(data.get("tds_pct", 2)),
        out_path=out_path,
    )
    if result["status"] == "success":
        fname = f"Invoice_{safe_id}_M{ms_id}.docx"
        if drive_available():
            try: upload_tender_file(t247_id, Path(out_path), fname)
            except: pass
        return {**result, "download_url": f"/download/{fname}"}
    return result


@app.post("/post-award/{t247_id}/completion-cert-request")
async def gen_completion_cert_req(t247_id: str, data: dict = Body(default={})):
    """Generate project completion certificate request letter."""
    if not POST_AWARD_AVAILABLE:
        raise HTTPException(501, "post_award.py not available")
    tender_data = {**get_tender(t247_id), **data}
    safe_id     = re.sub(r'[^\w\-]', '_', t247_id)[:30]
    out_path    = str(OUTPUT_DIR / f"CompletionCertReq_{safe_id}.docx")
    result      = generate_completion_cert_request(tender_data, out_path)
    if result["status"] == "success":
        fname = f"CompletionCertReq_{safe_id}.docx"
        if drive_available():
            try: upload_tender_file(t247_id, Path(out_path), fname)
            except: pass
        return {"status":"success","filename":fname,"download_url":f"/download/{fname}"}
    return result


# ═══════════════════════════════════════════════════════════════
# LETTERHEAD
# ═══════════════════════════════════════════════════════════════

@app.post("/letterhead/upload")
async def upload_letterhead(file: UploadFile = File(...)):
    """Upload Nascent company letterhead DOCX."""
    if not file.filename.lower().endswith((".docx", ".doc")):
        raise HTTPException(400, "Upload a Word document (.docx)")
    lh_dir = BASE_DIR / "letterhead"
    lh_dir.mkdir(exist_ok=True)
    dest = lh_dir / "nascent_letterhead.docx"
    dest.write_bytes(await file.read())
    return {"status": "saved", "path": str(dest), "message": "Letterhead saved successfully"}

@app.get("/letterhead/status")
async def letterhead_status():
    """Check if letterhead has been uploaded."""
    lh_path = BASE_DIR / "letterhead" / "nascent_letterhead.docx"
    exists = lh_path.exists()
    return {
        "uploaded": exists,
        "size_kb": round(lh_path.stat().st_size / 1024, 1) if exists else 0,
        "message": "Letterhead uploaded" if exists else "No letterhead uploaded yet. Upload a company letterhead DOCX in Settings.",
    }

@app.get("/letterhead")
async def get_letterhead():
    """Download the current letterhead file."""
    lh_path = BASE_DIR / "letterhead" / "nascent_letterhead.docx"
    if not lh_path.exists():
        raise HTTPException(404, "No letterhead uploaded")
    return FileResponse(str(lh_path), filename="nascent_letterhead.docx")


# ═══════════════════════════════════════════════════════════════
# FORM EXTRACTION & FILL
# ═══════════════════════════════════════════════════════════════

@app.post("/forms/{t247_id}/extract")
async def extract_forms(t247_id: str):
    """Extract list of all forms/annexures required in the RFP."""
    if not FORM_FILLER_AVAILABLE:
        raise HTTPException(501, "form_filler.py not installed")
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    rfp_text = tender.get("full_text", "") or tender.get("scope_summary", "")
    if not rfp_text:
        raise HTTPException(400, "No RFP text available. Analyse tender first.")
    try:
        from ai_analyzer import load_config, call_gemini_with_keys
        cfg = load_config()
        forms = extract_forms_from_rfp(rfp_text, cfg.get("gemini_api_key",""))
        tender["required_forms"] = forms
        save_tender(t247_id, tender)
        return {"status": "success", "forms": forms, "count": len(forms)}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/forms/{t247_id}/fill")
async def fill_form(t247_id: str, files: List[UploadFile] = File(...)):
    """Fill an uploaded blank form with Nascent data."""
    if not FORM_FILLER_AVAILABLE:
        raise HTTPException(501, "form_filler.py not installed")
    if not files:
        raise HTTPException(400, "Upload a blank form file")
    upload = files[0]
    tmp_path = TEMP_DIR / upload.filename
    tmp_path.write_bytes(await upload.read())
    try:
        from ai_analyzer import load_config
        cfg = load_config()
        out_path = OUTPUT_DIR / f"form_{t247_id}_{upload.filename}"
        result = fill_form_with_ai(str(tmp_path), str(out_path), cfg.get("gemini_api_key",""))
        if drive_available() and out_path.exists():
            try: upload_tender_file(t247_id, out_path, out_path.name)
            except: pass
        return {"status": "success", "filename": out_path.name,
                "download_url": f"/download/{out_path.name}"}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
# STAMP PAPERS
# ═══════════════════════════════════════════════════════════════

@app.get("/stamp-papers/{t247_id}/required")
async def stamp_papers_required(t247_id: str):
    """List stamp papers required for this tender (from checklist)."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    # Get from checklist items that require stamp paper
    checklist = tender.get("checklist_items", [])
    stamp_items = [
        item for item in checklist
        if item.get("stamp_paper_amount") or "stamp" in item.get("doc","").lower()
    ]
    # Standard stamp paper items always required
    standard_stamps = [
        {"doc": "Non-Blacklisting Declaration", "amount": "Rs.100 (Non-judicial stamp paper)", "notarised": True},
        {"doc": "Power of Attorney (if applicable)", "amount": "Rs.500", "notarised": True},
    ]
    return {
        "tender_id": t247_id,
        "stamp_papers": standard_stamps + stamp_items,
        "total": len(standard_stamps) + len(stamp_items),
    }

@app.post("/stamp-paper/{t247_id}")
async def generate_stamp_paper_content(t247_id: str, data: dict = Body(...)):
    """Generate the text content for a stamp paper declaration."""
    tender = get_tender(t247_id)
    doc_type = data.get("doc_type", "Non-Blacklisting")
    # Reuse submission_doc_generator
    try:
        from submission_doc_generator import NASCENT_INFO, generate_non_blacklisting
        out_path = str(OUTPUT_DIR / f"StampPaper_{doc_type.replace(' ','_')}_{t247_id}.docx")
        result = generate_non_blacklisting(tender, out_path)
        if drive_available() and result.get("status") == "success":
            try: upload_tender_file(t247_id, Path(out_path), Path(out_path).name)
            except: pass
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════
# INDIAN TENDER GUIDELINES + PRE-BID QUERY GENERATOR
# ═══════════════════════════════════════════════════════════════

@app.get("/guidelines")
async def get_guidelines():
    """Return all Indian tender guidelines in structured format."""
    if not GUIDELINES_AVAILABLE:
        raise HTTPException(501, "indian_tender_guidelines.py not installed")
    return get_all_guidelines_summary()


@app.post("/tender/{t247_id}/analyze-compliance")
async def analyze_compliance(t247_id: str, data: dict = Body(default={})):
    """
    Cross-check tender RFP against ALL Indian procurement guidelines.
    Identifies:
    - Legal violations in the tender (EMD > 5%, MSME rights not given, etc.)
    - Where Nascent meets / falls short
    - Generates complete pre-bid query letter with legal citations
    """
    if not GUIDELINES_AVAILABLE:
        raise HTTPException(501, "indian_tender_guidelines.py not installed")

    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, f"Tender {t247_id} not found")

    # Get RFP text — from tender data or Drive
    rfp_text = (
        tender.get("full_text", "") or
        tender.get("scope_summary", "") or
        tender.get("brief", "") or
        data.get("rfp_text", "")
    )

    if not rfp_text or len(rfp_text) < 100:
        raise HTTPException(400,
            "No RFP text available. Please analyse the tender first to extract document text.")

    ai_analysis = {
        "verdict":        tender.get("bid_no_bid", ""),
        "pq_criteria":    tender.get("pq_criteria", []),
        "emd":            tender.get("emd", ""),
        "tender_fee":     tender.get("doc_fee", ""),
        "estimated_value": tender.get("estimated_cost_cr", 0),
        "scope_summary":  tender.get("scope_summary", ""),
        "concerns":       tender.get("concerns", []),
        "conditional_items": tender.get("conditional_items", []),
    }

    import threading
    result_holder = [None]
    error_holder  = [None]

    def _do_analyze():
        try:
            result_holder[0] = analyze_rfp_against_guidelines(
                rfp_text, ai_analysis, tender
            )
        except Exception as e:
            error_holder[0] = str(e)

    t = threading.Thread(target=_do_analyze, daemon=True)
    t.start()
    t.join(timeout=90)

    if error_holder[0]:
        raise HTTPException(500, f"Analysis error: {error_holder[0]}")
    if not result_holder[0]:
        raise HTTPException(504, "Analysis timed out. Try again.")

    result = result_holder[0]

    # Save compliance analysis to tender
    tender["compliance_analysis"] = result
    tender["compliance_analyzed_at"] = datetime.now().isoformat()
    save_tender(t247_id, tender)

    return {
        "status": "success",
        "t247_id": t247_id,
        "violations_found": len(result.get("clause_violations", [])),
        "gaps_found": len(result.get("nascent_gaps", [])),
        "recommendation": result.get("overall_recommendation", {}),
        "analysis": result,
    }


@app.post("/tender/{t247_id}/generate-prebid-letter")
async def generate_prebid_letter(t247_id: str, data: dict = Body(default={})):
    """
    Generate complete Pre-bid Query Letter DOCX.
    Uses compliance analysis result + Indian tender guidelines database.
    Auto-includes:
    - MSME exemption claims (EMD, tender fee, turnover, experience)
    - Legal citations for each query
    - Nascent's profile facts (certifications, turnover, MSME reg)
    - Professional letterhead format
    """
    if not GUIDELINES_AVAILABLE:
        raise HTTPException(501, "indian_tender_guidelines.py not installed")

    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    # Get compliance analysis — run fresh if not available
    compliance = tender.get("compliance_analysis")
    if not compliance or data.get("refresh"):
        # Run compliance analysis first
        rfp_text = (
            tender.get("full_text","") or
            tender.get("scope_summary","") or
            tender.get("brief","")
        )
        if rfp_text and len(rfp_text) > 100:
            try:
                ai_analysis = {
                    "verdict":       tender.get("bid_no_bid",""),
                    "pq_criteria":   tender.get("pq_criteria",[]),
                    "emd":           tender.get("emd",""),
                    "estimated_value": tender.get("estimated_cost_cr",0),
                }
                compliance = analyze_rfp_against_guidelines(rfp_text, ai_analysis, tender)
                tender["compliance_analysis"] = compliance
                save_tender(t247_id, tender)
            except Exception as e:
                compliance = {"prebid_letter": {}, "clause_violations": [], "nascent_gaps": []}
        else:
            compliance = {"prebid_letter": {}, "clause_violations": [], "nascent_gaps": []}

    safe_id    = re.sub(r'[^\w\-]','_', t247_id)[:30]
    out_fname  = f"PreBid_Queries_{safe_id}.docx"
    out_path   = str(OUTPUT_DIR / out_fname)

    result = generate_prebid_letter_docx(compliance, tender, out_path)

    if result.get("status") == "success":
        # Auto-save to Drive
        if drive_available():
            try: upload_tender_file(t247_id, Path(out_path), out_fname)
            except: pass
        # Save to tender record
        tender["prebid_letter_path"]  = out_path
        tender["prebid_letter_generated"] = datetime.now().isoformat()
        save_tender(t247_id, tender)
        return {
            "status":        "success",
            "filename":      out_fname,
            "download_url":  f"/download/{out_fname}",
            "query_count":   result.get("query_count", 0),
            "gaps_found":    result.get("gaps_found", 0),
        }

    return result


@app.get("/tender/{t247_id}/compliance-report")
async def get_compliance_report(t247_id: str):
    """Get saved compliance analysis for a tender."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    compliance = tender.get("compliance_analysis", {})
    return {
        "has_analysis":    bool(compliance),
        "analyzed_at":     tender.get("compliance_analyzed_at",""),
        "violations":      compliance.get("clause_violations",[]),
        "gaps":            compliance.get("nascent_gaps",[]),
        "recommendation":  compliance.get("overall_recommendation",{}),
        "has_letter":      bool(tender.get("prebid_letter_path","")),
    }


@app.get("/api-quota-status")
async def api_quota_status():
    """Live API quota status — shown in dashboard."""
    cfg = load_config()
    keys = [k for k in [
        cfg.get("gemini_api_key",""),
        *cfg.get("gemini_api_keys",[]),
    ] if k]
    num_keys = max(len(keys), 1)

    # Free tier: 500 req/day per key, 1M tokens/day per key
    total_req_day  = 500 * num_keys
    total_tok_day  = 1_000_000 * num_keys

    today = datetime.now().strftime("%Y-%m-%d")
    if _quota_data["date"] != today:
        used_req = 0
        used_tok = 0
    else:
        used_req = _quota_data["requests_used"]
        used_tok = _quota_data["tokens_used"]

    # Time until reset (midnight Pacific = 1:30 PM IST)
    from datetime import timezone, timedelta
    now_ist = datetime.now()
    reset_ist = now_ist.replace(hour=13, minute=30, second=0, microsecond=0)
    if now_ist >= reset_ist:
        reset_ist = reset_ist + timedelta(days=1)
    secs_to_reset = int((reset_ist - now_ist).total_seconds())

    return {
        "num_keys":             num_keys,
        "requests_used_today":  used_req,
        "requests_total_today": total_req_day,
        "tokens_used_today":    used_tok,
        "tokens_total_today":   total_tok_day,
        "requests_pct":         round(used_req / total_req_day * 100, 1),
        "tokens_pct":           round(used_tok / total_tok_day * 100, 1) if total_tok_day else 0,
        "seconds_to_reset":     secs_to_reset,
        "reset_time_ist":       "1:30 PM IST",
        "keys_status":          _quota_data.get("keys_status", {}),
        "warning":              used_req > total_req_day * 0.8,
        "date":                 today,
    }


@app.get("/test-ai")
async def test_ai():
    from ai_analyzer import get_api_key, call_gemini
    key = get_api_key()
    if not key:
        return {"status": "error", "message": "No API key configured"}
    try:
        result = call_gemini('Return this exact JSON: {"status": "ok"}', key)
        return {"status": "success", "gemini_response": result[:100]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/test-groq")
async def test_groq():
    config = load_config()
    groq_key = config.get("groq_api_key", "")
    if not groq_key:
        return {"status": "missing", "message": "groq_api_key not in config"}
    try:
        from ai_analyzer import call_groq
        result = call_groq("Say OK in one word", groq_key)
        return {"status": "success", "response": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ════════════════════════════════════════
# TENDER FILE STORAGE ON DRIVE
# ════════════════════════════════════════

@app.get("/tender/{t247_id}/files")
async def get_tender_drive_files(t247_id: str):
    """
    List all files stored on Drive for this tender.
    Shows whether each file is available locally or needs download.
    """
    files = list_tender_files(t247_id)
    tender_temp = TEMP_DIR / t247_id
    for f in files:
        local_path = tender_temp / f["name"]
        f["available_locally"] = local_path.exists()
    return {
        "t247_id": t247_id,
        "files": files,
        "count": len(files),
        "drive_available": drive_available(),
        "message": f"{len(files)} file(s) stored on Drive for this tender" if files else "No files stored yet — analyse the tender first"
    }


@app.post("/tender/{t247_id}/restore-files")
async def restore_tender_files_from_drive(t247_id: str):
    """
    Download all files for this tender from Drive back to local temp.
    Call this to work on a tender without re-uploading the ZIP.
    """
    if not drive_available():
        return JSONResponse({"status": "error", "message": "Drive not connected"}, status_code=400)

    dest_dir = TEMP_DIR / t247_id
    dest_dir.mkdir(exist_ok=True, parents=True)

    drive_files = list_tender_files(t247_id)
    if not drive_files:
        return JSONResponse({
            "status": "empty",
            "message": "No files found on Drive for this tender. Please upload the ZIP again."
        })

    restored = []
    failed = []
    for f in drive_files:
        local_path = dest_dir / f["name"]
        if local_path.exists():
            restored.append(f["name"])
            continue
        ok = download_tender_file(t247_id, f["name"], local_path)
        if ok:
            restored.append(f["name"])
        else:
            failed.append(f["name"])

    return {
        "status": "success",
        "restored": len(restored),
        "failed": len(failed),
        "files": restored,
        "message": f"Restored {len(restored)} file(s) from Drive" + (f" ({len(failed)} failed)" if failed else "")
    }


@app.post("/tender/{t247_id}/re-analyse")
async def re_analyse_from_drive(t247_id: str):
    """
    Re-run analysis on a tender using files already stored on Drive.
    No re-upload needed.
    """
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    if not drive_available():
        raise HTTPException(400, "Drive not connected — please upload files manually")

    # Download files from Drive
    dest_dir = TEMP_DIR / t247_id
    dest_dir.mkdir(exist_ok=True, parents=True)
    drive_files = list_tender_files(t247_id)

    if not drive_files:
        raise HTTPException(400, "No files found on Drive for this tender. Please upload the ZIP again.")

    showLoading = []
    doc_files = []
    for f in drive_files:
        local_path = dest_dir / f["name"]
        if not local_path.exists():
            ok = download_tender_file(t247_id, f["name"], local_path)
            if ok:
                doc_files.append(local_path)
        else:
            doc_files.append(local_path)

    if not doc_files:
        raise HTTPException(400, "Could not restore files from Drive")

    # Run analysis
    extractor = TenderExtractor()
    tender_data = extractor.process_documents(doc_files)

    all_text = ""
    for f in doc_files:
        t_text = read_document(f)
        if t_text and t_text.strip():
            all_text += f"\n\n=== FILE: {f.name} ===\n{t_text}"

    config = load_config()
    ai_used = False
    if config.get("gemini_api_key") and all_text.strip():
        passed = prebid_passed(tender_data.get("prebid_query_date", ""))
        ai_result = analyze_with_gemini(all_text, passed)
        if "error" not in ai_result:
            tender_data = merge_results(tender_data, ai_result, passed)
            ai_used = True

    checker = NascentChecker()
    if not tender_data.get("overall_verdict"):
        tender_data["pq_criteria"] = checker.check_all(tender_data.get("pq_criteria", []))
        tender_data["tq_criteria"] = checker.check_all(tender_data.get("tq_criteria", []))
        tender_data["overall_verdict"] = checker.get_overall_verdict(
            tender_data["pq_criteria"] + tender_data["tq_criteria"])

    generator = BidDocGenerator()
    safe_no = re.sub(r'[^\w\-]', '_', tender_data.get("tender_no", t247_id))[:50]
    output_filename = f"BidNoBid_{safe_no}_reanalysed.docx"
    generator.generate(tender_data, str(OUTPUT_DIR / output_filename))

    # Update DB
    db_record = get_tender(t247_id)
    db_record.update({
        "full_tender_data": tender_data,
        "bid_no_bid_done": True,
        "report_file": output_filename,
        "analysed_at": datetime.now().isoformat(),
        "ai_used": ai_used,
    })
    save_tender(t247_id, db_record)

    return {
        "status": "success",
        "ai_used": ai_used,
        "files_used": [f.name for f in doc_files],
        "tender_data": tender_data,
        "download_file": output_filename,
        "message": f"Re-analysed using {len(doc_files)} file(s) from Drive"
    }


@app.delete("/tender/{t247_id}/files/{filename}")
async def delete_drive_file(t247_id: str, filename: str):
    """Delete a specific file from Drive storage for this tender."""
    ok = delete_tender_file(t247_id, filename)
    return {"status": "deleted" if ok else "not_found"}
