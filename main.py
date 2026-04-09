"""
Bid/No-Bid Automation v6
FastAPI backend — all routes including vault, reports listing, checklist, profiles
"""

import zipfile, tempfile, shutil, json, re, os, uuid
import asyncio
from pathlib import Path
from datetime import datetime, date
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import List

from extractor import TenderExtractor, read_document
from doc_generator import BidDocGenerator
from nascent_checker import NascentChecker
from ai_analyzer import analyze_with_gemini, merge_results, load_config, save_config
from excel_processor import process_excel
from prebid_generator import generate_prebid_queries
from chatbot import process_message, load_history
from gdrive_sync import (
    init_drive, save_to_drive, load_from_drive, is_available as drive_available,
    vault_upload, vault_download, vault_list, vault_delete,
    get_auth_mode,
)
from tracker import (
    get_deadline_alerts, get_pipeline_stats,
    get_win_loss_stats, generate_doc_checklist,
    PIPELINE_STAGES, STAGE_COLORS,
)

# Safe optional imports — modules added by other AI that may not exist yet
try:
    from submission_generator import generate_submission_package
    _has_submission_generator = True
except ImportError:
    _has_submission_generator = False

try:
    from ocr_engine import is_available as ocr_available
    _has_ocr = True
except ImportError:
    _has_ocr = False
    def ocr_available(): return False

app = FastAPI(title="Bid/No-Bid System v6", version="6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

BASE_DIR    = Path(__file__).parent
RUNTIME_DIR = Path(os.environ.get("BIDNOBID_RUNTIME_DIR", "/tmp/bid-nobid"))
OUTPUT_DIR  = RUNTIME_DIR / "data"
TEMP_DIR    = RUNTIME_DIR / "temp"
VAULT_DIR   = RUNTIME_DIR / "vault"
DB_FILE     = OUTPUT_DIR / "tenders_db.json"
PROFILE_FILE = RUNTIME_DIR / "nascent_profile.json"

for d in [OUTPUT_DIR, TEMP_DIR, VAULT_DIR]:
    d.mkdir(exist_ok=True, parents=True)


# ── STARTUP ───────────────────────────────────────────────────

async def _drive_warm_sync():
    """Run Drive reads after startup so health checks are not blocked."""
    for attempt in range(2):
        try:
            success = await asyncio.wait_for(
                asyncio.to_thread(load_from_drive, DB_FILE),
                timeout=12,
            )
            if success:
                db = load_db()
                print(f"Loaded {len(db.get('tenders', {}))} tenders from Google Drive")
                break
        except asyncio.TimeoutError:
            print(f"Drive DB load attempt {attempt + 1} timed out")
        except Exception as e:
            print(f"Drive DB load attempt {attempt + 1} failed: {e}")
        await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    print("Starting Bid/No-Bid System v6...")
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    TEMP_DIR.mkdir(exist_ok=True, parents=True)
    VAULT_DIR.mkdir(exist_ok=True, parents=True)

    try:
        drive_ok = await asyncio.wait_for(asyncio.to_thread(init_drive), timeout=8)
    except asyncio.TimeoutError:
        drive_ok = False
        print("Google Drive init timed out after 8s — continuing without Drive")
    except Exception as e:
        drive_ok = False
        print(f"Google Drive init failed during startup: {e}")

    print(f"Google Drive: {'Connected' if drive_ok else 'Not configured'}")

    if drive_ok:
        asyncio.create_task(_drive_warm_sync())
    elif DB_FILE.exists():
        db = load_db()
        print(f"Using local DB: {len(db.get('tenders', {}))} tenders")
    else:
        print("No DB found — fresh start")

    print("Server ready")


# ── DB HELPERS ────────────────────────────────────────────────

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
        save_to_drive(DB_FILE)
    except Exception:
        pass


def get_tender(t247_id: str) -> dict:
    return load_db()["tenders"].get(str(t247_id), {})


def save_tender(t247_id: str, data: dict):
    db = load_db()
    db["tenders"][str(t247_id)] = data
    save_db(db)


def days_left(deadline_str: str) -> int:
    if not deadline_str:
        return 999
    for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%Y"]:
        try:
            d = datetime.strptime(str(deadline_str).split()[0], fmt).date()
            return (d - date.today()).days
        except Exception:
            continue
    return 999


def prebid_passed(date_str: str) -> bool:
    return days_left(date_str) < 0


def build_quality_flags(tender_data: dict) -> list:
    flags = []
    if not tender_data.get("tender_no"):
        flags.append("Tender number missing")
    if not tender_data.get("org_name"):
        flags.append("Organization name missing")
    if not tender_data.get("bid_submission_date"):
        flags.append("Bid submission date missing")
    if not tender_data.get("emd"):
        flags.append("EMD not found")
    if not tender_data.get("estimated_cost"):
        flags.append("Estimated cost not found")
    verdict = (tender_data.get("overall_verdict", {}) or {}).get("verdict") or tender_data.get("verdict")
    if not verdict:
        flags.append("Final verdict not available")
    pq = tender_data.get("pq_criteria", [])
    if isinstance(pq, list) and not pq:
        flags.append("PQ criteria list is empty")
    return flags


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


# ── STATIC PAGES ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = BASE_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Bid/No-Bid System v6</h1>")

@app.head("/")
async def root_head():
    return Response(status_code=200)

@app.get("/profile-page", response_class=HTMLResponse)
async def profile_page():
    p = BASE_DIR / "profile.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Profile page not found</h1>", status_code=404)

