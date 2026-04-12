"""
Bid/No-Bid Automation v5 - Complete System
FastAPI backend with all modules
"""

import zipfile, tempfile, shutil, json, re, os
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
from gdrive_sync import init_drive, save_to_drive, load_from_drive, is_available as drive_available
from tracker import (get_deadline_alerts, get_pipeline_stats,
                     get_win_loss_stats, generate_doc_checklist,
                     PIPELINE_STAGES, STAGE_COLORS)
from boq_engine import (extract_boq_from_scope, calculate_boq_totals,
                        get_boq_constants)

app = FastAPI(title="Bid/No-Bid System v5", version="5.0")

@app.on_event("startup")
async def startup_event():
    import time
    print("🚀 Starting Bid/No-Bid System...")
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    TEMP_DIR.mkdir(exist_ok=True, parents=True)
    drive_ok = init_drive()
    print(f"📁 Google Drive: {'✅ Connected' if drive_ok else '❌ Not configured'}")
    if drive_ok:
        for attempt in range(3):
            try:
                success = load_from_drive(DB_FILE)
                if success:
                    db = load_db()
                    count = len(db.get("tenders", {}))
                    print(f"✅ Loaded {count} tenders from Google Drive")
                    break
                else:
                    print(f"⚠️ Drive load attempt {attempt+1} returned empty")
                    time.sleep(2)
            except Exception as e:
                print(f"⚠️ Drive load attempt {attempt+1} failed: {e}")
                time.sleep(2)
    else:
        if DB_FILE.exists():
            db = load_db()
            count = len(db.get("tenders", {}))
            print(f"✅ Using local DB: {count} tenders")
        else:
            print("⚠️ No DB found — fresh start")
    print(f"✅ Server ready")

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
TEMP_DIR.mkdir(exist_ok=True, parents=True)
DB_FILE = OUTPUT_DIR / "tenders_db.json"

for d in [OUTPUT_DIR, TEMP_DIR]:
    d.mkdir(exist_ok=True, parents=True)

# ── DB helpers ─────────────────────────────────────────────────────────────

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

# ── ZIP helpers ────────────────────────────────────────────────────────────

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

# ══════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root():
    index = BASE_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Bid/No-Bid System v5</h1>")

# ── Excel Import ───────────────────────────────────────────────────────────

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
        return {
            "status": "success",
            "total": len(tenders),
            "added": added,
            "updated": updated,
            "imported": len(tenders),
            "bid": sum(1 for t in tenders if t.get("verdict") == "BID"),
            "no_bid": sum(1 for t in tenders if t.get("verdict") == "NO-BID"),
            "tenders": tenders,
        }
    finally:
        tmp.unlink(missing_ok=True)

# ── Dashboard Data ─────────────────────────────────────────────────────────

@app.get("/dashboard")
async def dashboard():
    db = load_db()
    tenders = list(db["tenders"].values())
    stats = {
        "total": len(tenders),
        "bid": sum(1 for t in tenders if t.get("verdict") == "BID"),
        "no_bid": sum(1 for t in tenders if t.get("verdict") == "NO-BID"),
        "conditional": sum(1 for t in tenders if t.get("verdict") == "CONDITIONAL"),
        "review": sum(1 for t in tenders if t.get("verdict") == "REVIEW"),
        "analysed": sum(1 for t in tenders if t.get("bid_no_bid_done")),
        "deadline_today": sum(1 for t in tenders if days_left(t.get("deadline", "")) == 0),
        "deadline_3days": sum(1 for t in tenders if 0 < days_left(t.get("deadline", "")) <= 3),
    }
    tenders_sorted = sorted(tenders, key=lambda t: days_left(t.get("deadline", "999")))
    return {"stats": stats, "tenders": tenders_sorted}

@app.get("/tenders")
async def get_all_tenders():
    db = load_db()
    return {"tenders": list(db["tenders"].values())}

# ── Process ZIP / Files ────────────────────────────────────────────────────

@app.post("/process")
async def process_zip(file: UploadFile = File(...), t247_id: str = ""):
    return await process_files(files=[file], t247_id=t247_id)

@app.post("/prebid-queries")
async def get_prebid_queries(data: dict = Body(...)):
    queries = generate_prebid_queries(data)
    return {"queries": queries}

@app.get("/prebid-queries/{t247_id}")
async def get_saved_prebid_queries(t247_id: str):
    tender = get_tender(t247_id)
    return {"queries": tender.get("prebid_queries", [])}

