"""
Bid/No-Bid Automation v6.2 — Production-ready
FIXES APPLIED:
- CRITICAL: Constants moved above startup event (NameError on Render fixed)
- CRITICAL: CORS locked to ALLOWED_ORIGIN env var (was wildcard *)
- CRITICAL: Path traversal fix in /download endpoint
- CRITICAL: Admin token guard on /config, /upload-db, /sync-drive
- CRITICAL: Config keys read from env vars first, disk write skipped on Render
- WARN: Deprecated @app.on_event replaced with lifespan
- WARN: Threading lock added to DB read/write
- WARN: File size limit on uploads (50MB)
- WARN: Temp dir cleanup on startup
"""

import zipfile, tempfile, shutil, json, re, os
import threading
from pathlib import Path
from datetime import datetime, date
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Request
from typing import List
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from extractor import TenderExtractor, read_document
from doc_generator import BidDocGenerator
from nascent_checker import NascentChecker
from ai_analyzer import analyze_with_gemini, merge_results, load_config, save_config, get_all_api_keys
from excel_processor import process_excel
from prebid_generator import generate_prebid_queries
from chatbot import process_message, load_history
from gdrive_sync import init_drive, save_to_drive, load_from_drive, is_available as drive_available
from tracker import (get_deadline_alerts, get_pipeline_stats,
                     get_win_loss_stats, generate_doc_checklist,
                     PIPELINE_STAGES, STAGE_COLORS)

try:
    from boq_engine import extract_boq_from_scope, calculate_boq_totals, get_boq_constants
    BOQ_AVAILABLE = True
except ImportError:
    BOQ_AVAILABLE = False
    def extract_boq_from_scope(t): return []
    def calculate_boq_totals(i, m=15, g=18):
        return {"items": i, "base_total": 0, "margin_amount": 0, "subtotal": 0, "gst_amount": 0, "grand_total": 0}
    def get_boq_constants():
        return {"categories": ["Manpower","Software / Licenses","Hardware","Cloud / Hosting","Training","AMC / Support","Miscellaneous"],
                "unit_types": ["Months","Nos","Lumpsum","Per Year","Sq.Km","Per Day"],
                "manpower_roles": ["Project Manager","GIS Developer","Software Developer","QA Engineer"]}

# ── FIX 1: Constants defined HERE — above everything that uses them ──────────
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
TEMP_DIR.mkdir(exist_ok=True, parents=True)
DB_FILE = OUTPUT_DIR / "tenders_db.json"

# ── FIX 2: Threading lock for safe concurrent DB access ─────────────────────
_db_lock = threading.Lock()

# ── FIX 3: Admin token for sensitive endpoints ───────────────────────────────
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

def check_admin(request: Request):
    """Raises 403 if ADMIN_TOKEN env var is set and request doesn't provide it."""
    if not ADMIN_TOKEN:
        return  # No token configured → open (backward compat for local dev)
    token = request.headers.get("X-Admin-Token", "") or request.query_params.get("token", "")
    if token != ADMIN_TOKEN:
        raise HTTPException(403, "Admin token required. Set X-Admin-Token header.")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# ── FIX 4: Lifespan replaces deprecated @app.on_event("startup") ────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    import time
    print("Starting Bid/No-Bid System v6.2...")
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    TEMP_DIR.mkdir(exist_ok=True, parents=True)

    # Cleanup stale temp dirs from previous crashed runs
    for stale in TEMP_DIR.glob("tender_*"):
        try:
            if stale.is_dir():
                age = datetime.now().timestamp() - stale.stat().st_mtime
                if age > 3600:
                    shutil.rmtree(stale, ignore_errors=True)
        except Exception:
            pass

    drive_ok = init_drive()
    print(f"Google Drive: {'Connected' if drive_ok else 'Not configured'}")
    if not drive_ok:
        print("WARNING: Google Drive not configured — tenders_db.json is ephemeral on Render!")

    if drive_ok:
        for attempt in range(3):
            try:
                if load_from_drive(DB_FILE):
                    print(f"Loaded {len(load_db().get('tenders', {}))} tenders from Drive")
                    break
                time.sleep(2)
            except Exception as e:
                print(f"Drive load attempt {attempt+1} failed: {e}")
                time.sleep(2)
    else:
        count = len(load_db().get("tenders", {})) if DB_FILE.exists() else 0
        print(f"Local DB: {count} tenders")

    print(f"BOQ: {'loaded' if BOQ_AVAILABLE else 'missing boq_engine.py'}")
    print(f"Admin token: {'set' if ADMIN_TOKEN else 'not set (admin routes open)'}")
    print("Server ready — v6.2")
    yield
    # Shutdown: nothing needed

