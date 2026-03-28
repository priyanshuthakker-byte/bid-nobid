"""
Bid/No-Bid Automation v7 - Complete Fixed System
FastAPI backend - ALL bugs fixed in this version
"""
import zipfile, tempfile, shutil, json, re, os
from pathlib import Path
from datetime import datetime, date
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from typing import List
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Safe imports with fallbacks so server starts even if a module has errors
try:
    from extractor import TenderExtractor, read_document
except Exception as _e:
    print(f"⚠ extractor import failed: {_e}")
    class TenderExtractor:
        def process_documents(self, files): return {}
    def read_document(f): return ""

try:
    from doc_generator import BidDocGenerator
except Exception as _e:
    print(f"⚠ doc_generator import failed: {_e}")
    class BidDocGenerator:
        def generate(self, data, path): pass

try:
    from nascent_checker import NascentChecker, load_profile as _load_profile
except Exception as _e:
    print(f"⚠ nascent_checker import failed: {_e}")
    class NascentChecker:
        def check_all(self, items): return items
        def get_overall_verdict(self, items): return {"verdict": "REVIEW", "color": "BLUE"}
    def _load_profile(): return {}

try:
    from ai_analyzer import analyze_with_gemini, merge_results, load_config, save_config, get_all_api_keys
except Exception as _e:
    print(f"⚠ ai_analyzer import failed: {_e}")
    def analyze_with_gemini(text, prebid=False): return {"error": "ai_analyzer not loaded"}
    def merge_results(a, b, c=False): return a
    def load_config(): return {}
    def save_config(c): pass
    def get_all_api_keys(): return []

try:
    from excel_processor import process_excel
except Exception as _e:
    print(f"⚠ excel_processor import failed: {_e}")
    def process_excel(path): return []

try:
    from prebid_generator import generate_prebid_queries
except Exception as _e:
    print(f"⚠ prebid_generator import failed: {_e}")
    def generate_prebid_queries(data): return []

try:
    from chatbot import process_message, load_history
except Exception as _e:
    print(f"⚠ chatbot import failed: {_e}")
    def process_message(msg, history): return {"response": "Chatbot not available"}
    def load_history(): return []

try:
    from gdrive_sync import init_drive, save_to_drive, load_from_drive, is_available as drive_available
except Exception as _e:
    print(f"⚠ gdrive_sync import failed: {_e}")
    def init_drive(): return False
    def save_to_drive(f): return False
    def load_from_drive(f): return False
    def drive_available(): return False

try:
    from tracker import get_deadline_alerts, get_pipeline_stats, get_win_loss_stats, generate_doc_checklist, PIPELINE_STAGES, STAGE_COLORS
except Exception as _e:
    print(f"⚠ tracker import failed: {_e}")
    def get_deadline_alerts(): return []
    def get_pipeline_stats(): return {}
    def get_win_loss_stats(): return {}
    def generate_doc_checklist(t): return []
    PIPELINE_STAGES = ["Identified", "To Analyse", "Analysed", "Pre-bid Sent", "Bid Decided", "Preparing", "Submitted", "Won", "Lost"]
    STAGE_COLORS = {}