@app.get("/dashboard-page", response_class=HTMLResponse)
async def dashboard_page():
    p = BASE_DIR / "dashboard_v2.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard page not found</h1>", status_code=404)


# ── EXCEL IMPORT ──────────────────────────────────────────────

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
            if not tid:
                continue
            existing = db["tenders"].get(tid, {})
            if existing:
                excel_fields = [
                    "ref_no", "brief", "org_name", "location",
                    "estimated_cost_raw", "estimated_cost_cr",
                    "deadline", "days_left", "deadline_status",
                    "doc_fee", "emd", "msme_exemption",
                    "eligibility", "checklist", "is_gem",
                ]
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
        return {"status": "success", "total": len(tenders), "added": added, "updated": updated, "tenders": tenders}
    finally:
        tmp.unlink(missing_ok=True)


# ── DASHBOARD DATA ────────────────────────────────────────────

@app.get("/dashboard")
async def dashboard():
    db = load_db()
    tenders = list(db["tenders"].values())
    stats = {
        "total": 0, "bid": 0, "no_bid": 0, "conditional": 0,
        "review": 0, "analysed": 0, "deadline_today": 0, "deadline_3days": 0,
    }
    enriched = []
    for t in tenders:
        dl = days_left(t.get("deadline", ""))
        item = dict(t)
        item["_days_left_sort"] = dl
        enriched.append(item)
        stats["total"] += 1
        verdict = item.get("verdict")
        if verdict == "BID": stats["bid"] += 1
        elif verdict == "NO-BID": stats["no_bid"] += 1
        elif verdict == "CONDITIONAL": stats["conditional"] += 1
        elif verdict == "REVIEW": stats["review"] += 1
        if item.get("bid_no_bid_done"): stats["analysed"] += 1
        if dl == 0: stats["deadline_today"] += 1
        elif 0 < dl <= 3: stats["deadline_3days"] += 1

    tenders_sorted = sorted(enriched, key=lambda t: t.get("_days_left_sort", 999))
    for t in tenders_sorted:
        t.pop("_days_left_sort", None)
    return {"stats": stats, "tenders": tenders_sorted}


# ── PROCESS ZIP / FILES ───────────────────────────────────────

@app.post("/process")
async def process_zip(file: UploadFile = File(...), t247_id: str = ""):
    return await process_files(files=[file], t247_id=t247_id)