@app.post("/tender/{t247_id}/status")
async def update_status(t247_id: str, data: dict = Body(...)):
    tender = get_tender(t247_id)
    tender.update(data)
    save_tender(t247_id, tender)
    return {"status": "saved"}

@app.get("/tender/{t247_id}")
async def get_tender_detail(t247_id: str):
    return get_tender(t247_id)

@app.post("/tender/{t247_id}/skip")
async def skip_tender(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["status"] = "Not Interested"
    t["skip_reason"] = data.get("reason", "Not interested")
    t["skipped_at"] = datetime.now().isoformat()
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

# ── Process Files (main analysis) ─────────────────────────────────────────

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
            ext = dest.suffix.lower()
            if ext == ".zip":
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

        corrigendum_files = [f for f in doc_files if
                             any(k in f.name.lower() for k in
                                 ["corrigendum", "addendum", "amendment",
                                  "corr_", "addend", "revised", "rectification"])]
        main_files = [f for f in doc_files if f not in corrigendum_files]

        extractor = TenderExtractor()
        tender_data = extractor.process_documents(main_files if main_files else doc_files)

        if corrigendum_files:
            corr_extractor = TenderExtractor()
            corr_data = corr_extractor.process_documents(corrigendum_files)
            override_fields = ["bid_submission_date", "bid_opening_date",
                               "bid_start_date", "prebid_query_date",
                               "estimated_cost", "emd", "tender_fee"]
            for field in override_fields:
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
            tender_data["ai_warning"] = "Gemini API key not configured. Go to Settings."

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
                # Save full analysis data for BOQ and other modules
                "scope_items": tender_data.get("scope_items", []),
                "contract_period": tender_data.get("contract_period", ""),
                "post_implementation": tender_data.get("post_implementation", ""),
                "pq_criteria": tender_data.get("pq_criteria", []),
                "tq_criteria": tender_data.get("tq_criteria", []),
                "payment_terms": tender_data.get("payment_terms", []),
                "notes": tender_data.get("notes", []),
                "overall_verdict": tender_data.get("overall_verdict", {}),
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

@app.get("/reports")
async def list_reports():
    files = sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"filename": f.name,
             "size_kb": round(f.stat().st_size / 1024, 1),
             "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d %b %Y %H:%M")}
            for f in files[:100]]

# ── Config ─────────────────────────────────────────────────────────────────

@app.get("/config")
async def get_config_route():
    config = load_config()
    key = config.get("gemini_api_key", "")
    return {
        "gemini_api_key_set": bool(key),
        "gemini_api_key": key,
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
    masked = []
    for k in all_keys:
        if len(k) > 12:
            masked.append(k[:8] + "..." + k[-4:])
        else:
            masked.append(k[:4] + "...")
    return {"gemini_api_keys": masked, "total_keys": len(all_keys), "ai_active": bool(all_keys)}

@app.post("/config")
async def update_config_route(data: dict = Body(...)):
    config = load_config()
    if "gemini_api_key" in data and data["gemini_api_key"]:
        config["gemini_api_key"] = data["gemini_api_key"]
    if "gemini_api_keys" in data:
        keys = [k.strip() for k in data["gemini_api_keys"] if k and k.strip()]
        config["gemini_api_keys"] = keys
        if keys:
            config["gemini_api_key"] = keys[0]
    if "groq_api_key" in data and data["groq_api_key"]:
        config["groq_api_key"] = data["groq_api_key"].strip()
    if "t247_username" in data:
        config["t247_username"] = data["t247_username"]
    if "t247_password" in data:
        config["t247_password"] = data["t247_password"]
    save_config(config)
    return {"status": "saved", "keys_saved": len(config.get("gemini_api_keys", []))}

# ── Profile ─────────────────────────────────────────────────────────────────

@app.get("/profile")
async def get_profile():
    from nascent_checker import load_profile
    return load_profile()

@app.post("/profile")
async def update_profile(data: dict = Body(...)):
    profile_path = BASE_DIR / "nascent_profile.json"
    profile_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"status": "saved"}

# ── BOQ Module ──────────────────────────────────────────────────────────────

@app.get("/boq/constants")
async def boq_constants():
    """Return category, unit type, and role lists for dropdowns."""
    return get_boq_constants()