app = FastAPI(title="Bid/No-Bid System v7", version="7.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
VAULT_DIR = BASE_DIR / "data" / "vault"
DB_FILE = OUTPUT_DIR / "tenders_db.json"

for d in [OUTPUT_DIR, TEMP_DIR, VAULT_DIR]:
    d.mkdir(exist_ok=True, parents=True)

# ── STARTUP ────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    import asyncio, time
    print("🚀 Starting Bid/No-Bid System v7...")
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    TEMP_DIR.mkdir(exist_ok=True, parents=True)
    VAULT_DIR.mkdir(exist_ok=True, parents=True)

    drive_ok = init_drive()
    print(f"📁 Google Drive: {'✅ Connected' if drive_ok else '❌ Not configured'}")

    # Restore vault files from Drive (survive Render redeploys)
    if drive_ok:
        try:
            for doc in VAULT_DOCS_LIST:
                for ext in [".pdf", ".docx", ".png", ".jpg", ".jpeg"]:
                    remote_name = f"vault_{doc['id']}{ext}"
                    local_dest  = VAULT_DIR / f"{doc['id']}{ext}"
                    if not local_dest.exists():
                        try:
                            load_from_drive(local_dest, filename=remote_name)
                        except Exception:
                            pass
            print("✅ Vault restore attempted from Drive")
        except Exception as e:
            print(f"⚠️ Vault restore skipped: {e}")

    if drive_ok:
        for attempt in range(3):
            try:
                success = load_from_drive(DB_FILE)
                if success:
                    db = load_db()
                    print(f"✅ Loaded {len(db.get('tenders',{}))} tenders from Drive")
                    break
                time.sleep(2)
            except Exception as e:
                print(f"⚠ Drive load attempt {attempt+1} failed: {e}")
                time.sleep(2)
    elif DB_FILE.exists():
        db = load_db()
        print(f"✅ Using local DB: {len(db.get('tenders',{}))} tenders")
    else:
        print("⚠ No DB found — fresh start")

    print("✅ Server ready")

# ── DB HELPERS ─────────────────────────────────────────────────
def load_db() -> dict:
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tenders": {}}

def save_db(db: dict):
    # Always ensure the data directory exists first
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    DB_FILE.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")
    try:
        save_to_drive(DB_FILE)
    except Exception as e:
        print(f"⚠️ Drive sync warning: {e}")

def get_tender(t247_id: str) -> dict:
    return load_db()["tenders"].get(str(t247_id), {})

def save_tender(t247_id: str, data: dict):
    db = load_db()
    db["tenders"][str(t247_id)] = data
    save_db(db)

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

# ══════════════════════════════════════════════════════════════
# STATIC PAGES
# ══════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
async def root():
    index = BASE_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Bid/No-Bid System v7</h1><p>index.html not found</p>")

@app.head("/")
async def head_root():
    return {}

@app.head("/health")
async def head_health():
    return {}

@app.get("/health")
async def health():
    config = load_config()
    db = load_db()
    all_keys = get_all_api_keys()
    return {
        "status": "ok",
        "version": "7.0",
        "ai_configured": bool(all_keys),
        "ai_keys_count": len(all_keys),
        "drive_sync": drive_available(),
        "tenders_loaded": len(db.get("tenders", {})),
    }

# ══════════════════════════════════════════════════════════════
# CONFIG — FIXED: saves all 4 keys + groq + T247 credentials
# ══════════════════════════════════════════════════════════════
@app.get("/config")
async def get_config_route():
    config = load_config()
    primary = config.get("gemini_api_key", "")
    all_keys = config.get("gemini_api_keys", [])
    if primary and primary not in all_keys:
        all_keys = [primary] + all_keys
    all_keys = [k for k in all_keys if k and len(k.strip()) > 10]
    return {
        "gemini_api_key_set": bool(all_keys),
        "gemini_api_key": primary,
        "gemini_keys_count": len(all_keys),
        "gemini_api_key_2": all_keys[1] if len(all_keys) > 1 else "",
        "gemini_api_key_3": all_keys[2] if len(all_keys) > 2 else "",
        "gemini_api_key_4": all_keys[3] if len(all_keys) > 3 else "",
        "t247_username": config.get("t247_username", ""),
    }

@app.get("/config-full")
async def get_config_full():
    config = load_config()
    all_keys = config.get("gemini_api_keys", [])
    primary = config.get("gemini_api_key", "")
    if primary and primary not in all_keys:
        all_keys = [primary] + all_keys
    all_keys = [k for k in all_keys if k and len(k.strip()) > 10]
    masked = []
    for k in all_keys:
        if len(k) > 12:
            masked.append(k[:8] + "..." + k[-4:])
        else:
            masked.append(k[:4] + "...")
    return {
        "gemini_api_keys": masked,
        "total_keys": len(all_keys),
        "ai_active": bool(all_keys),
        "groq_configured": bool(config.get("groq_api_key")),
    }

@app.post("/config")
async def update_config_route(data: dict = Body(...)):
    config = load_config()

    # Save all 4 Gemini keys (new array format from settings UI)
    if "gemini_api_keys" in data:
        keys = [k.strip() for k in data["gemini_api_keys"] if k and k.strip() and len(k.strip()) > 10]
        if keys:
            config["gemini_api_keys"] = keys
            config["gemini_api_key"] = keys[0]

    # Also accept single key (old format / backwards compat)
    if "gemini_api_key" in data and data["gemini_api_key"]:
        k = data["gemini_api_key"].strip()
        if k and len(k) > 10:
            config["gemini_api_key"] = k
            existing = config.get("gemini_api_keys", [])
            if k not in existing:
                config["gemini_api_keys"] = [k] + existing

    # Groq key
    if "groq_api_key" in data and data["groq_api_key"]:
        config["groq_api_key"] = data["groq_api_key"].strip()

    # T247 credentials
    if "t247_username" in data and data["t247_username"]:
        config["t247_username"] = data["t247_username"].strip()
    if "t247_password" in data and data["t247_password"]:
        config["t247_password"] = data["t247_password"]

    save_config(config)
    all_keys = config.get("gemini_api_keys", [])
    return {
        "status": "saved",
        "gemini_keys_saved": len(all_keys),
        "groq_saved": bool(config.get("groq_api_key")),
    }

# ══════════════════════════════════════════════════════════════
# TEST AI — returns clear error with exact model name that failed
# ══════════════════════════════════════════════════════════════
@app.get("/test-ai")
async def test_ai():
    from ai_analyzer import get_all_api_keys, call_gemini
    keys = get_all_api_keys()
    if not keys:
        return {
            "status": "error",
            "message": "No API key configured. Go to Settings and add your Gemini key from aistudio.google.com/apikey"
        }
    try:
        result = call_gemini('Return exactly this JSON and nothing else: {"status": "ok", "message": "AI working"}', keys[0])
        return {
            "status": "success",
            "api_key_present": True,
            "gemini_response": result[:100]
        }
    except Exception as e:
        return {
            "status": "error",
            "api_key_present": True,
            "error": str(e)
        }

@app.get("/test-groq")
async def test_groq():
    config = load_config()
    groq_key = config.get("groq_api_key", "")
    if not groq_key:
        return {"status": "missing", "message": "No Groq key configured. Get free key at console.groq.com"}
    try:
        from ai_analyzer import call_groq
        result = call_groq('Say "OK" and nothing else', groq_key)
        return {"status": "success", "response": result[:50]}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/api-quota-status")
async def api_quota_status():
    keys = get_all_api_keys()
    config = load_config()
    return {
        "status": "ok" if keys else "no_keys",
        "gemini_keys": len(keys),
        "groq_configured": bool(config.get("groq_api_key")),
        "total_keys": len(keys) + (1 if config.get("groq_api_key") else 0),
    }

# ══════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════
@app.get("/dashboard")
async def dashboard():
    db = load_db()
    tenders = list(db["tenders"].values())
    stats = {
        "total": len(tenders),
        "bid": sum(1 for t in tenders if t.get("verdict") == "BID"),
        "no_bid": sum(1 for t in tenders if t.get("verdict") == "NO-BID"),
        "conditional": sum(1 for t in tenders if t.get("verdict") == "CONDITIONAL"),
        "review": sum(1 for t in tenders if t.get("verdict") in ("REVIEW", "", None)),
        "analysed": sum(1 for t in tenders if t.get("bid_no_bid_done")),
        "deadline_today": sum(1 for t in tenders if days_left(t.get("deadline", "")) == 0),
        "deadline_3days": sum(1 for t in tenders if 0 < days_left(t.get("deadline", "")) <= 3),
    }
    tenders_sorted = sorted(tenders, key=lambda t: days_left(t.get("deadline", "999")))
    return {"stats": stats, "tenders": tenders_sorted}

# ══════════════════════════════════════════════════════════════
# EXCEL IMPORT
# ══════════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════════
# PROCESS FILES — AI ANALYSIS
# ══════════════════════════════════════════════════════════════
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
            raise HTTPException(400, "No readable documents found. Upload ZIP, PDF, or DOCX.")

        corrigendum_files = [f for f in doc_files if any(
            k in f.name.lower() for k in ["corrigendum", "addendum", "amendment", "corr_", "revised"])]
        main_files = [f for f in doc_files if f not in corrigendum_files]

        extractor = TenderExtractor()
        tender_data = extractor.process_documents(main_files if main_files else doc_files)

        if corrigendum_files:
            corr_data = TenderExtractor().process_documents(corrigendum_files)
            for field in ["bid_submission_date", "bid_opening_date", "prebid_query_date", "estimated_cost", "emd", "tender_fee"]:
                val = corr_data.get(field, "")
                if val and val not in ["—", "Refer document", "Not specified", ""]:
                    tender_data[field] = val
            tender_data["has_corrigendum"] = True
            tender_data["corrigendum_files"] = [f.name for f in corrigendum_files]

        # Build full text for AI
        all_text = ""
        for f in sorted(doc_files, key=lambda x: (
            0 if any(k in x.name.lower() for k in ["rfp", "nit", "tender", "bid"]) else
            1 if any(k in x.name.lower() for k in ["corrigendum", "addendum"]) else 2
        )):
            t = read_document(f)
            if t and t.strip():
                all_text += f"\n\n=== FILE: {f.name} ===\n{t}"

        # AI Analysis
        ai_used = False
        all_keys = get_all_api_keys()
        print(f"[AI] Keys available: {len(all_keys)} | Text length: {len(all_text)} chars")

        if all_keys and all_text.strip():
            passed = prebid_passed(tender_data.get("prebid_query_date", ""))
            ai_result = analyze_with_gemini(all_text, passed)
            print(f"[AI] Result: {'SUCCESS' if 'error' not in ai_result else 'ERROR: '+ai_result.get('error','')[:80]}")
            if "error" not in ai_result:
                tender_data = merge_results(tender_data, ai_result, passed)
                ai_used = True
            else:
                tender_data["ai_warning"] = ai_result.get("error", "")
        elif not all_keys:
            tender_data["ai_warning"] = "No Gemini API key configured. Go to Settings to add key from aistudio.google.com/apikey"

        # Nascent checker fallback
        checker = NascentChecker()
        if not tender_data.get("overall_verdict"):
            tender_data["pq_criteria"] = checker.check_all(tender_data.get("pq_criteria", []))
            tender_data["tq_criteria"] = checker.check_all(tender_data.get("tq_criteria", []))
            tender_data["overall_verdict"] = checker.get_overall_verdict(
                tender_data["pq_criteria"] + tender_data["tq_criteria"])

        # Set top-level verdict field for dashboard
        if tender_data.get("overall_verdict"):
            v = tender_data["overall_verdict"]
            verdict_str = v.get("verdict", "REVIEW")
            # Normalize: remove " RECOMMENDED" suffix
            verdict_str = verdict_str.replace(" RECOMMENDED", "").replace("BID RECOMMENDED", "BID")
            tender_data["verdict"] = verdict_str
            tender_data["reason"] = v.get("reason", "")

        # Generate Word report
        generator = BidDocGenerator()
        safe_no = re.sub(r'[^\w\-]', '_', tender_data.get("tender_no", t247_id or "Report"))[:50]
        output_filename = f"BidNoBid_{safe_no}.docx"
        try:
            generator.generate(tender_data, str(OUTPUT_DIR / output_filename))
        except Exception as gen_err:
            print(f"⚠ Report generation error: {gen_err}")
            output_filename = None

        # Save to DB
        if t247_id:
            db_record = get_tender(t247_id)
            ov = tender_data.get("overall_verdict", {})
            db_record.update({
                "t247_id": t247_id,
                "tender_no": tender_data.get("tender_no"),
                "org_name": tender_data.get("org_name"),
                "tender_name": tender_data.get("tender_name"),
                "bid_submission_date": tender_data.get("bid_submission_date"),
                "emd": tender_data.get("emd"),
                "estimated_cost": tender_data.get("estimated_cost"),
                "verdict": tender_data.get("verdict", ov.get("verdict", "REVIEW")),
                "reason": tender_data.get("reason", ov.get("reason", "")),
                "bid_no_bid_done": True,
                "report_file": output_filename,
                "analysed_at": datetime.now().isoformat(),
                "has_corrigendum": tender_data.get("has_corrigendum", False),
                "ai_used": ai_used,
                "pq_criteria": tender_data.get("pq_criteria", []),
                "tq_criteria": tender_data.get("tq_criteria", []),
                "prebid_queries": tender_data.get("prebid_queries", []),
                "overall_verdict": tender_data.get("overall_verdict"),
                "scope_items": tender_data.get("scope_items", []),
                "payment_terms": tender_data.get("payment_terms", []),
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

# ══════════════════════════════════════════════════════════════
# TENDER CRUD
# ══════════════════════════════════════════════════════════════
@app.get("/tender/{t247_id}")
async def get_tender_detail(t247_id: str):
    t = get_tender(t247_id)
    if not t:
        raise HTTPException(404, f"Tender {t247_id} not found")
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

@app.post("/tender/{t247_id}/skip")
async def skip_tender(t247_id: str, data: dict = Body(default={})):
    db = load_db()
    t = db["tenders"].get(t247_id, {"t247_id": t247_id})
    t["status"] = "Not Interested"
    t["skip_reason"] = data.get("reason", "Not interested")
    t["skipped_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "skipped", "t247_id": t247_id}

@app.post("/tender/{t247_id}/restore")
async def restore_tender(t247_id: str):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    if t.get("status") == "Not Interested":
        t["status"] = "Identified"
        t.pop("skip_reason", None)
        t.pop("skipped_at", None)
        db["tenders"][t247_id] = t
        save_db(db)
    return {"status": "restored"}

@app.post("/tender/{t247_id}/favourite")
async def toggle_favourite(t247_id: str):
    db = load_db()
    t = db["tenders"].get(t247_id, {"t247_id": t247_id})
    t["favourite"] = not t.get("favourite", False)
    db["tenders"][t247_id] = t
    save_db(db)
    return {"favourite": t["favourite"]}

# ══════════════════════════════════════════════════════════════
# PREBID QUERIES
# ══════════════════════════════════════════════════════════════
@app.post("/prebid-queries")
async def get_prebid_queries(data: dict = Body(...)):
    queries = generate_prebid_queries(data)
    return {"queries": queries}

@app.get("/prebid-queries/{t247_id}")
async def get_saved_prebid_queries(t247_id: str):
    tender = get_tender(t247_id)
    return {"queries": tender.get("prebid_queries", [])}

@app.post("/tender/{t247_id}/generate-prebid-letter")
async def gen_prebid_letter(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    queries = tender.get("prebid_queries", [])
    if not queries:
        raise HTTPException(400, "No pre-bid queries found. Analyse tender first.")
    try:
        from docx import Document
        doc = Document()
        doc.add_heading("Pre-Bid Queries", 0)
        doc.add_paragraph(f"Tender: {tender.get('tender_name', 'N/A')}")
        doc.add_paragraph(f"Tender No: {tender.get('tender_no', 'N/A')}")
        doc.add_paragraph(f"Organization: {tender.get('org_name', 'N/A')}")
        doc.add_paragraph("")
        doc.add_paragraph("Respected Sir/Madam,")
        doc.add_paragraph(
            "We, Nascent Info Technologies Pvt. Ltd., wish to participate in the above tender. "
            "We request clarifications on the following points:"
        )
        doc.add_paragraph("")
        for i, q in enumerate(queries, 1):
            clause = q.get("clause", "") if isinstance(q, dict) else ""
            query_text = q.get("query", q) if isinstance(q, dict) else str(q)
            doc.add_paragraph(f"Q{i}. {clause}: {query_text}")
        doc.add_paragraph("")
        doc.add_paragraph("Thanking you,")
        doc.add_paragraph("Nascent Info Technologies Pvt. Ltd.")
        safe_no = re.sub(r'[^\w\-]', '_', tender.get("tender_no", t247_id))[:40]
        filename = f"PreBid_{safe_no}.docx"
        doc.save(str(OUTPUT_DIR / filename))
        return {"status": "success", "filename": filename, "query_count": len(queries), "download_url": f"/download/{filename}"}
    except Exception as e:
        raise HTTPException(500, f"Letter generation failed: {str(e)}")

# ══════════════════════════════════════════════════════════════
# CHECKLIST
# ══════════════════════════════════════════════════════════════
@app.get("/checklist/{t247_id}")
async def get_checklist(t247_id: str):
    db  = load_db()
    t   = db["tenders"].get(t247_id, {})

    # 1. AI-generated checklist (most accurate — from analysed ZIP)
    if t.get("doc_checklist"):
        return {"checklist": t["doc_checklist"], "t247_id": t247_id,
                "source": "ai_analysis"}

    # 2. Excel checklist column (from T247 import — available without ZIP)
    excel_cl = t.get("checklist", "").strip()
    if excel_cl:
        items = _parse_excel_checklist(excel_cl)
        if items:
            return {"checklist": items, "t247_id": t247_id,
                    "source": "excel_import"}

    # 3. Auto-generated from tender profile
    checklist = generate_doc_checklist(t)
    return {"checklist": checklist, "t247_id": t247_id, "source": "auto_generated"}


def _parse_excel_checklist(text: str) -> list:
    """Parse T247 Excel checklist column into structured items."""
    import re as _re
    items = []
    sr = 1
    # Split on bullet points, numbers, or newlines
    lines = _re.split(r"[\n]+", text)
    for line in lines:
        line = line.strip().lstrip("0123456789.").strip("-").strip()
        if len(line) < 5:
            continue
        items.append({
            "id":          f"excel_{sr}",
            "sr_no":       str(sr),
            "label":       line[:200],
            "description": "",
            "source":      "excel",
            "mandatory":   True,
            "done":        False,
            "generated_by_app": False,
            "responsible": "Bid Team",
        })
        sr += 1
    return items

@app.post("/checklist/{t247_id}")
async def save_checklist(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["doc_checklist"] = data.get("checklist", [])
    pct = round(sum(1 for i in t["doc_checklist"] if i.get("done")) / max(len(t["doc_checklist"]), 1) * 100)
    t["checklist_pct"] = pct
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "completion_pct": pct}

@app.post("/checklist/{t247_id}/item")
async def toggle_checklist_item(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    items = t.get("doc_checklist", [])
    item_id = data.get("id", "")
    done = data.get("done", False)
    for item in items:
        if item.get("id") == item_id or item.get("label") == item_id:
            item["done"] = done
            break
    t["doc_checklist"] = items
    if items:
        t["checklist_pct"] = round(100 * sum(1 for i in items if i.get("done")) / len(items))
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved"}

# ══════════════════════════════════════════════════════════════
# ALERTS & PIPELINE
# ══════════════════════════════════════════════════════════════
@app.get("/alerts")
async def get_alerts():
    return {"alerts": get_deadline_alerts()}

@app.get("/pipeline")
async def get_pipeline():
    return {"stages": get_pipeline_stats(), "stage_list": PIPELINE_STAGES, "stage_colors": STAGE_COLORS}

@app.get("/win-loss")
async def get_win_loss():
    return get_win_loss_stats()

# ══════════════════════════════════════════════════════════════
# REPORTS & DOWNLOAD
# ══════════════════════════════════════════════════════════════
@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = OUTPUT_DIR / Path(filename).name
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    ext = file_path.suffix.lower()
    media_types = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf",
    }
    return FileResponse(str(file_path), filename=file_path.name, media_type=media_types.get(ext, "application/octet-stream"))

@app.get("/reports-list")
async def reports_list():
    try:
        db = load_db()
        reports = []
        for fname in sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"), key=lambda f: f.stat().st_mtime, reverse=True):
            tender = None
            for tid, t in db["tenders"].items():
                if tid in fname.stem or (t.get("tender_no", "") and t.get("tender_no", "").replace("/", "_") in fname.stem):
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
            tenders = [t for t in tenders if any(s in str(t.get(f, "")).lower() for f in ["t247_id", "ref_no", "brief", "org_name"])]

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tenders"
        headers = ["Sr.", "T247 ID", "Ref No.", "Brief", "Organization", "Location",
                   "Cost (Cr)", "EMD", "Doc Fee", "Deadline", "Days Left", "Verdict", "Stage", "Analysed", "Reason"]
        hdr_fill = PatternFill("solid", fgColor="1E2A3B")
        for ci, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=ci, value=h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = hdr_fill
        verdict_colors = {"BID": "E2EFDA", "CONDITIONAL": "FFF2CC", "NO-BID": "FCE4D6", "REVIEW": "DEEAF1"}
        tenders_sorted = sorted(tenders, key=lambda t: days_left(t.get("deadline", "999")))
        for ri, t in enumerate(tenders_sorted, 2):
            dl = days_left(t.get("deadline", ""))
            v = t.get("verdict", "")
            vals = [ri-1, t.get("t247_id",""), t.get("ref_no",""), t.get("brief",""), t.get("org_name",""),
                    t.get("location",""), t.get("estimated_cost_cr",""), t.get("emd",""), t.get("doc_fee",""),
                    t.get("deadline",""), dl if dl < 999 else "—", v, t.get("status","Identified"),
                    "Yes" if t.get("bid_no_bid_done") else "No", t.get("reason","")[:100]]
            row_fill = PatternFill("solid", fgColor=verdict_colors.get(v, "FFFFFF"))
            for ci, val in enumerate(vals, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                cell.fill = row_fill
        ws.freeze_panes = "A2"
        fname = f"Tenders_Export_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
        fpath = OUTPUT_DIR / fname
        wb.save(str(fpath))
        return FileResponse(str(fpath), filename=fname, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        raise HTTPException(500, f"Export failed: {str(e)}")

# ══════════════════════════════════════════════════════════════
# GENERATE SUBMISSION DOCS
# ══════════════════════════════════════════════════════════════
@app.post("/generate-docs/{t247_id}")
async def generate_submission_docs(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found. Analyse it first.")
    try:
        from submission_generator import SubmissionGenerator
        gen = SubmissionGenerator()
        result = gen.generate_all(tender)
        return {"status": "success", "files": result.get("files", []), "zip_url": result.get("zip_url"), "count": len(result.get("files", []))}
    except ImportError:
        # Fallback: generate bid report only
        try:
            generator = BidDocGenerator()
            safe_no = re.sub(r'[^\w\-]', '_', tender.get("tender_no", t247_id))[:50]
            filename = f"BidNoBid_{safe_no}.docx"
            generator.generate(tender, str(OUTPUT_DIR / filename))
            return {"status": "partial", "files": [{"name": "Bid/No-Bid Analysis Report", "filename": filename}], "message": "submission_generator.py not found"}
        except Exception as e2:
            raise HTTPException(500, f"Document generation failed: {str(e2)}")
    except Exception as e:
        raise HTTPException(500, f"Generation error: {str(e)}")

# ══════════════════════════════════════════════════════════════
# NASCENT PROFILE — FIXED: reads from correct path, saves correctly
# ══════════════════════════════════════════════════════════════
PROFILE_PATH = BASE_DIR / "nascent_profile.json"

@app.get("/profile")
async def get_profile():
    """Read nascent_profile.json — returns full profile data"""
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            return data
        except Exception as e:
            raise HTTPException(500, f"Profile read error: {str(e)}")
    # Return empty structure if file doesn't exist
    return {
        "company": {}, "finance": {"turnover_by_year": {}},
        "certifications": {}, "employees": {}, "projects": [],
        "capabilities": {}, "bid_rules": {"do_not_bid": [], "conditional": [], "preferred_sectors": []}
    }

@app.post("/profile")
async def update_profile(data: dict = Body(...)):
    """Save profile — writes to nascent_profile.json"""
    try:
        PROFILE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        # Also sync to Drive if available
        try:
            save_to_drive(PROFILE_PATH)
        except Exception:
            pass
        return {"status": "saved"}
    except Exception as e:
        raise HTTPException(500, f"Profile save error: {str(e)}")

# ══════════════════════════════════════════════════════════════
# BID RULES — read/write bid_rules section of nascent_profile.json
# Syncs both ways: app UI ↔ JSON file ↔ Google Drive
# ══════════════════════════════════════════════════════════════

@app.get("/rules")
async def get_rules():
    """Return bid_rules section of nascent_profile.json"""
    try:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8")) if PROFILE_PATH.exists() else {}
        rules = profile.get("bid_rules", {})
        return {
            "do_not_bid":       rules.get("do_not_bid", []),
            "preferred_sectors": rules.get("preferred_sectors", []),
            "conditional":      rules.get("conditional", []),
            "min_project_value_cr": rules.get("min_project_value_cr", 0.5),
            "max_project_value_cr": rules.get("max_project_value_cr", 150),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/rules")
async def save_rules(data: dict = Body(...)):
    """
    Save bid_rules to nascent_profile.json.
    Merges into existing profile — does not overwrite other sections.
    Syncs to Drive automatically.
    """
    try:
        # Load existing profile
        if PROFILE_PATH.exists():
            profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        else:
            profile = {}

        # Clean and validate incoming rules
        def clean_list(lst):
            return sorted(set(
                kw.lower().strip()
                for kw in (lst or [])
                if kw and kw.strip()
            ))

        profile["bid_rules"] = {
            "do_not_bid":            clean_list(data.get("do_not_bid", [])),
            "preferred_sectors":     clean_list(data.get("preferred_sectors", [])),
            "conditional":           clean_list(data.get("conditional", [])),
            "min_project_value_cr":  float(data.get("min_project_value_cr", 0.5)),
            "max_project_value_cr":  float(data.get("max_project_value_cr", 150)),
            "do_not_bid_remarks":    data.get("do_not_bid_remarks", {}),
        }

        # Write JSON file
        OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
        PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")

        # Sync to Drive
        try:
            save_to_drive(PROFILE_PATH, filename="nascent_profile.json")
        except Exception:
            pass

        return {
            "status": "saved",
            "counts": {
                "do_not_bid":        len(profile["bid_rules"]["do_not_bid"]),
                "preferred_sectors": len(profile["bid_rules"]["preferred_sectors"]),
                "conditional":       len(profile["bid_rules"]["conditional"]),
            }
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/rules/add")
async def add_rule(data: dict = Body(...)):
    """Add a single keyword to a rule list. list_type: do_not_bid | preferred_sectors | conditional"""
    try:
        list_type = data.get("list_type", "")
        keyword   = (data.get("keyword") or "").lower().strip()
        if not keyword or list_type not in ("do_not_bid", "preferred_sectors", "conditional"):
            raise HTTPException(400, "Invalid list_type or empty keyword")

        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8")) if PROFILE_PATH.exists() else {}
        rules   = profile.setdefault("bid_rules", {})
        lst     = rules.setdefault(list_type, [])
        if keyword not in lst:
            lst.append(keyword)
            lst.sort()
        profile["bid_rules"] = rules
        PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        try: save_to_drive(PROFILE_PATH, filename="nascent_profile.json")
        except Exception: pass
        return {"status": "added", "keyword": keyword, "list_type": list_type, "total": len(lst)}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/rules/remove")
async def remove_rule(data: dict = Body(...)):
    """Remove a single keyword from a rule list."""
    try:
        list_type = data.get("list_type", "")
        keyword   = (data.get("keyword") or "").lower().strip()
        if not keyword or list_type not in ("do_not_bid", "preferred_sectors", "conditional"):
            raise HTTPException(400, "Invalid list_type or empty keyword")

        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8")) if PROFILE_PATH.exists() else {}
        rules   = profile.setdefault("bid_rules", {})
        lst     = rules.get(list_type, [])
        if keyword in lst:
            lst.remove(keyword)
        profile["bid_rules"] = rules
        PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        try: save_to_drive(PROFILE_PATH, filename="nascent_profile.json")
        except Exception: pass
        return {"status": "removed", "keyword": keyword, "list_type": list_type, "total": len(lst)}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(500, str(e))

# ══════════════════════════════════════════════════════════════
# DOCUMENT VAULT — FIXED: proper upload/download/list
# ══════════════════════════════════════════════════════════════
VAULT_DOCS_LIST = [
    {"id": "pan_card",        "name": "PAN Card",                              "category": "Company"},
    {"id": "cin_cert",        "name": "CIN Certificate / MOA",                 "category": "Company"},
    {"id": "gst_cert",        "name": "GST Certificate",                       "category": "Company"},
    {"id": "msme_cert",       "name": "MSME / UDYAM Certificate",              "category": "Company"},
    {"id": "poa_doc",         "name": "Power of Attorney (Current)",           "category": "Company"},
    {"id": "cmmi_cert",       "name": "CMMI Level 3 Certificate",              "category": "Certification"},
    {"id": "iso9001_cert",    "name": "ISO 9001:2015 Certificate",             "category": "Certification"},
    {"id": "iso27001_cert",   "name": "ISO 27001:2022 Certificate",            "category": "Certification"},
    {"id": "iso20000_cert",   "name": "ISO 20000-1:2018 Certificate",          "category": "Certification"},
    {"id": "audited_fy2223",  "name": "Audited Accounts FY 2022-23",           "category": "Finance"},
    {"id": "audited_fy2324",  "name": "Audited Accounts FY 2023-24",           "category": "Finance"},
    {"id": "audited_fy2425",  "name": "Audited Accounts FY 2024-25",           "category": "Finance"},
    {"id": "net_worth_cert",  "name": "Net Worth Certificate (CA Signed)",      "category": "Finance"},
    {"id": "solvency_cert",   "name": "Solvency Certificate",                  "category": "Finance"},
    {"id": "blacklisting_dec","name": "Non-Blacklisting Declaration Template", "category": "Declaration"},
    {"id": "mii_dec",         "name": "Make in India Declaration Template",    "category": "Declaration"},
    {"id": "integrity_pact",  "name": "Integrity Pact Template",               "category": "Declaration"},
    {"id": "amc_gis_cc",      "name": "Completion Cert — AMC GIS (10.55Cr)",   "category": "Experience"},
    {"id": "pcscl_po",        "name": "Work Order — PCSCL Smart City (61Cr)",  "category": "Experience"},
    {"id": "kvic_cc",         "name": "Completion Cert — KVIC Geo Portal",     "category": "Experience"},
    {"id": "tcgl_cc",         "name": "Completion Cert — TCGL Tourism",        "category": "Experience"},
    {"id": "vmc_cc",          "name": "Completion Cert — VMC GIS+ERP",         "category": "Experience"},
    {"id": "jumc_po",         "name": "Work Order — JuMC GIS",                 "category": "Experience"},
]

@app.get("/vault")
async def get_vault():
    docs = []
    for doc in VAULT_DOCS_LIST:
        existing = list(VAULT_DIR.glob(f"{doc['id']}.*"))
        docs.append({
            **doc,
            "uploaded": len(existing) > 0,
            "filename": existing[0].name if existing else None,
            "size_kb": round(existing[0].stat().st_size / 1024) if existing else 0,
        })
    return {"documents": docs}

@app.post("/vault/upload/{doc_id}")
async def upload_vault_doc(doc_id: str, file: UploadFile = File(...)):
    valid_ids = {d["id"] for d in VAULT_DOCS_LIST}
    if doc_id not in valid_ids:
        raise HTTPException(400, f"Unknown document ID: {doc_id}")
    # Remove existing files for this doc
    for old in VAULT_DIR.glob(f"{doc_id}.*"):
        old.unlink(missing_ok=True)
    ext = Path(file.filename or "file.pdf").suffix.lower() or ".pdf"
    dest = VAULT_DIR / f"{doc_id}{ext}"
    dest.write_bytes(await file.read())
    try:
        # Save to Drive with vault_ prefix so startup can restore it
        save_to_drive(dest, filename=f"vault_{doc_id}{ext}")
    except Exception:
        pass
    return {"status": "uploaded", "doc_id": doc_id, "filename": dest.name, "size_kb": round(dest.stat().st_size / 1024)}

@app.delete("/vault/{doc_id}")
async def delete_vault_doc(doc_id: str):
    deleted = []
    for f in VAULT_DIR.glob(f"{doc_id}.*"):
        deleted.append(f.name)
        f.unlink(missing_ok=True)
    return {"deleted": deleted}

@app.get("/vault/download/{doc_id}")
async def download_vault_doc(doc_id: str):
    files = list(VAULT_DIR.glob(f"{doc_id}.*"))
    if not files:
        raise HTTPException(404, f"Document {doc_id} not uploaded yet")
    f = files[0]
    media_map = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    return FileResponse(str(f), filename=f.name, media_type=media_map.get(f.suffix.lower(), "application/octet-stream"))

# ══════════════════════════════════════════════════════════════
# DRIVE
# ══════════════════════════════════════════════════════════════
@app.get("/drive-status")
async def drive_status():
    db = load_db()
    return {
        "drive_connected": drive_available(),
        "tenders_in_memory": len(db.get("tenders", {})),
        "db_file_exists": DB_FILE.exists(),
        "db_size_kb": round(DB_FILE.stat().st_size / 1024) if DB_FILE.exists() else 0,
    }

@app.post("/sync-drive")
async def sync_drive():
    if not drive_available():
        return JSONResponse({"status": "error", "message": "Google Drive not connected"}, status_code=400)
    try:
        # Ensure data directory and DB file exist before syncing
        OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
        if not DB_FILE.exists():
            DB_FILE.write_text(json.dumps({"tenders": {}}, indent=2), encoding="utf-8")
        db = load_db()
        count = len(db.get("tenders", {}))
        ok = save_to_drive(DB_FILE)
        if ok:
            return {"status": "ok", "message": f"Synced {count} tenders to Google Drive"}
        return JSONResponse({"status": "error", "message": "Sync failed — check Render logs"}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

# ══════════════════════════════════════════════════════════════
# GOOGLE SHEET SYNC — only runs when user presses Sync button
# Pull: Sheet → JSON  |  Push: JSON → Sheet  |  Both: bidirectional
# ══════════════════════════════════════════════════════════════

@app.post("/sheet-sync/pull")
async def sheet_sync_pull():
    """Pull all tabs from Google Sheet → update nascent_profile.json"""
    try:
        from sync_manager import pull_from_sheet
        result = pull_from_sheet()
        if result.get("status") == "success":
            return result
        return JSONResponse({"status": "error", "message": result.get("error","Pull failed")}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/sheet-sync/push")
async def sheet_sync_push():
    """Push nascent_profile.json → write back to all Google Sheet tabs"""
    try:
        from sync_manager import push_to_sheet
        result = push_to_sheet()
        if result.get("status") == "success":
            return result
        return JSONResponse({"status": "error", "message": result.get("error","Push failed")}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/sheet-sync/both")
async def sheet_sync_both():
    """Pull from Sheet first, then push back — full two-way reconcile"""
    try:
        from sync_manager import pull_from_sheet, push_to_sheet
        pull_result = pull_from_sheet()
        if pull_result.get("status") != "success":
            return JSONResponse({"status": "error", "message": "Pull failed: " + pull_result.get("error","")}, status_code=500)
        push_result = push_to_sheet()
        return {
            "status": "success",
            "pull": pull_result.get("message",""),
            "push": push_result.get("message",""),
            "tabs_read": pull_result.get("tabs_read",[]),
            "tabs_written": push_result.get("tabs_written",[]),
            "tabs_skipped": list(set(
                pull_result.get("tabs_skipped",[]) +
                push_result.get("tabs_skipped",[])
            )),
        }
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/sheet-status")
async def sheet_status():
    """Check if Google Sheet is reachable and return tab list"""
    try:
        from sync_manager import _connect, SHEET_ID
        gc = _connect()
        if not gc:
            return {"connected": False, "reason": "GDRIVE_CREDENTIALS not set or invalid"}
        sh = gc.open_by_key(SHEET_ID)
        tabs = [ws.title for ws in sh.worksheets()]
        required = ["Firm_Identity","Financial_Credentials","Project_Experience"]
        optional = ["Certifications","Bid_Rules"]
        return {
            "connected": True,
            "tabs": tabs,
            "required_tabs": {t: (t in tabs) for t in required},
            "optional_tabs": {t: (t in tabs) for t in optional},
            "sheet_id": SHEET_ID,
        }
    except Exception as e:
        return {"connected": False, "reason": str(e)}

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
            drive_ok = save_to_drive(DB_FILE)
        return {"status": "ok", "tenders": count, "drive_saved": drive_ok, "message": f"Loaded {count} tenders"}
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")
    except Exception as e:
        raise HTTPException(500, str(e))

# ══════════════════════════════════════════════════════════════
# CHATBOT
# ══════════════════════════════════════════════════════════════
@app.post("/chat")
async def chat(data: dict = Body(...)):
    message = data.get("message", "").strip()
    if not message:
        raise HTTPException(400, "Empty message")
    history = load_history()
    result = process_message(message, history)
    return result

@app.get("/chat/history")
async def get_chat_history():
    return {"history": load_history()}

@app.delete("/chat/history")
async def clear_chat_history():
    h = OUTPUT_DIR / "chat_history.json"
    if h.exists():
        h.unlink()
    return {"status": "cleared"}

# ══════════════════════════════════════════════════════════════
# T247 AUTO-DOWNLOAD
# ══════════════════════════════════════════════════════════════
@app.post("/tender/{t247_id}/auto-download")
async def auto_download_tender(t247_id: str, data: dict = Body(default={})):
    """Download tender ZIP from T247 using saved credentials."""
    config   = load_config()
    username = config.get("t247_username", "")
    password = config.get("t247_password", "")
    t247_url = f"https://www.tender247.com/tender/detail/{t247_id}"

    if not username or not password:
        return {"status": "no_credentials",
                "message": "T247 credentials not saved. Go to Settings → T247 tab.",
                "t247_url": t247_url}
    try:
        from t247_downloader import auto_download_tender as t247_dl
        # Get tender info for portal code
        db = load_db()
        tender = db.get("tenders", {}).get(t247_id, {})
        tender_no   = tender.get("ref_no", "")
        portal_code = "gem" if tender.get("is_gem") else "nprocure"
        result = t247_dl(t247_id, tender_no, portal_code, username, password, str(TEMP_DIR))
        if result.get("success"):
            return {
                "status":   "success",
                "message":  f"Downloaded {result.get('filename','')} from T247",
                "filename": result.get("filename",""),
                "filepath": result.get("filepath",""),
                "t247_url": t247_url,
            }
        return {
            "status":   "failed",
            "message":  result.get("error", "Download failed"),
            "t247_url": t247_url,
        }
    except ImportError:
        return {
            "status":   "manual",
            "message":  "Open T247 website to download documents manually.",
            "t247_url": t247_url,
            "t247_id":  t247_id,
        }
    except Exception as e:
        return {
            "status":   "failed",
            "message":  str(e),
            "t247_url": t247_url,
        }

@app.get("/test-t247")
async def test_t247():
    config = load_config()
    username = config.get("t247_username", "")
    if not username:
        return {"status": "error", "message": "No T247 credentials saved"}
    return {"status": "ok", "username": username, "message": f"Credentials saved for {username}"}

# ══════════════════════════════════════════════════════════════
# WORKSPACE
# ══════════════════════════════════════════════════════════════
@app.get("/workspace/{t247_id}")
async def get_workspace(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, f"Tender {t247_id} not found")
    reports = []
    for f in OUTPUT_DIR.glob(f"*{t247_id}*.docx"):
        reports.append({"filename": f.name, "size_kb": round(f.stat().st_size / 1024), "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d-%b-%Y %H:%M")})
    return {"tender": tender, "checklist": tender.get("doc_checklist", []), "prebid_queries": tender.get("prebid_queries", []), "reports": reports}

@app.post("/prebid-sent/{t247_id}")
async def mark_prebid_sent(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["prebid_sent"] = True
    t["prebid_sent_at"] = datetime.now().isoformat()
    t["prebid_sent_to"] = data.get("email", "")
    t["status"] = "Pre-bid Sent"
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved"}

# ══════════════════════════════════════════════════════════════
# RECLASSIFY ALL TENDERS — re-applies bid rules to all tenders
# ══════════════════════════════════════════════════════════════
@app.post("/reclassify-all")
async def reclassify_all():
    """Re-run classify_tender() on all non-AI-analysed tenders using current profile rules."""
    try:
        from excel_processor import classify_tender
    except ImportError:
        raise HTTPException(500, "excel_processor not available")
    db = load_db()
    counts = {"bid": 0, "no_bid": 0, "conditional": 0, "review": 0, "count": 0}
    for tid, t in db["tenders"].items():
        # Always re-classify (rules may have changed)
        brief = t.get("brief", "")
        cost_raw = t.get("estimated_cost_raw", 0)
        eligibility = t.get("eligibility", "")
        checklist_str = t.get("checklist", "")
        try:
            result = classify_tender(brief, float(cost_raw or 0), eligibility, checklist_str)
            # Only update verdict if AI hasn't set one
            if not t.get("bid_no_bid_done"):
                t["verdict"] = result["verdict"]
                t["verdict_color"] = result["verdict_color"]
                t["reason"] = result["reason"]
            v = result["verdict"]
            if v == "BID": counts["bid"] += 1
            elif v == "NO-BID": counts["no_bid"] += 1
            elif v == "CONDITIONAL": counts["conditional"] += 1
            else: counts["review"] += 1
            counts["count"] += 1
        except Exception as e:
            print(f"reclassify error for {tid}: {e}")
    save_db(db)
    return {"status": "ok", **counts}


# ── ADDITIONAL ROUTES FROM main_extra ──
@app.get("/skipped-tenders")
async def get_skipped():
    """Return all skipped tenders."""
    db = load_db()
    skipped = [
        t for t in db["tenders"].values()
        if t.get("status") == "Not Interested"
    ]
    return {"tenders": skipped}

@app.post("/tender/{t247_id}/reanalyse")
async def reanalyse_tender(t247_id: str):
    """Re-run AI analysis on previously uploaded tender (from saved text in DB)."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    saved_text = tender.get("raw_text", "")
    if not saved_text:
        raise HTTPException(400, "No saved document text. Upload files and analyse again.")
    # Re-run analysis
    from ai_analyzer import analyze_with_gemini, merge_results
    prebid_passed = tender.get("prebid_passed", False)
    ai_result = analyze_with_gemini(saved_text, prebid_passed)
    if "error" not in ai_result:
        updated = merge_results(tender, ai_result, prebid_passed)
        updated["bid_no_bid_done"] = True
        updated["analysed_at"] = datetime.now().isoformat()
        save_tender(t247_id, updated)
        return {"status": "success", "verdict": updated.get("verdict", "REVIEW")}
    return {"status": "error", "error": ai_result.get("error", "Unknown error")}


# ── GENERATE PREBID LETTER ─────────────────────────────────────

