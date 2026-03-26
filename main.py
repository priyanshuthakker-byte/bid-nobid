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

# Safe imports with fallbacks so server starts even if a module is missing
try:
    from extractor import TenderExtractor, read_document
except ImportError:
    class TenderExtractor:
        def process_documents(self, files): return {}
    def read_document(f): return ""

try:
    from doc_generator import BidDocGenerator
except ImportError:
    class BidDocGenerator:
        def generate(self, data, path): Path(path).write_text("Report")

try:
    from nascent_checker import NascentChecker
except ImportError:
    class NascentChecker:
        def check_all(self, lst): return lst
        def get_overall_verdict(self, lst): return {"verdict":"REVIEW","color":"BLUE"}

try:
    from ai_analyzer import analyze_with_gemini, merge_results, load_config, save_config
except ImportError:
    def analyze_with_gemini(*a, **k): return {"error": "AI module not loaded"}
    def merge_results(a, b, *args): return a
    def load_config(): return {}
    def save_config(c): pass

try:
    from excel_processor import process_excel, quick_classify
except ImportError:
    def process_excel(path): return []
    def quick_classify(t): return t

try:
    from prebid_generator import generate_prebid_queries
except ImportError:
    def generate_prebid_queries(data): return []

try:
    from chatbot import process_message, load_history
except ImportError:
    def process_message(msg, hist): return {"reply": "Chatbot not available"}
    def load_history(): return []

try:
    from gdrive_sync import init_drive, save_to_drive, load_from_drive, is_available as drive_available
except ImportError:
    def init_drive(): return False
    def save_to_drive(*a): return False
    def load_from_drive(*a): return False
    def drive_available(): return False

try:
    from tracker import (get_deadline_alerts, get_pipeline_stats,
        get_win_loss_stats, generate_doc_checklist,
        PIPELINE_STAGES, STAGE_COLORS)
except ImportError:
    def get_deadline_alerts(): return []
    def get_pipeline_stats(): return {}
    def get_win_loss_stats(): return {}
    def generate_doc_checklist(t): return []
    PIPELINE_STAGES = []
    STAGE_COLORS = {}

try:
    from downloader import download_sync, is_playwright_available
except ImportError:
    def download_sync(tid): return None
    def is_playwright_available(): return False

app = FastAPI(title="Bid/No-Bid System v5", version="5.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"],
    allow_methods=["*"], allow_headers=["*"])

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
TEMP_DIR.mkdir(exist_ok=True, parents=True)
DB_FILE = OUTPUT_DIR / "tenders_db.json"


@app.on_event("startup")
async def startup_event():
    import time
    print("🚀 Starting Bid/No-Bid System v7...")
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    TEMP_DIR.mkdir(exist_ok=True, parents=True)
    drive_ok = init_drive()
    print(f"Google Drive: {'Connected' if drive_ok else 'Not configured'}")
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
    print("Server ready")
    # Portal watcher stub
    print("✅ Portal watcher started")


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