@app.post("/process-files")
async def process_files(
    files: List[UploadFile] = File(...),
    t247_id: str = "",
    prebid_only: str = Form(""),
):
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
            ext = dest.suffix.lower()
            if ext == ".zip":
                with zipfile.ZipFile(dest, "r") as zf:
                    zf.extractall(extract_dir)
                extract_all_zips(extract_dir)
            else:
                shutil.copy2(dest, extract_dir / fname)

        doc_files = []
        for ext in ["*.pdf", "*.docx", "*.doc", "*.txt", "*.html", "*.htm", "*.xlsx"]:
            doc_files.extend(extract_dir.rglob(ext))
        image_files = []
        for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp"]:
            image_files.extend(extract_dir.rglob(ext))

        seen, unique = set(), []
        for f in doc_files:
            if f.name not in seen:
                seen.add(f.name)
                unique.append(f)
        doc_files = unique

        if not doc_files:
            raise HTTPException(400, "No readable documents found in uploaded files.")

        logo_file = None
        if image_files:
            pref = [f for f in image_files if any(k in f.name.lower() for k in ["logo", "emblem", "seal"])]
            logo_file = (pref[0] if pref else image_files[0])
            try:
                logo_store = OUTPUT_DIR / "logos"
                logo_store.mkdir(exist_ok=True, parents=True)
                safe_ext = logo_file.suffix.lower() if logo_file.suffix else ".png"
                saved_logo = logo_store / f"{uuid.uuid4().hex}{safe_ext}"
                shutil.copy2(logo_file, saved_logo)
                logo_file = saved_logo
            except Exception:
                pass

        corrigendum_files = [f for f in doc_files if
            any(k in f.name.lower() for k in
                ["corrigendum", "addendum", "amendment", "corr_", "addend", "revised", "rectification"])]
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
            tender_data["ai_warning"] = (
                "Gemini API key not configured — using basic extraction only. "
                "Go to Settings to configure AI."
            )

        checker = NascentChecker()
        if not tender_data.get("overall_verdict"):
            tender_data["pq_criteria"] = checker.check_all(tender_data.get("pq_criteria", []))
            tender_data["tq_criteria"] = checker.check_all(tender_data.get("tq_criteria", []))
            tender_data["overall_verdict"] = checker.get_overall_verdict(
                tender_data["pq_criteria"] + tender_data["tq_criteria"]
            )

        if logo_file:
            tender_data["client_logo_file"] = str(logo_file)

        quality_flags = build_quality_flags(tender_data)
        tender_data["quality_flags"] = quality_flags
        tender_data["quality_score"] = max(0, 100 - 10 * len(quality_flags))

        prebid_mode = str(prebid_only).strip().lower() in {"1", "true", "yes", "y"}

        if prebid_mode:
            tender_data["prebid_queries"] = generate_prebid_queries(tender_data)
            return {
                "status": "success",
                "prebid_only": True,
                "ai_used": ai_used,
                "has_corrigendum": tender_data.get("has_corrigendum", False),
                "corrigendum_files": tender_data.get("corrigendum_files", []),
                "files_processed": [f.name for f in doc_files],
                "tender_data": tender_data,
                "download_file": None,
            }

        generator = BidDocGenerator()
        safe_no = re.sub(r'[^\w\-]', '_', tender_data.get("tender_no", "Report"))[:50]
        output_filename = f"BidNoBid_{safe_no}.docx"
        generator.generate(tender_data, str(OUTPUT_DIR / output_filename))

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
                "verdict": tender_data.get("overall_verdict", {}).get("verdict", ""),
                "verdict_color": tender_data.get("overall_verdict", {}).get("color", ""),
                "bid_no_bid_done": True,
                "report_file": output_filename,
                "analysed_at": datetime.now().isoformat(),
                "has_corrigendum": tender_data.get("has_corrigendum", False),
                "ai_used": ai_used,
            })
            save_tender(t247_id, db_record)

        return {
            "status": "success",
            "ai_used": ai_used,
            "has_corrigendum": tender_data.get("has_corrigendum", False),
            "corrigendum_files": tender_data.get("corrigendum_files", []),
            "files_processed": [f.name for f in doc_files],
            "tender_data": tender_data,
            "download_file": output_filename,
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(500, f"Error: {str(e)}\n{traceback.format_exc()}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── DOWNLOAD ──────────────────────────────────────────────────

@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = OUTPUT_DIR / Path(filename).name
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path=str(file_path),
        filename=Path(filename).name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

@app.get("/download-zip/{filename}")
async def download_zip(filename: str):
    file_path = OUTPUT_DIR / Path(filename).name
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path=str(file_path),
        filename=Path(filename).name,
        media_type="application/zip"
    )


# ── REPORTS ───────────────────────────────────────────────────

@app.get("/reports")
async def list_reports():
    files = sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return [
        {
            "filename": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d %b %Y %H:%M"),
        }
        for f in files[:100]
    ]

@app.get("/reports-list")
async def reports_list():
    try:
        db = load_db()
        reports = []
        for fname in sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"),
                            key=lambda f: f.stat().st_mtime, reverse=True):
            tender = None
            for tid, t in db["tenders"].items():
                ref = (t.get("tender_no", "") or "").replace("/", "_")
                if tid in fname.stem or (ref and ref in fname.stem):
                    tender = t
                    break
            reports.append({
                "filename": fname.name,
                "created": datetime.fromtimestamp(fname.stat().st_mtime).strftime("%d-%b-%Y %H:%M"),
                "size_kb": round(fname.stat().st_size / 1024, 1),
                "t247_id": tender.get("t247_id", "—") if tender else "—",
                "tender_no": tender.get("tender_no", "—") if tender else "—",
                "tender_name": (tender.get("tender_name") or tender.get("brief", ""))[:70] if tender else fname.stem[:60],
                "org": tender.get("org_name", "—") if tender else "—",
                "verdict": tender.get("verdict", "—") if tender else "—",
                "verdict_color": tender.get("verdict_color", "BLUE") if tender else "BLUE",
                "analysed_at": tender.get("analysed_at", "") if tender else "",
                "download_url": f"/download/{fname.name}",
            })
        return {"reports": reports, "total": len(reports)}
    except Exception as e:
        return {"reports": [], "total": 0, "error": str(e)}