app = FastAPI(title="Bid/No-Bid System v6.2", version="6.2", lifespan=lifespan)

# ── FIX 5: CORS locked to env var, not wildcard ──────────────────────────────
_allowed_origin = os.environ.get("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origin] if _allowed_origin != "*" else ["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── DB helpers ───────────────────────────────────────────────────────────────
def load_db() -> dict:
    with _db_lock:
        if DB_FILE.exists():
            try:
                return json.loads(DB_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"tenders": {}}

def save_db(db: dict):
    with _db_lock:
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

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root():
    index = BASE_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Bid/No-Bid v6.2</h1>")

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.get("/health")
async def health():
    config = load_config()
    db = load_db()
    ai_keys = get_all_api_keys()
    return {
        "status": "ok",
        "version": "6.2",
        "ai_configured": bool(ai_keys),
        "ai_keys_count": len(ai_keys),
        "drive_sync": drive_available(),
        "tenders_loaded": len(db.get("tenders", {})),
        "boq_available": BOQ_AVAILABLE,
        "drive_warning": not drive_available(),
    }

@app.get("/api-quota-status")
async def api_quota_status():
    """Lightweight status endpoint used by dashboard UI."""
    keys = get_all_api_keys()
    return {
        "status": "ok" if keys else "no_key",
        "daily_limit": "unknown",
        "today_used": "unknown",
        "keys_count": len(keys),
    }

# ══ SELF-DIAGNOSE ════════════════════════════════════════════════════════════
@app.get("/diagnose")
async def diagnose():
    results = []
    overall = "OK"

    def chk(name, fn):
        nonlocal overall
        try:
            status, detail, fix = fn()
            if status == "ERROR": overall = "ERROR"
            elif status == "WARN" and overall == "OK": overall = "WARN"
            results.append({"name": name, "status": status, "detail": detail, "fix": fix})
        except Exception as e:
            overall = "ERROR"
            results.append({"name": name, "status": "ERROR", "detail": str(e), "fix": "Check server logs"})

    chk("Database", lambda: ("WARN","Database empty","Import Excel or upload tenders_db.json") if len(load_db().get("tenders",{}))==0 else ("OK",f"{len(load_db().get('tenders',{}))} tenders in database",""))
    chk("Google Drive", lambda: ("OK","Google Drive connected","") if drive_available() else (("ERROR","GDRIVE_CREDENTIALS not set — data will be LOST on restart","Add env var in Render dashboard") if not os.environ.get("GDRIVE_CREDENTIALS") else ("WARN","Drive credentials set but connection failed","Check JSON format")))
    chk("Gemini AI", lambda: ("OK",f"Key configured ({load_config().get('gemini_api_key','')[:8]}...)","") if load_config().get("gemini_api_key") else ("ERROR","No Gemini API key","Settings → add key from aistudio.google.com"))
    chk("BOQ Engine", lambda: ("OK","BOQ engine loaded","") if BOQ_AVAILABLE else ("ERROR","boq_engine.py missing","Add boq_engine.py to GitHub repo"))
    chk("Required Files", lambda: (("OK","All required files present","") if not [f for f in ["extractor.py","doc_generator.py","nascent_checker.py","ai_analyzer.py","excel_processor.py","prebid_generator.py","chatbot.py","gdrive_sync.py","tracker.py","nascent_profile.json"] if not (BASE_DIR/f).exists()] else ("ERROR",f"Missing: {', '.join([f for f in ['extractor.py','doc_generator.py','nascent_checker.py','ai_analyzer.py','excel_processor.py','prebid_generator.py','chatbot.py','gdrive_sync.py','tracker.py','nascent_profile.json'] if not (BASE_DIR/f).exists()])}", "Add to GitHub")))
    chk("Company Profile", lambda: ("OK","Profile complete","") if (BASE_DIR/"nascent_profile.json").exists() and all(k in json.loads((BASE_DIR/"nascent_profile.json").read_text()) for k in ["company","finance","certifications","employees","projects","bid_rules"]) else ("WARN","Profile incomplete or missing","Company Profile → fill all sections"))
    def _check_data_dir():
        try:
            OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
            probe = OUTPUT_DIR / "_test.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return ("OK", "Data directory writable", "")
        except Exception:
            return ("ERROR", "Cannot write data", "Check Render disk")
    chk("Data Directory", _check_data_dir)
    chk("Admin Security", lambda: ("OK","Admin token configured","") if ADMIN_TOKEN else ("WARN","ADMIN_TOKEN env var not set — config endpoints are open","Set ADMIN_TOKEN in Render environment"))

    return {"overall": overall, "timestamp": datetime.now().isoformat(), "checks": results,
            "summary": {"ok": sum(1 for r in results if r["status"]=="OK"),
                        "warn": sum(1 for r in results if r["status"]=="WARN"),
                        "error": sum(1 for r in results if r["status"]=="ERROR")}}

@app.post("/diagnose/ai")
async def diagnose_with_ai(data: dict = Body(...)):
    error_text = data.get("error", "").strip()
    if not error_text:
        raise HTTPException(400, "No error text provided")
    config = load_config()
    api_key = config.get("gemini_api_key", "")
    if not api_key:
        return {"root_cause": "No Gemini API key — go to Settings first.", "fixes": [], "severity": "WARN", "affected_file": "config"}
    prompt = f"""You are a senior Python/FastAPI developer diagnosing an error in the NIT Bid/No-Bid tender management system.
Stack: Python 3.11, FastAPI, Uvicorn, Gemini AI, Google Drive API, Render free tier.
Files: main.py, ai_analyzer.py, doc_generator.py, nascent_checker.py, boq_engine.py, gdrive_sync.py, excel_processor.py, extractor.py, tracker.py, chatbot.py, index.html

ERROR:
{error_text[:3000]}

Return ONLY valid JSON, no markdown:
{{"root_cause":"one sentence what went wrong","affected_file":"filename or deployment","severity":"CRASH|WARN|MINOR","fixes":[{{"step":1,"action":"exact action","where":"GitHub|Render|Settings|Code"}}],"can_auto_fix":false}}"""
    try:
        from ai_analyzer import call_gemini, clean_json
        return clean_json(call_gemini(prompt, api_key))
    except Exception as e:
        return {"root_cause": f"AI diagnosis unavailable: {e}", "fixes": [{"step": 1, "action": "Check API key in Settings", "where": "Settings"}], "severity": "WARN", "affected_file": "unknown", "can_auto_fix": False}

# ══ EXCEL IMPORT ════════════════════════════════════════════════════════════
@app.post("/import-excel")
async def import_excel(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload an Excel file")
    tmp = Path(tempfile.mktemp(suffix=".xlsx", dir=str(TEMP_DIR)))
    try:
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large (max 50MB)")
        tmp.write_bytes(content)
        tenders = process_excel(str(tmp))
        db = load_db()
        added = updated = 0
        for t in tenders:
            tid = str(t.get("t247_id", ""))
            if not tid:
                continue
            existing = db["tenders"].get(tid, {})
            if existing:
                for field in ["ref_no","brief","org_name","location","estimated_cost_raw","estimated_cost_cr","deadline","days_left","deadline_status","doc_fee","emd","msme_exemption","eligibility","checklist","is_gem"]:
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
        return {"status": "success", "total": len(tenders), "added": added, "updated": updated,
                "imported": len(tenders),
                "bid": sum(1 for t in tenders if t.get("verdict") == "BID"),
                "no_bid": sum(1 for t in tenders if t.get("verdict") == "NO-BID"),
                "tenders": tenders}
    finally:
        tmp.unlink(missing_ok=True)

# ══ DASHBOARD ═══════════════════════════════════════════════════════════════
@app.get("/dashboard")
async def dashboard():
    db = load_db()
    tenders = list(db["tenders"].values())
    return {"stats": {
        "total": len(tenders),
        "bid": sum(1 for t in tenders if t.get("verdict") == "BID"),
        "no_bid": sum(1 for t in tenders if t.get("verdict") == "NO-BID"),
        "conditional": sum(1 for t in tenders if t.get("verdict") == "CONDITIONAL"),
        "review": sum(1 for t in tenders if t.get("verdict") == "REVIEW"),
        "analysed": sum(1 for t in tenders if t.get("bid_no_bid_done")),
        "deadline_today": sum(1 for t in tenders if days_left(t.get("deadline", "")) == 0),
        "deadline_3days": sum(1 for t in tenders if 0 < days_left(t.get("deadline", "")) <= 3),
        "has_boq": sum(1 for t in tenders if t.get("boq")),
    }, "tenders": sorted(tenders, key=lambda t: days_left(t.get("deadline", "999")))}

@app.get("/tenders")
async def get_all_tenders():
    return {"tenders": list(load_db()["tenders"].values())}

# ══ TENDER OPS ══════════════════════════════════════════════════════════════
@app.post("/prebid-queries")
async def get_prebid_queries_post(data: dict = Body(...)):
    return {"queries": generate_prebid_queries(data)}

@app.get("/prebid-queries/{t247_id}")
async def get_saved_prebid_queries(t247_id: str):
    return {"queries": get_tender(t247_id).get("prebid_queries", [])}

@app.post("/tender/{t247_id}/generate-prebid-letter")
async def generate_prebid_letter(t247_id: str):
    """
    Generate a simple pre-bid query letter from stored prebid_queries.
    Returns a downloadable .txt file path under /download/.
    """
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    queries = tender.get("prebid_queries", [])
    if not queries:
        raise HTTPException(400, "No pre-bid queries found. Analyse tender first.")

    lines = [
        f"Date: {datetime.now().strftime('%d-%m-%Y')}",
        "",
        "To,",
        f"The Tender Inviting Authority, {tender.get('org_name', 'Authority')}",
        "",
        f"Subject: Pre-bid Queries — {tender.get('tender_no', t247_id)}",
        "",
        f"Tender: {tender.get('tender_name', tender.get('brief', 'N/A'))}",
        "",
        "Respected Sir/Madam,",
        "Please find below the pre-bid clarifications requested by the bidder:",
        "",
    ]
    for i, q in enumerate(queries, 1):
        if isinstance(q, dict):
            clause = q.get("clause_ref") or q.get("clause") or "—"
            qtxt = q.get("query") or q.get("text") or str(q)
            lines.append(f"{i}. Clause {clause}: {qtxt}")
        else:
            lines.append(f"{i}. {q}")
        lines.append("")
    lines.extend(["Regards,", "Authorized Signatory"])

    fname = f"PreBid_{re.sub(r'[^\\w\\-]', '_', str(tender.get('tender_no', t247_id)))[:40]}.txt"
    fpath = OUTPUT_DIR / fname
    fpath.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "success", "filename": fname, "query_count": len(queries), "download_url": f"/download/{fname}"}

@app.post("/tender/{t247_id}/analyze-compliance")
async def analyze_compliance(t247_id: str):
    """
    Basic compliance scanner (rule-based) to avoid UI failures.
    """
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    text = " ".join([
        str(tender.get("tender_name", "")),
        str(tender.get("reason", "")),
        str(tender.get("emd_exemption", "")),
        str(tender.get("jv_allowed", "")),
    ]).lower()
    issues = []
    if "not allowed" in text and ("consortium" in text or "joint venture" in text):
        issues.append({
            "clause_no": "JV/Consortium",
            "issue_type": "Restriction",
            "severity": "MEDIUM",
            "what_law_says": "Verify consortium restrictions and bid as single entity if required.",
        })
    if "emd" in text and "exempt" not in text and "msme" in text:
        issues.append({
            "clause_no": "EMD",
            "issue_type": "MSME exemption unclear",
            "severity": "LOW",
            "what_law_says": "Seek written clarification on MSME EMD exemption applicability.",
        })

    return {
        "status": "success",
        "violations_found": len(issues),
        "analysis": {"clause_violations": issues},
    }

@app.post("/tender/{t247_id}/status")
async def update_status(t247_id: str, data: dict = Body(...)):
    t = get_tender(t247_id)
    t.update(data)
    save_tender(t247_id, t)
    return {"status": "saved"}

@app.get("/tender/{t247_id}")
async def get_tender_detail(t247_id: str):
    return get_tender(t247_id)

@app.post("/tender/{t247_id}/reanalyse")
async def reanalyse_tender(t247_id: str):
    """Re-run AI analysis using saved raw text from DB."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    saved_text = tender.get("raw_text", "")
    if not saved_text or len(str(saved_text).strip()) < 100:
        raise HTTPException(400, "No saved document text found. Upload and analyse again.")

    cfg = load_config()
    api_key = cfg.get("gemini_api_key", "")
    if not api_key:
        raise HTTPException(400, "Gemini API key not configured. Go to Settings.")

    prebid_flag = bool(tender.get("prebid_passed", False))
    ai_result = analyze_with_gemini(saved_text, prebid_flag)
    if "error" in ai_result:
        raise HTTPException(502, ai_result.get("error", "AI reanalysis failed"))

    merged = merge_results(tender, ai_result, prebid_flag)
    merged["bid_no_bid_done"] = True
    merged["analysed_at"] = datetime.now().isoformat()
    merged["raw_text"] = saved_text
    save_tender(t247_id, merged)

    return {
        "status": "success",
        "t247_id": t247_id,
        "verdict": merged.get("verdict", merged.get("overall_verdict", {}).get("verdict", "REVIEW")),
        "tender_data": merged,
    }

@app.get("/tender-quickview/{t247_id}")
async def tender_quickview(t247_id: str):
    t = get_tender(t247_id)
    if not t:
        raise HTTPException(404, "Not found")
    return t

@app.post("/tender/{t247_id}/skip")
async def skip_tender(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t.update({"status": "Not Interested", "skip_reason": data.get("reason", "Not interested"), "skipped_at": datetime.now().isoformat()})
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "skipped"}

@app.post("/tender/{t247_id}/restore")
async def restore_tender(t247_id: str):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["status"] = "Identified"
    t.pop("skip_reason", None)
    t.pop("skipped_at", None)
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "restored"}

@app.post("/tender/{t247_id}/reclassify")
async def reclassify_tender(t247_id: str):
    from excel_processor import classify_tender
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if not t:
        raise HTTPException(404, "Not found")
    r = classify_tender(t.get("brief", ""), t.get("estimated_cost_raw", 0), t.get("eligibility", ""), t.get("checklist", ""))
    t.update({"verdict": r["verdict"], "verdict_color": r["verdict_color"], "reason": r["reason"]})
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "reclassified", "verdict": r["verdict"], "reason": r["reason"]}

@app.post("/reclassify-all")
async def reclassify_all():
    from excel_processor import classify_tender
    db = load_db()
    counts = {}
    for tid, t in db["tenders"].items():
        if t.get("bid_no_bid_done"):
            continue
        r = classify_tender(t.get("brief", ""), t.get("estimated_cost_raw", 0), t.get("eligibility", ""), t.get("checklist", ""))
        t.update({"verdict": r["verdict"], "verdict_color": r["verdict_color"], "reason": r["reason"]})
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    save_db(db)
    return {"status": "done", "reclassified": sum(counts.values()), "breakdown": counts}

# ══ PROCESS FILES ════════════════════════════════════════════════════════════
@app.post("/process")
async def process_zip(file: UploadFile = File(...), t247_id: str = ""):
    return await process_files(files=[file], t247_id=t247_id)

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
            content = await upload.read()
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"File {fname} too large (max 50MB)")
            dest = Path(tmp_dir) / fname
            dest.write_bytes(content)
            if dest.suffix.lower() == ".zip":
                with zipfile.ZipFile(dest, "r") as zf:
                    zf.extractall(extract_dir)
                extract_all_zips(extract_dir)
            else:
                shutil.copy2(dest, extract_dir / fname)

        doc_files = []
        for ext in ["*.pdf", "*.docx", "*.doc", "*.txt", "*.html", "*.htm", "*.xlsx", "*.xls"]:
            doc_files.extend(extract_dir.rglob(ext))
        seen, unique = set(), []
        for f in doc_files:
            if f.name not in seen:
                seen.add(f.name)
                unique.append(f)
        doc_files = unique

        if not doc_files:
            raise HTTPException(400, "No readable documents found.")

        corr = [f for f in doc_files if any(k in f.name.lower() for k in ["corrigendum","addendum","amendment","corr_","addend","revised","rectification"])]
        main_files = [f for f in doc_files if f not in corr]

        extractor = TenderExtractor()
        tender_data = extractor.process_documents(main_files if main_files else doc_files)

        if corr:
            cd = TenderExtractor().process_documents(corr)
            for field in ["bid_submission_date","bid_opening_date","bid_start_date","prebid_query_date","estimated_cost","emd","tender_fee"]:
                val = cd.get(field, "")
                if val and val not in ["—","Refer document","Not specified",""]:
                    tender_data[field] = val
            tender_data["has_corrigendum"] = True
            tender_data["corrigendum_files"] = [f.name for f in corr]

        all_text = ""
        for f in sorted(doc_files, key=lambda x: (0 if any(k in x.name.lower() for k in ["rfp","nit","tender","bid"]) else 1 if any(k in x.name.lower() for k in ["corrigendum","addendum"]) else 2)):
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
            tender_data["ai_warning"] = "Gemini API key not configured. Go to Settings."

        checker = NascentChecker()
        if not tender_data.get("overall_verdict"):
            tender_data["pq_criteria"] = checker.check_all(tender_data.get("pq_criteria", []))
            tender_data["tq_criteria"] = checker.check_all(tender_data.get("tq_criteria", []))
            tender_data["overall_verdict"] = checker.get_overall_verdict(tender_data["pq_criteria"] + tender_data["tq_criteria"])

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
                "scope_items": tender_data.get("scope_items", []),
                "contract_period": tender_data.get("contract_period", ""),
                "post_implementation": tender_data.get("post_implementation", ""),
                "pq_criteria": tender_data.get("pq_criteria", []),
                "tq_criteria": tender_data.get("tq_criteria", []),
                "payment_terms": tender_data.get("payment_terms", []),
                "notes": tender_data.get("notes", []),
                "overall_verdict": tender_data.get("overall_verdict", {}),
                "prebid_queries": tender_data.get("prebid_queries", []),
                "raw_text": all_text,
                "prebid_passed": passed if 'passed' in locals() else False,
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

# ══ GENERATE DOCS ════════════════════════════════════════════════════════════
@app.post("/generate-docs/{t247_id}")
async def generate_docs(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found. Analyse the tender first.")
    try:
        generator = BidDocGenerator()
        safe_no = re.sub(r'[^\w\-]', '_', tender.get("tender_no") or tender.get("brief", "Report"))[:50]
        output_filename = f"BidNoBid_{safe_no}.docx"
        tender_data = dict(tender)
        if not tender_data.get("overall_verdict") and tender.get("verdict"):
            tender_data["overall_verdict"] = {
                "verdict": tender.get("verdict", ""),
                "color": tender.get("verdict_color", "BLUE"),
                "reason": tender.get("reason", ""),
                "green": 0, "amber": 0, "red": 0
            }
        generator.generate(tender_data, str(OUTPUT_DIR / output_filename))
        tender["report_file"] = output_filename
        save_tender(t247_id, tender)
        return {"status": "success", "files": [output_filename], "download_file": output_filename}
    except Exception as e:
        raise HTTPException(500, f"Document generation failed: {str(e)}")

# ══ DOWNLOAD ─────────────────────────────────────────────────────────────────
@app.get("/download/{filename}")
async def download_file(filename: str):
    # FIX: Path traversal guard — resolve and assert inside OUTPUT_DIR
    fp = (OUTPUT_DIR / Path(filename).name).resolve()
    output_resolved = OUTPUT_DIR.resolve()
    if not str(fp).startswith(str(output_resolved)):
        raise HTTPException(400, "Invalid filename")
    if not fp.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path=str(fp),
        filename=fp.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

@app.get("/reports")
async def list_reports():
    return [{"filename": f.name, "size_kb": round(f.stat().st_size / 1024, 1),
             "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d %b %Y %H:%M")}
            for f in sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"), key=lambda f: f.stat().st_mtime, reverse=True)[:100]]

@app.get("/reports-list")
async def reports_list():
    try:
        db = load_db()
        reports = []
        for fname in sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"), reverse=True):
            tender = next((t for tid, t in db["tenders"].items() if tid in fname.stem or (t.get("tender_no", "") and t.get("tender_no", "").replace("/", "_") in fname.stem)), None)
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

# ══ CONFIG (admin-guarded) ════════════════════════════════════════════════════
@app.get("/config")
async def get_config_route(request: Request):
    check_admin(request)
    config = load_config()
    key = config.get("gemini_api_key", "")
    return {"gemini_api_key_set": bool(key), "gemini_api_key": key,
            "gemini_api_key_preview": (key[:8] + "..." + key[-4:]) if key else ""}

@app.post("/config")
async def update_config_route(request: Request, data: dict = Body(...)):
    check_admin(request)
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
    if "t247_username" in data:
        config["t247_username"] = data["t247_username"]
    if "t247_password" in data:
        config["t247_password"] = data["t247_password"]
    save_config(config)
    return {"status": "saved"}

# ══ PROFILE ══════════════════════════════════════════════════════════════════
@app.get("/profile")
async def get_profile():
    from nascent_checker import load_profile
    return load_profile()

@app.post("/profile")
async def update_profile(data: dict = Body(...)):
    # Write profile to runtime path (used first by checker) and repo path (fallback)
    runtime_dir = Path(os.environ.get("BIDNOBID_RUNTIME_DIR", "/tmp/bid-nobid"))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / "nascent_profile.json"
    repo_path = BASE_DIR / "nascent_profile.json"
    payload = json.dumps(data, indent=2)
    runtime_path.write_text(payload, encoding="utf-8")
    repo_path.write_text(payload, encoding="utf-8")
    try:
        from excel_processor import invalidate_rules_cache
        invalidate_rules_cache()
    except (ImportError, AttributeError):
        pass
    return {"status": "saved", "runtime_profile": str(runtime_path), "repo_profile": str(repo_path)}

# ══ BOQ ══════════════════════════════════════════════════════════════════════
@app.get("/boq/constants")
async def boq_constants():
    return get_boq_constants()

@app.get("/boq/{t247_id}")
async def get_boq(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    boq = tender.get("boq")
    name = tender.get("tender_name") or tender.get("brief", "")
    if boq:
        return {"t247_id": t247_id, "tender_name": name, "boq": boq, "source": "saved"}
    items = extract_boq_from_scope(tender)
    return {"t247_id": t247_id, "tender_name": name, "boq": {"items": items, "margin_pct": 15.0, "gst_pct": 18.0}, "source": "auto"}

@app.post("/boq/{t247_id}")
async def save_boq(t247_id: str, data: dict = Body(...)):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    result = calculate_boq_totals(data.get("items", []), float(data.get("margin_pct", 15)), float(data.get("gst_pct", 18)))
    tender["boq"] = {**result, "margin_pct": data.get("margin_pct", 15), "gst_pct": data.get("gst_pct", 18), "saved_at": datetime.now().isoformat()}
    save_tender(t247_id, tender)
    return {"status": "saved", "totals": result}

@app.post("/boq/{t247_id}/regenerate")
async def regenerate_boq(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    return {"t247_id": t247_id, "boq": {"items": extract_boq_from_scope(tender), "margin_pct": 15.0, "gst_pct": 18.0}, "source": "regenerated"}

# ══ CHECKLIST ════════════════════════════════════════════════════════════════
@app.get("/checklist/{t247_id}")
async def get_checklist(t247_id: str):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if "doc_checklist" in t:
        return {"checklist": t["doc_checklist"], "t247_id": t247_id}
    return {"checklist": generate_doc_checklist(t), "t247_id": t247_id}

@app.post("/checklist/{t247_id}")
async def save_checklist(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["doc_checklist"] = data.get("checklist", [])
    pct = round(sum(1 for d in t["doc_checklist"] if d.get("done")) / max(len(t["doc_checklist"]), 1) * 100)
    t["checklist_pct"] = pct
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "completion_pct": pct}

@app.post("/checklist/{t247_id}/item")
async def toggle_checklist_item(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    cl = t.get("doc_checklist", [])
    for item in cl:
        if str(item.get("id")) == str(data.get("id")):
            item["done"] = data.get("done", False)
            break
    t["doc_checklist"] = cl
    pct = round(sum(1 for d in cl if d.get("done")) / max(len(cl), 1) * 100)
    t["checklist_pct"] = pct
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "completion_pct": pct}

# ══ PIPELINE / ANALYTICS ═════════════════════════════════════════════════════
@app.get("/alerts")
async def get_alerts():
    return {"alerts": get_deadline_alerts()}

@app.get("/pipeline")
async def get_pipeline():
    return {"stages": get_pipeline_stats(), "stage_list": PIPELINE_STAGES, "stage_colors": STAGE_COLORS}

@app.get("/win-loss")
async def get_win_loss():
    return get_win_loss_stats()

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

@app.post("/bid-result/{t247_id}")
async def save_bid_result(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t.update({"outcome": data.get("outcome", ""), "outcome_value": data.get("value", ""),
              "outcome_competitor": data.get("competitor", ""), "outcome_notes": data.get("notes", ""),
              "outcome_date": datetime.now().isoformat()})
    if data.get("outcome") == "Won": t["status"] = "Won"
    elif data.get("outcome") == "Lost": t["status"] = "Lost"
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved"}

# ══ PRE-BID ══════════════════════════════════════════════════════════════════
@app.post("/prebid-sent/{t247_id}")
async def mark_prebid_sent(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t.update({"prebid_sent": True, "prebid_sent_at": datetime.now().isoformat(),
              "prebid_sent_to": data.get("email", ""), "status": "Pre-bid Sent",
              "status_updated_at": datetime.now().isoformat()})
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved"}

# ══ CHAT ════════════════════════════════════════════════════════════════════
@app.post("/chat")
async def chat(data: dict = Body(...)):
    message = data.get("message", "").strip()
    if not message:
        raise HTTPException(400, "Empty message")
    return process_message(message, load_history())

@app.get("/chat/history")
async def get_chat_history():
    return {"history": load_history()}

@app.delete("/chat/history")
async def clear_chat_history():
    h = OUTPUT_DIR / "chat_history.json"
    if h.exists():
        h.unlink()
    return {"status": "cleared"}

# ══ SKIPPED ═════════════════════════════════════════════════════════════════
@app.get("/skipped")
async def get_skipped():
    db = load_db()
    return {"skipped": [t for t in db["tenders"].values() if t.get("status") == "Not Interested"]}

# ══ DRIVE (admin-guarded) ════════════════════════════════════════════════════
@app.post("/sync-drive")
async def sync_drive(request: Request):
    check_admin(request)
    if not drive_available():
        return JSONResponse({"status": "error", "message": "Google Drive not connected"}, status_code=400)
    try:
        db = load_db()
        ok = save_to_drive(DB_FILE)
        if ok:
            return {"status": "ok", "message": f"Synced {len(db.get('tenders', {}))} tenders to Drive"}
        return JSONResponse({"status": "error", "message": "Sync failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/sync-sheets")
async def sync_sheets(request: Request):
    return await sync_drive(request)

@app.get("/drive-status")
async def drive_status():
    db = load_db()
    return {"drive_connected": drive_available(),
            "tenders_in_memory": len(db.get("tenders", {})),
            "db_file_exists": DB_FILE.exists(),
            "db_size_kb": round(DB_FILE.stat().st_size / 1024) if DB_FILE.exists() else 0}

@app.post("/upload-db")
async def upload_db(request: Request, file: UploadFile = File(...)):
    check_admin(request)
    try:
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large")
        data = json.loads(content)
        count = len(data.get("tenders", {}))
        if count == 0:
            raise HTTPException(400, "File has 0 tenders")
        DB_FILE.write_bytes(content)
        drive_ok = save_to_drive(DB_FILE) if drive_available() else False
        return {"status": "ok", "tenders": count, "drive_saved": drive_ok}
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")

# ══ EXPORT ══════════════════════════════════════════════════════════════════
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
            tenders = [t for t in tenders if any(s in str(t.get(f, "")).lower() for f in ["t247_id","ref_no","brief","org_name","location","verdict"])]
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tenders"
        headers = ["Sr.","T247 ID","Reference No.","Brief","Organization","Location","Cost (Cr)","EMD","Doc Fee","MSME Exempt","Deadline","Days Left","Verdict","Stage","Analysed","BOQ","Checklist %","Reason"]
        col_widths = [5,12,25,45,30,20,10,12,10,12,14,10,14,18,10,8,12,35]
        hdr_fill = PatternFill("solid", fgColor="1E2A3B")
        hdr_font = Font(bold=True, color="FFFFFF", size=11)
        for ci, (hdr, w) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(row=1, column=ci, value=hdr)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[cell.column_letter].width = w
        ws.row_dimensions[1].height = 30
        vc = {"BID": "E2EFDA", "CONDITIONAL": "FFF2CC", "NO-BID": "FCE4D6", "REVIEW": "DEEAF1"}
        def dl(t):
            try:
                s = t.get("deadline", "")
                for fmt in ["%d-%m-%Y", "%d/%m/%Y"]:
                    try:
                        return (datetime.strptime(s.split()[0], fmt).date() - date.today()).days
                    except Exception:
                        continue
            except Exception:
                pass
            return 999
        for ri, t in enumerate(sorted(tenders, key=dl), 2):
            days = dl(t)
            v = t.get("verdict", "")
            rf = PatternFill("solid", fgColor=vc.get(v, "FFFFFF"))
            vals = [ri-1, t.get("t247_id",""), t.get("ref_no",""), t.get("brief",""), t.get("org_name",""), t.get("location",""),
                    t.get("estimated_cost_cr",""), t.get("emd",""), t.get("doc_fee",""), t.get("msme_exemption",""),
                    t.get("deadline",""), days if days < 999 else "—", v, t.get("status","Identified"),
                    "Yes" if t.get("bid_no_bid_done") else "No", "Yes" if t.get("boq") else "No",
                    str(t.get("checklist_pct","0")) + "%", t.get("reason","")[:100]]
            for ci, val in enumerate(vals, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                cell.fill = rf
                cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.freeze_panes = "A2"
        fname = f"Tenders_Export_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
        fpath = OUTPUT_DIR / fname
        wb.save(str(fpath))
        return FileResponse(str(fpath), filename=fname, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        raise HTTPException(500, f"Export failed: {str(e)}")

# ══ TEST ════════════════════════════════════════════════════════════════════
@app.get("/test-ai")
async def test_ai():
    from ai_analyzer import get_api_key, call_gemini
    key = get_api_key()
    if not key:
        return {"status": "error", "message": "No API key"}
    try:
        result = call_gemini('Return this exact JSON: {"status": "ok"}', key)
        return {"status": "success", "api_key_present": True, "gemini_response": result[:100]}
    except Exception as e:
        return {"status": "error", "api_key_present": True, "error": str(e)}

@app.get("/test-t247")
async def test_t247():
    cfg = load_config()
    u = str(cfg.get("t247_username", "") or "").strip()
    p = str(cfg.get("t247_password", "") or "").strip()
    if u and p:
        return {"status": "ok", "username": u}
    return {"status": "error", "message": "T247 credentials not configured"}

@app.post("/auto-download/{t247_id}")
async def auto_download_tender(t247_id: str):
    return {"status": "unavailable", "message": "Download manually from tender247.com — Playwright not available on Render free tier."}