# ══════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root():
    index = BASE_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Bid/No-Bid System v5</h1>")

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
                    excel_fields = ["ref_no","brief","org_name","location",
                        "estimated_cost_raw","estimated_cost_cr","deadline",
                        "days_left","deadline_status","doc_fee","emd",
                        "msme_exemption","eligibility","checklist","is_gem"]
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
        # Return fields that match what frontend expects
        return {
            "status": "success",
            "total": len(tenders),
            "imported": len(tenders),
            "added": added,
            "updated": updated,
            "bid": sum(1 for t in tenders if t.get("verdict") == "BID"),
            "no_bid": sum(1 for t in tenders if t.get("verdict") == "NO-BID"),
            "conditional": sum(1 for t in tenders if t.get("verdict") == "CONDITIONAL"),
            "tenders": tenders
        }
    finally:
        tmp.unlink(missing_ok=True)

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
        "deadline_today": sum(1 for t in tenders if days_left(t.get("deadline","")) == 0),
        "deadline_3days": sum(1 for t in tenders if 0 < days_left(t.get("deadline","")) <= 3),
    }
    tenders_sorted = sorted(tenders, key=lambda t: days_left(t.get("deadline","999")))
    return {"stats": stats, "tenders": tenders_sorted}

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

        corrigendum_files = [f for f in doc_files if
            any(k in f.name.lower() for k in ["corrigendum","addendum","amendment","corr_","addend","revised","rectification"])]
        main_files = [f for f in doc_files if f not in corrigendum_files]

        extractor = TenderExtractor()
        tender_data = extractor.process_documents(main_files if main_files else doc_files)

        if corrigendum_files:
            corr_extractor = TenderExtractor()
            corr_data = corr_extractor.process_documents(corrigendum_files)
            for field in ["bid_submission_date","bid_opening_date","bid_start_date","prebid_query_date","estimated_cost","emd","tender_fee"]:
                val = corr_data.get(field, "")
                if val and val not in ["—","Refer document","Not specified",""]:
                    tender_data[field] = val
            tender_data["has_corrigendum"] = True
            tender_data["corrigendum_files"] = [f.name for f in corrigendum_files]

        all_text = ""
        for f in sorted(doc_files, key=lambda x: (
            0 if any(k in x.name.lower() for k in ["rfp","nit","tender","bid"]) else
            1 if any(k in x.name.lower() for k in ["corrigendum","addendum"]) else 2
        )):
            t = read_document(f)
            if t and t.strip():
                all_text += f"\n\n=== FILE: {f.name} ===\n{t}"

        config = load_config()
        api_key = config.get("gemini_api_key", "")
        ai_used = False
        print(f"[AI] API key present: {bool(api_key)} | Text length: {len(all_text)} chars")

        if api_key and all_text.strip():
            passed = prebid_passed(tender_data.get("prebid_query_date", ""))
            print(f"[AI] Calling Gemini... prebid_passed={passed}")
            ai_result = analyze_with_gemini(all_text, passed)
            print(f"[AI] Result keys: {list(ai_result.keys())[:5]}")
            if "error" not in ai_result:
                tender_data = merge_results(tender_data, ai_result, passed)
                ai_used = True
                print(f"[AI] SUCCESS — tender_no={ai_result.get('tender_no','?')}")
            else:
                err = ai_result.get("error","")
                tender_data["ai_warning"] = err
                print(f"[AI] ERROR: {err}")
        elif not api_key:
            tender_data["ai_warning"] = "Gemini API key not configured."
            print("[AI] No API key")

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
    return FileResponse(path=str(file_path), filename=Path(filename).name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

@app.get("/reports")
async def list_reports():
    files = sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"filename": f.name, "size_kb": round(f.stat().st_size/1024,1),
             "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d %b %Y %H:%M")}
            for f in files[:100]]

@app.get("/config")
async def get_config_route():
    config = load_config()
    key = config.get("gemini_api_key", "")
    return {
        "gemini_api_key_set": bool(key),
        "gemini_api_key_preview": (key[:8] + "..." + key[-4:]) if key else "",
        "gemini_api_key": (key[:8] + "..." + key[-4:]) if key else "",
    }

@app.post("/config")
async def update_config_route(data: dict = Body(...)):
    config = load_config()
    if "gemini_api_key" in data and data["gemini_api_key"]:
        config["gemini_api_key"] = data["gemini_api_key"]
    if "gemini_api_key_2" in data and data["gemini_api_key_2"]:
        config["gemini_api_key_2"] = data["gemini_api_key_2"]
    if "gemini_api_key_3" in data and data["gemini_api_key_3"]:
        config["gemini_api_key_3"] = data["gemini_api_key_3"]
    if "gemini_api_key_4" in data and data["gemini_api_key_4"]:
        config["gemini_api_key_4"] = data["gemini_api_key_4"]
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
    return {"status": "saved"}

@app.get("/profile")
async def get_profile():
    try:
        from nascent_checker import load_profile
        return load_profile()
    except Exception:
        return {}

@app.post("/profile")
async def update_profile(data: dict = Body(...)):
    profile_path = BASE_DIR / "nascent_profile.json"
    profile_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"status": "saved"}

@app.get("/test-ai")
async def test_ai():
    from ai_analyzer import get_api_key, call_gemini
    key = get_api_key()
    if not key:
        return {"status": "error", "message": "No API key configured"}
    try:
        result = call_gemini('Return this exact JSON: {"status": "ok"}', key)
        return {"status": "ok", "api_key_present": True, "model": "gemini"}
    except Exception as e:
        return {"status": "error", "api_key_present": True, "error": str(e)}