# ── TENDER CRUD ───────────────────────────────────────────────

@app.get("/tender/{t247_id}")
async def get_tender_detail(t247_id: str):
    t = get_tender(t247_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    return t

@app.get("/tender-quickview/{t247_id}")
async def tender_quickview(t247_id: str):
    t = get_tender(t247_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    return t

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
    if "status" in data: t["status"] = data["status"]
    if "notes" in data: t["notes_internal"] = data["notes"]
    if "outcome_value" in data: t["outcome_value"] = data["outcome_value"]
    if "outcome_notes" in data: t["outcome_notes"] = data["outcome_notes"]
    t["status_updated_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "new_stage": t.get("status")}

@app.delete("/tender/{t247_id}")
async def delete_tender(t247_id: str):
    db = load_db()
    if t247_id in db["tenders"]:
        del db["tenders"][t247_id]
        save_db(db)
        return {"status": "deleted"}
    raise HTTPException(404, "Tender not found")


# ── PRE-BID QUERIES ───────────────────────────────────────────

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

@app.post("/submission-package/{t247_id}")
async def create_submission_package(t247_id: str):
    if not _has_submission_generator:
        raise HTTPException(501, "Submission package generator not yet available in this deployment.")
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    result = generate_submission_package(tender, OUTPUT_DIR)
    if result.get("error"):
        raise HTTPException(500, result["error"])
    if "zip_file" in result:
        result["download_url"] = f"/download-zip/{result['zip_file']}"
    return result

@app.get("/email-draft/{t247_id}")
async def email_draft(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    verdict = tender.get("verdict", "REVIEW")
    subject = f"[{verdict}] Tender Analysis - {tender.get('tender_no', t247_id)}"
    body = (
        f"Team,\n\n"
        f"Please find the latest analysis for tender {tender.get('tender_no', t247_id)}.\n"
        f"Organization: {tender.get('org_name', 'N/A')}\n"
        f"Deadline: {tender.get('deadline', tender.get('bid_submission_date', 'N/A'))}\n"
        f"Verdict: {verdict}\n\n"
        f"Key reason: {tender.get('reason', '')}\n\n"
        f"Regards,\nBid Automation System"
    )
    return {"subject": subject, "body": body}


# ── CHECKLIST ─────────────────────────────────────────────────

@app.get("/checklist/{t247_id}")
async def get_checklist(t247_id: str):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if not t:
        raise HTTPException(404, "Tender not found")
    if "doc_checklist" in t and t["doc_checklist"]:
        return {"checklist": t["doc_checklist"], "t247_id": t247_id, "source": "saved"}
    checklist = generate_doc_checklist(t)
    excel_checklist = t.get("checklist", [])
    if excel_checklist and isinstance(excel_checklist, list):
        existing_docs = {item["doc"] for item in checklist}
        for item in excel_checklist:
            if isinstance(item, str) and item.strip() and item.strip() not in existing_docs:
                checklist.append({
                    "doc": item.strip(),
                    "category": "Document",
                    "source": "T247 Excel",
                    "done": False,
                })
    return {"checklist": checklist, "t247_id": t247_id, "source": "generated"}

@app.post("/checklist/{t247_id}")
async def save_checklist(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if not t:
        raise HTTPException(404, "Tender not found")
    t["doc_checklist"] = data.get("checklist", [])
    done = sum(1 for d in t["doc_checklist"] if d.get("done"))
    total = max(len(t["doc_checklist"]), 1)
    t["checklist_pct"] = round(done / total * 100)
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "completion_pct": t["checklist_pct"]}


# ── NASCENT PROFILE ───────────────────────────────────────────

@app.get("/profile")
async def get_profile():
    candidates = [PROFILE_FILE, BASE_DIR / "nascent_profile.json"]
    for path in candidates:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    from nascent_checker import load_profile
    return load_profile()

@app.post("/profile")
async def update_profile(data: dict = Body(...)):
    PROFILE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"status": "saved"}


# ── VAULT ─────────────────────────────────────────────────────

@app.post("/vault/upload")
async def vault_upload_endpoint(file: UploadFile = File(...), category: str = "general"):
    allowed_categories = ["company", "financial", "certification", "project", "legal", "general"]
    if category not in allowed_categories:
        category = "general"
    file_bytes = await file.read()
    filename = file.filename or "document"
    if not drive_available():
        safe_name = re.sub(r'[^\w\-.]', '_', f"{category}_{filename}")
        local_path = VAULT_DIR / safe_name
        local_path.write_bytes(file_bytes)
        return {
            "success": True, "file_id": safe_name, "filename": safe_name,
            "category": category, "drive_url": None, "local_only": True,
            "message": "Saved locally — Drive not connected. File may be lost on restart.",
            "size_kb": len(file_bytes) // 1024,
        }
    result = vault_upload(file_bytes, filename, category)
    if result["success"]:
        (VAULT_DIR / result["filename"]).write_bytes(file_bytes)
    return result

@app.get("/vault/list")
async def vault_list_endpoint():
    if drive_available():
        files = vault_list()
    else:
        files = [
            {
                "file_id": f.name, "filename": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "category": f.name.split("_")[0] if "_" in f.name else "general",
                "local_only": True, "drive_url": None,
            }
            for f in sorted(VAULT_DIR.iterdir()) if f.is_file()
        ]
    return {"files": files, "total": len(files), "drive_connected": drive_available()}

@app.get("/vault/download/{file_id}")
async def vault_download_endpoint(file_id: str):
    local = VAULT_DIR / file_id
    if local.exists():
        ext = local.suffix.lower()
        mime_map = {
            ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        return FileResponse(str(local), filename=file_id,
                            media_type=mime_map.get(ext, "application/octet-stream"))
    if not drive_available():
        raise HTTPException(404, "File not found locally and Drive not connected")
    file_bytes = vault_download(file_id)
    if not file_bytes:
        raise HTTPException(404, "File not found in vault")
    return Response(
        content=file_bytes, media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={file_id}"}
    )

@app.delete("/vault/{file_id}")
async def vault_delete_endpoint(file_id: str):
    deleted = False
    local = VAULT_DIR / file_id
    if local.exists():
        local.unlink()
        deleted = True
    if drive_available():
        deleted = vault_delete(file_id) or deleted
    if deleted:
        return {"status": "deleted"}
    raise HTTPException(404, "File not found")


# ── CONFIG / API KEYS ─────────────────────────────────────────

@app.get("/config")
async def get_config_route():
    config = load_config()
    key = config.get("gemini_api_key", "")
    return {
        "gemini_api_key_set": bool(key),
        "gemini_api_key_preview": (key[:8] + "..." + key[-4:]) if key else "",
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
    masked = [(k[:8] + "..." + k[-4:]) if len(k) > 12 else k[:4] + "..." for k in all_keys]
    return {"gemini_api_keys": masked, "total_keys": len(all_keys), "ai_active": bool(all_keys)}

@app.post("/config")
async def update_config_route(data: dict = Body(...)):
    config = load_config()
    if data.get("gemini_api_key"):
        config["gemini_api_key"] = data["gemini_api_key"]
    if data.get("gemini_api_keys"):
        keys = [k.strip() for k in data["gemini_api_keys"] if k and k.strip()]
        config["gemini_api_keys"] = keys
        if keys:
            config["gemini_api_key"] = keys[0]
    if data.get("groq_api_key"):
        config["groq_api_key"] = data["groq_api_key"].strip()
    save_config(config)
    return {"status": "saved", "keys_saved": len(config.get("gemini_api_keys", []))}


# ── ALERTS / PIPELINE ─────────────────────────────────────────

@app.get("/alerts")
async def get_alerts():
    return {"alerts": get_deadline_alerts()}

@app.get("/pipeline")
async def get_pipeline():
    return {
        "stages": get_pipeline_stats(),
        "stage_list": PIPELINE_STAGES,
        "stage_colors": STAGE_COLORS,
    }

@app.get("/win-loss")
async def get_win_loss():
    return get_win_loss_stats()


# ── EXPORT ────────────────────────────────────────────────────

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
        verdict_colors = {
            "BID": "E2EFDA", "CONDITIONAL": "FFF2CC",
            "NO-BID": "FCE4D6", "REVIEW": "DEEAF1"
        }

        headers = ["Sr.", "T247 ID", "Reference No.", "Brief", "Organization",
                   "Location", "Cost (Cr)", "EMD", "Deadline", "Days Left",
                   "Verdict", "Stage", "Analysed", "Report"]
        col_widths = [5, 12, 25, 45, 30, 20, 10, 12, 14, 10, 14, 18, 10, 30]

        for ci, (hdr, w) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(row=1, column=ci, value=hdr)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[cell.column_letter].width = w
        ws.row_dimensions[1].height = 28
        ws.freeze_panes = "A2"

        def dl(t):
            try:
                return days_left(t.get("deadline", ""))
            except Exception:
                return 999

        for ri, t in enumerate(sorted(tenders, key=dl), 2):
            d = dl(t)
            verdict = t.get("verdict", "")
            row_fill = PatternFill("solid", fgColor=verdict_colors.get(verdict, "FFFFFF"))
            report_url = f"/download/{t.get('report_file', '')}" if t.get("report_file") else ""
            vals = [
                ri - 1, t.get("t247_id", ""), t.get("ref_no", ""),
                t.get("brief", ""), t.get("org_name", ""), t.get("location", ""),
                t.get("estimated_cost_cr", ""), t.get("emd", ""), t.get("deadline", ""),
                d if d < 999 else "—", verdict, t.get("status", "Identified"),
                "Yes" if t.get("bid_no_bid_done") else "No", report_url
            ]
            for ci, val in enumerate(vals, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                cell.fill = row_fill
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.row_dimensions[ri].height = 18

        fname = f"Tenders_Export_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
        fpath = OUTPUT_DIR / fname
        wb.save(str(fpath))
        return FileResponse(
            str(fpath), filename=fname,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        raise HTTPException(500, f"Export failed: {str(e)}")


# ── DRIVE / DB SYNC ───────────────────────────────────────────

@app.post("/upload-db")
async def upload_db(file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = json.loads(content)
        count = len(data.get("tenders", {}))
        if count == 0:
            raise HTTPException(400, "File has 0 tenders")
        DB_FILE.write_bytes(content)
        drive_ok = save_to_drive(DB_FILE) if drive_available() else False
        return {"status": "ok", "tenders": count, "drive_saved": drive_ok,
                "message": f"Loaded {count} tenders."}
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/sync-drive")
async def sync_drive():
    if not drive_available():
        return JSONResponse({"status": "error", "message": "Google Drive not connected"}, status_code=400)
    db = load_db()
    ok = save_to_drive(DB_FILE)
    count = len(db.get("tenders", {}))
    if ok:
        return {"status": "ok", "message": f"Synced {count} tenders to Drive"}
    return JSONResponse({"status": "error", "message": "Sync failed"}, status_code=500)

@app.get("/drive-status")
async def drive_status():
    db = load_db()
    tenders_count = len(db.get("tenders", {}))
    oauth_fields = {
        "GDRIVE_OAUTH_CLIENT_ID": bool(os.environ.get("GDRIVE_OAUTH_CLIENT_ID", "").strip()),
        "GDRIVE_OAUTH_CLIENT_SECRET": bool(os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET", "").strip()),
        "GDRIVE_OAUTH_REFRESH_TOKEN": bool(os.environ.get("GDRIVE_OAUTH_REFRESH_TOKEN", "").strip()),
    }
    service_account_set = bool(os.environ.get("GDRIVE_CREDENTIALS", "").strip())

    return {
        "drive_connected": drive_available(),
        "auth_mode": get_auth_mode(),
        "oauth2_env": oauth_fields,
        "service_account_env": service_account_set,
        "tenders_in_db": tenders_count,
        "tenders_in_memory": tenders_count,  # backward-compatible alias for older UI code
        "db_file_exists": DB_FILE.exists(),
        "db_size_kb": round(DB_FILE.stat().st_size / 1024) if DB_FILE.exists() else 0,
        "profile_exists": PROFILE_FILE.exists(),
        "vault_local_files": len(list(VAULT_DIR.iterdir())) if VAULT_DIR.exists() else 0,
    }


# ── CHAT ─────────────────────────────────────────────────────

@app.post("/chat")
async def chat(data: dict = Body(...)):
    message = data.get("message", "").strip()
    if not message:
        raise HTTPException(400, "Empty message")

    context = data.get("context", {})
    tender_data = context.get("tender_data", {})
    history = load_history()
    result = process_message(message, history)

    correction_applied = None
    updated_tender_data = None
    import re as _re
    text_lower = message.lower()

    pq_match = _re.search(
        r'(?:change|mark|set|update)\s+(?:pq|criterion|criteria)\s+(\d+)\s+(?:to|as)\s+(met|not met|conditional|review)',
        text_lower
    )
    if pq_match and tender_data:
        idx = int(pq_match.group(1)) - 1
        new_status = pq_match.group(2).strip()
        new_status_title = {"met": "Met", "not met": "Not Met",
                            "conditional": "Conditional", "review": "Review"}.get(new_status, new_status.title())
        new_color = {"Met": "GREEN", "Not Met": "RED",
                     "Conditional": "AMBER", "Review": "BLUE"}.get(new_status_title, "BLUE")
        pq_list = tender_data.get("pq_criteria", [])
        if 0 <= idx < len(pq_list):
            pq_list[idx]["nascent_status"] = new_status_title
            pq_list[idx]["nascent_color"] = new_color
            pq_list[idx]["nascent_remark"] = (
                pq_list[idx].get("nascent_remark", "") + f" [Manually corrected to {new_status_title}]"
            )
            tender_data["pq_criteria"] = pq_list
            correction_applied = {"type": "pq_status", "index": idx,
                                   "new_status": new_status_title, "new_color": new_color}
            updated_tender_data = tender_data

    verdict_match = _re.search(
        r'(?:change|update|set)\s+verdict\s+to\s+(bid|no-bid|conditional)',
        text_lower
    )
    if verdict_match and tender_data:
        new_verdict = verdict_match.group(1).upper()
        color_map = {"BID": "GREEN", "NO-BID": "RED", "CONDITIONAL": "AMBER"}
        if "overall_verdict" not in tender_data:
            tender_data["overall_verdict"] = {}
        tender_data["overall_verdict"]["verdict"] = new_verdict
        tender_data["overall_verdict"]["color"] = color_map.get(new_verdict, "BLUE")
        tender_data["verdict"] = new_verdict
        correction_applied = {"type": "verdict", "new_verdict": new_verdict}
        updated_tender_data = tender_data

    response = {"response": result.get("response") or result.get("message") or "Done."}
    if correction_applied:
        response["correction_applied"] = correction_applied
        response["updated_tender_data"] = updated_tender_data
        idx_label = str(idx + 1) if pq_match else ""
        response["response"] = (
            f"Done. "
            f"{'PQ ' + idx_label + ' updated to ' + new_status_title if pq_match else ''}"
            f"{'Verdict updated to ' + new_verdict if verdict_match else ''}. "
            "Correction saved and preview will refresh."
        )
    return response

@app.get("/chat/history")
async def get_chat_history():
    return {"history": load_history()}

@app.delete("/chat/history")
async def clear_chat_history():
    h = OUTPUT_DIR / "chat_history.json"
    if h.exists():
        h.unlink()
    return {"status": "cleared"}


# ── CORRECTIONS (SELF-LEARNING) ───────────────────────────────

CORRECTIONS_FILE = BASE_DIR / "corrections.json"

def load_corrections() -> list:
    if CORRECTIONS_FILE.exists():
        try:
            return json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def save_corrections(corrections: list):
    CORRECTIONS_FILE.write_text(json.dumps(corrections, indent=2, default=str), encoding="utf-8")
    try:
        if drive_available():
            save_to_drive(CORRECTIONS_FILE, "corrections.json")
    except Exception:
        pass

@app.post("/save-correction")
async def save_correction_route(data: dict = Body(...)):
    corrections = load_corrections()
    correction = {
        "id": len(corrections) + 1,
        "text": data.get("text", ""),
        "correction": data.get("correction", {}),
        "tender_no": data.get("tender_no", ""),
        "timestamp": datetime.now().isoformat(),
        "applied": False,
    }
    corrections.append(correction)
    save_corrections(corrections)
    return {
        "status": "saved",
        "correction_id": correction["id"],
        "message": "Correction saved."
    }

@app.get("/corrections")
async def get_corrections():
    corrections = load_corrections()
    return {"corrections": corrections, "total": len(corrections)}

@app.delete("/corrections/{correction_id}")
async def delete_correction(correction_id: int):
    corrections = load_corrections()
    corrections = [c for c in corrections if c.get("id") != correction_id]
    save_corrections(corrections)
    return {"status": "deleted"}


# ── HEALTH / DIAGNOSTICS ──────────────────────────────────────

@app.get("/health")
async def health():
    config = load_config()
    db = load_db()
    return {
        "status": "ok",
        "version": "6.0",
        "ai_configured": bool(config.get("gemini_api_key")),
        "drive_sync": drive_available(),
        "ocr_available": ocr_available(),
        "tenders_loaded": len(db.get("tenders", {})),
        "vault_local_files": len(list(VAULT_DIR.iterdir())) if VAULT_DIR.exists() else 0,
    }

@app.get("/healthz")
@app.head("/healthz")
async def healthz():
    return Response(status_code=200)

@app.get("/test-ai")
async def test_ai():
    from ai_analyzer import get_api_key, call_gemini
    key = get_api_key()
    if not key:
        return {"status": "error", "message": "No API key in config.json"}
    try:
        result = call_gemini('Return this exact JSON: {"status": "ok"}', key)
        return {"status": "success", "api_key_present": True, "gemini_response": result[:100]}
    except Exception as e:
        return {"status": "error", "api_key_present": True, "error": str(e)}

@app.get("/test-groq")
async def test_groq():
    config = load_config()
    groq_key = config.get("groq_api_key", "")
    if not groq_key:
        return {"status": "missing", "message": "groq_api_key not found in config.json"}
    import urllib.request
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": "Say OK"}],
            "max_tokens": 5
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + groq_key},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
        return {"status": "success", "response": result["choices"][0]["message"]["content"]}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/auto-download/{t247_id}")
async def auto_download_tender(t247_id: str):
    try:
        import importlib.util
        if importlib.util.find_spec("downloader") is None:
            return {
                "status": "unavailable",
                "message": "Tender247 auto-download not available in this deployment. Please use manual upload.",
            }
        from downloader import download_sync, is_playwright_available
        if not is_playwright_available():
            return {"status": "unavailable", "message": "Playwright not installed."}
        zip_path = download_sync(t247_id)
        if zip_path:
            return {"status": "success", "zip_path": zip_path, "t247_id": t247_id}
        return {"status": "failed", "message": "Could not find download button on T247 page"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