@app.get("/boq/{t247_id}")
async def get_boq(t247_id: str):
    """Get BOQ for a tender. Auto-generate from scope if not yet created."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    boq = tender.get("boq")
    if boq:
        return {
            "t247_id": t247_id,
            "tender_name": tender.get("tender_name") or tender.get("brief", ""),
            "boq": boq,
            "source": "saved",
        }

    # Auto-generate from scope
    items = extract_boq_from_scope(tender)
    return {
        "t247_id": t247_id,
        "tender_name": tender.get("tender_name") or tender.get("brief", ""),
        "boq": {
            "items": items,
            "margin_pct": 15.0,
            "gst_pct": 18.0,
        },
        "source": "auto",
    }

@app.post("/boq/{t247_id}")
async def save_boq(t247_id: str, data: dict = Body(...)):
    """Save BOQ items with rates filled by user."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    items = data.get("items", [])
    margin_pct = float(data.get("margin_pct", 15.0))
    gst_pct = float(data.get("gst_pct", 18.0))

    # Recalculate totals server-side
    result = calculate_boq_totals(items, margin_pct, gst_pct)

    tender["boq"] = {
        "items": result["items"],
        "margin_pct": margin_pct,
        "gst_pct": gst_pct,
        "base_total": result["base_total"],
        "margin_amount": result["margin_amount"],
        "subtotal": result["subtotal"],
        "gst_amount": result["gst_amount"],
        "grand_total": result["grand_total"],
        "saved_at": datetime.now().isoformat(),
    }
    save_tender(t247_id, tender)
    return {"status": "saved", "totals": result}