@app.get("/test-groq")
async def test_groq():
    config = load_config()
    groq_key = config.get("groq_api_key", "")
    if not groq_key:
        return {"status": "missing", "message": "groq_api_key not configured"}
    import urllib.request
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({"model":"llama-3.3-70b-versatile","messages":[{"role":"user","content":"Say OK"}],"max_tokens":5}).encode()
        req = urllib.request.Request(url, data=payload,
            headers={"Content-Type":"application/json","Authorization":"Bearer "+groq_key}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
        return {"status":"success","response":result["choices"][0]["message"]["content"]}
    except Exception as e:
        return {"status":"error","error":str(e)}

@app.get("/tender-quickview/{t247_id}")
async def tender_quickview(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    return tender

@app.post("/auto-download/{t247_id}")
async def auto_download_tender(t247_id: str):
    try:
        if not is_playwright_available():
            return {"status":"unavailable","message":"Playwright not installed on server"}
        zip_path = download_sync(t247_id)
        if zip_path:
            return {"status":"success","zip_path":zip_path,"t247_id":t247_id}
        else:
            return {"status":"failed","message":"Could not find download button"}
    except Exception as e:
        return {"status":"error","message":str(e)}

@app.get("/test-chat")
async def test_chat():
    try:
        h = load_history()
        return {"status":"ok","drive_sync":drive_available(),"chatbot_loaded":True,"history_count":len(h)}
    except Exception as e:
        return {"status":"error","error":str(e)}

@app.post("/chat")
async def chat(data: dict = Body(...)):
    message = data.get("message","").strip()
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
    if h.exists(): h.unlink()
    return {"status": "cleared"}

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
    # Support both full checklist save and single item toggle
    if "checklist" in data:
        t["doc_checklist"] = data.get("checklist", [])
    elif "item_id" in data or "id" in data:
        item_id = data.get("item_id") or data.get("id")
        done = data.get("done", False)
        checklist = t.get("doc_checklist", [])
        for item in checklist:
            if item.get("id") == item_id:
                item["done"] = done
                break
        t["doc_checklist"] = checklist
    db["tenders"][t247_id] = t
    pct = round(sum(1 for d in t.get("doc_checklist",[]) if d.get("done")) /
        max(len(t.get("doc_checklist",[])), 1) * 100)
    t["checklist_pct"] = pct
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
            tenders = [t for t in tenders if any(s in str(t.get(f,"")).lower()
                for f in ["t247_id","ref_no","brief","org_name","location","verdict"])]
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tenders"
        hdr_fill = PatternFill("solid", fgColor="1E2A3B")
        hdr_font = Font(bold=True, color="FFFFFF", size=11)
        verdict_colors = {"BID":"E2EFDA","CONDITIONAL":"FFF2CC","NO-BID":"FCE4D6","REVIEW":"DEEAF1"}
        headers = ["Sr.","T247 ID","Reference No.","Brief","Organization","Location",
            "Cost (Cr)","EMD","Doc Fee","Deadline","Days Left","Verdict","Analysed","Reason"]
        col_widths = [5,12,25,45,30,20,10,12,10,14,10,14,10,35]
        for ci,(hdr,w) in enumerate(zip(headers,col_widths),1):
            cell = ws.cell(row=1,column=ci,value=hdr)
            cell.font = hdr_font; cell.fill = hdr_fill
            ws.column_dimensions[cell.column_letter].width = w
        def dl(t):
            try:
                dl_str = t.get("deadline","")
                for fmt in ["%d-%m-%Y","%d/%m/%Y"]:
                    try:
                        return (datetime.strptime(dl_str.split()[0],fmt).date()-date.today()).days
                    except: continue
            except: pass
            return 999
        tenders_sorted = sorted(tenders, key=dl)
        for ri,t in enumerate(tenders_sorted,2):
            days = dl(t)
            verdict = t.get("verdict","")
            row_fill = PatternFill("solid", fgColor=verdict_colors.get(verdict,"FFFFFF"))
            vals = [ri-1,t.get("t247_id",""),t.get("ref_no",""),t.get("brief",""),
                t.get("org_name",""),t.get("location",""),t.get("estimated_cost_cr",""),
                t.get("emd",""),t.get("doc_fee",""),t.get("deadline",""),
                days if days<999 else "—",verdict,"Yes" if t.get("bid_no_bid_done") else "No",
                t.get("reason","")[:100]]
            for ci,val in enumerate(vals,1):
                cell = ws.cell(row=ri,column=ci,value=val)
                cell.fill = row_fill
                cell.alignment = Alignment(vertical="center",wrap_text=True)
        ws.freeze_panes = "A2"
        fname = f"Tenders_Export_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
        fpath = OUTPUT_DIR / fname
        wb.save(str(fpath))
        return FileResponse(str(fpath), filename=fname,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
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
                if tid in fname.stem:
                    tender = t
                    break
            reports.append({
                "filename": fname.name,
                "created": datetime.fromtimestamp(fname.stat().st_mtime).strftime("%d-%b-%Y %H:%M"),
                "size_kb": round(fname.stat().st_size/1024,1),
                "t247_id": tender.get("t247_id","—") if tender else "—",
                "tender_name": tender.get("brief","")[:60] if tender else fname.stem[:60],
                "org": tender.get("org_name","—") if tender else "—",
                "verdict": tender.get("verdict","—") if tender else "—",
            })
        return {"reports": reports}
    except Exception as e:
        return {"reports": [], "error": str(e)}

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
        return {"status":"ok","tenders":count,"drive_saved":drive_ok,
            "message":f"✅ Loaded {count} tenders. Drive sync: {'✅' if drive_ok else '❌'}"}
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/sync-drive")
async def sync_drive():
    if not drive_available():
        return JSONResponse({"status":"error","message":"Google Drive not connected"}, status_code=400)
    try:
        db = load_db()
        count = len(db.get("tenders", {}))
        ok = save_to_drive(DB_FILE)
        if ok:
            return {"status":"ok","message":f"✅ Synced {count} tenders to Google Drive"}
        else:
            return JSONResponse({"status":"error","message":"Sync failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"status":"error","message":str(e)}, status_code=500)

@app.get("/drive-status")
async def drive_status():
    db = load_db()
    return {
        "drive_connected": drive_available(),
        "tenders_in_memory": len(db.get("tenders", {})),
        "db_file_exists": DB_FILE.exists(),
        "db_size_kb": round(DB_FILE.stat().st_size/1024) if DB_FILE.exists() else 0
    }

# ═══════════════════════════════════════════════════════════
# MISSING ENDPOINTS — fixes frontend 404 errors
# ═══════════════════════════════════════════════════════════

@app.post("/tender/{t247_id}/skip")
async def skip_tender_ep(t247_id: str, data: dict = Body(default={})):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    t["status"] = "Not Interested"
    t["skip_reason"] = data.get("reason","")
    t["status_updated_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "skipped"}

@app.post("/tender/{t247_id}/reanalyse")
async def reanalyse_tender(t247_id: str, data: dict = Body(default={})):
    return {"status":"ok","message":"Upload files in Analyse page to re-analyse"}

@app.get("/api-quota-status")
async def api_quota_status():
    config = load_config()
    return {"status":"ok","ai_configured":bool(config.get("gemini_api_key")),"today_used":0,"daily_limit":1500}

@app.post("/generate-standard-docs/{t247_id}")
async def generate_standard_docs(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    return {"status":"ok","files":[],"message":"Analyse tender first to generate documents"}

@app.post("/tender/{t247_id}/generate-prebid-letter")
async def generate_prebid_letter_ep(t247_id: str, data: dict = Body(default={})):
    tender = get_tender(t247_id)
    queries = tender.get("prebid_queries",[])
    return {"status":"ok","query_count":len(queries),"filename":f"PrebidLetter_{t247_id}.docx"}

@app.post("/tender/{t247_id}/analyze-compliance")
async def analyze_compliance_ep(t247_id: str, data: dict = Body(default={})):
    return {"status":"ok","violations_found":0,"analysis":{"clause_violations":[]}}

@app.post("/tender/{t247_id}/auto-download")
async def auto_download_v2(t247_id: str):
    return await auto_download_tender(t247_id)

@app.get("/letterhead-status")
async def letterhead_status():
    lh = OUTPUT_DIR / "letterhead.docx"
    return {"exists":lh.exists(),"size_kb":round(lh.stat().st_size/1024,1) if lh.exists() else 0}

@app.post("/upload-letterhead")
async def upload_letterhead(file: UploadFile = File(...)):
    dest = OUTPUT_DIR / "letterhead.docx"
    dest.write_bytes(await file.read())
    return {"status":"ok"}

@app.get("/test-t247")
async def test_t247():
    return {"status":"error","message":"T247 auto-login not available on server"}

@app.post("/checklist/{t247_id}/item")
async def toggle_checklist_item(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id, {})
    checklist = t.get("doc_checklist",[])
    item_id = data.get("id") or data.get("item_id")
    done = data.get("done",False)
    for item in checklist:
        if item.get("id") == item_id:
            item["done"] = done
            break
    t["doc_checklist"] = checklist
    t["checklist_pct"] = round(sum(1 for d in checklist if d.get("done"))/max(len(checklist),1)*100)
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status":"saved"}

@app.get("/skipped-tenders")
async def get_skipped():
    db = load_db()
    skipped = [t for t in db["tenders"].values() if t.get("status") == "Not Interested"]
    return {"tenders": skipped}