@app.post("/boq/{t247_id}/regenerate")
async def regenerate_boq(t247_id: str):
    """Re-run AI extraction to regenerate BOQ from scratch."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    items = extract_boq_from_scope(tender)
    return {
        "t247_id": t247_id,
        "boq": {"items": items, "margin_pct": 15.0, "gst_pct": 18.0},
        "source": "regenerated",
    }

# ── Test routes ─────────────────────────────────────────────────────────────

@app.get("/test-groq")
async def test_groq():
    config = load_config()
    groq_key = config.get("groq_api_key", "")
    if not groq_key:
        return {"status": "missing", "message": "groq_api_key not found in config"}
    import urllib.request
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": "Say OK"}],
            "max_tokens": 5
        }).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + groq_key},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
        return {"status": "success", "response": result["choices"][0]["message"]["content"]}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/test-ai")
async def test_ai():
    from ai_analyzer import get_api_key, call_gemini
    key = get_api_key()
    if not key:
        return {"status": "error", "message": "No API key in config"}
    try:
        result = call_gemini('Return this exact JSON: {"status": "ok"}', key)
        return {"status": "success", "api_key_present": True, "gemini_response": result[:100]}
    except Exception as e:
        return {"status": "error", "api_key_present": True, "error": str(e)}

@app.get("/tender-quickview/{t247_id}")
async def tender_quickview(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    return tender

@app.post("/auto-download/{t247_id}")
async def auto_download_tender(t247_id: str):
    return {
        "status": "unavailable",
        "message": "Auto-download not available on Render free tier. Download ZIP manually from tender247.com and upload via Analyse page."
    }

@app.get("/test-chat")
async def test_chat():
    try:
        h = load_history()
        return {"status": "ok", "drive_sync": drive_available(), "chatbot_loaded": True, "history_count": len(h)}
    except Exception as e:
        return {"status": "error", "error": str(e)}

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
    if "outcome_notes" in data:
        t["outcome_notes"] = data["outcome_notes"]
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

@app.post("/checklist/{t247_id}")
async def save_checklist(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["doc_checklist"] = data.get("checklist", [])
    db["tenders"][t247_id] = t
    pct = round(sum(1 for d in t["doc_checklist"] if d.get("done")) /
                max(len(t["doc_checklist"]), 1) * 100)
    t["checklist_pct"] = pct
    save_db(db)
    return {"status": "saved", "completion_pct": pct}

@app.post("/checklist/{t247_id}/item")
async def toggle_checklist_item(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    checklist = t.get("doc_checklist", [])
    item_id = data.get("id")
    done = data.get("done", False)
    for item in checklist:
        if str(item.get("id")) == str(item_id):
            item["done"] = done
            break
    t["doc_checklist"] = checklist
    pct = round(sum(1 for d in checklist if d.get("done")) / max(len(checklist), 1) * 100)
    t["checklist_pct"] = pct
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved", "completion_pct": pct}

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

        headers = ["Sr.", "T247 ID", "Reference No.", "Brief", "Organization",
                   "Location", "Cost (Cr)", "EMD", "Doc Fee", "MSME Exempt",
                   "Deadline", "Days Left", "Verdict", "Stage",
                   "Analysed", "Checklist %", "Reason"]
        col_widths = [5, 12, 25, 45, 30, 20, 10, 12, 10, 12, 14, 10, 14, 18, 10, 12, 35]

        for ci, (hdr, w) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(row=1, column=ci, value=hdr)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[cell.column_letter].width = w

        ws.row_dimensions[1].height = 30

        verdict_colors = {
            "BID": "E2EFDA", "CONDITIONAL": "FFF2CC",
            "NO-BID": "FCE4D6", "REVIEW": "DEEAF1"
        }

        def dl(t):
            try:
                dl_str = t.get("deadline", "")
                for fmt in ["%d-%m-%Y", "%d/%m/%Y"]:
                    try:
                        return (datetime.strptime(dl_str.split()[0], fmt).date() - date.today()).days
                    except:
                        continue
            except:
                pass
            return 999

        tenders_sorted = sorted(tenders, key=dl)

        for ri, t in enumerate(tenders_sorted, 2):
            days = dl(t)
            v = t.get("verdict", "")
            row_fill = PatternFill("solid", fgColor=verdict_colors.get(v, "FFFFFF"))
            vals = [
                ri - 1, t.get("t247_id", ""), t.get("ref_no", ""),
                t.get("brief", ""), t.get("org_name", ""), t.get("location", ""),
                t.get("estimated_cost_cr", ""), t.get("emd", ""), t.get("doc_fee", ""),
                t.get("msme_exemption", ""), t.get("deadline", ""),
                days if days < 999 else "—",
                v, t.get("status", "Identified"),
                "Yes" if t.get("bid_no_bid_done") else "No",
                str(t.get("checklist_pct", "0")) + "%",
                t.get("reason", "")[:100]
            ]
            for ci, val in enumerate(vals, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                cell.fill = row_fill
                cell.alignment = Alignment(vertical="center", wrap_text=True)

        ws.freeze_panes = "A2"

        fname = f"Tenders_Export_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
        fpath = OUTPUT_DIR / fname
        wb.save(str(fpath))

        return FileResponse(
            str(fpath), filename=fname,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        raise HTTPException(500, f"Export failed: {str(e)}")

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

@app.get("/skipped")
async def get_skipped():
    db = load_db()
    skipped = [t for t in db["tenders"].values() if t.get("status") == "Not Interested"]
    return {"skipped": skipped}

@app.get("/health")
async def health():
    config = load_config()
    db = load_db()
    return {
        "status": "ok",
        "version": "5.0",
        "ai_configured": bool(config.get("gemini_api_key")),
        "drive_sync": drive_available(),
        "tenders_loaded": len(db.get("tenders", {}))
    }

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.post("/upload-db")
async def upload_db(file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = json.loads(content)
        count = len(data.get("tenders", {}))
        if count == 0:
            raise HTTPException(400, "File has 0 tenders — check file")
        DB_FILE.write_bytes(content)
        drive_ok = False
        if drive_available():
            drive_ok = save_to_drive(DB_FILE)
        return {
            "status": "ok",
            "tenders": count,
            "drive_saved": drive_ok,
            "message": f"✅ Loaded {count} tenders."
        }
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/sync-drive")
async def sync_drive():
    if not drive_available():
        return JSONResponse({"status": "error", "message": "Google Drive not connected"}, status_code=400)
    try:
        db = load_db()
        count = len(db.get("tenders", {}))
        ok = save_to_drive(DB_FILE)
        if ok:
            return {"status": "ok", "message": f"✅ Synced {count} tenders to Google Drive"}
        else:
            return JSONResponse({"status": "error", "message": "Sync failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

# Alias
@app.post("/sync-sheets")
async def sync_sheets():
    return await sync_drive()

@app.get("/drive-status")
async def drive_status():
    db = load_db()
    return {
        "drive_connected": drive_available(),
        "tenders_in_memory": len(db.get("tenders", {})),
        "db_file_exists": DB_FILE.exists(),
        "db_size_kb": round(DB_FILE.stat().st_size / 1024) if DB_FILE.exists() else 0
    }

@app.post("/bid-result/{t247_id}")
async def save_bid_result(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["outcome"] = data.get("outcome", "")
    t["outcome_value"] = data.get("value", "")
    t["outcome_competitor"] = data.get("competitor", "")
    t["outcome_notes"] = data.get("notes", "")
    t["outcome_date"] = datetime.now().isoformat()
    if data.get("outcome") == "Won":
        t["status"] = "Won"
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "saved"}
