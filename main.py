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

def _v(field_val, default=""):
    """Extract plain string value from snapshot field.
    Snapshot fields are now {value, clause_ref, page_no} dicts or plain strings.
    """
    if isinstance(field_val, dict):
        return str(field_val.get("value", "") or default)
    if field_val is None:
        return default
    return str(field_val)

def sanitize_tender_data(data: dict) -> dict:
    """
    Walk the tender_data dict after AI analysis and replace all None values
    with empty strings/lists so downstream code never hits None + str errors.
    Also flattens snapshot fields from {value, clause_ref, page_no} to plain strings
    for storage in tenders DB (the structured data stays in the response to frontend).
    """
    STRING_FIELDS = [
        "tender_no","tender_id","org_name","dept_name","tender_name","portal",
        "tender_type","mode_of_selection","no_of_covers","bid_start_date",
        "bid_submission_date","bid_opening_date","commercial_opening_date",
        "prebid_meeting","prebid_query_date","estimated_cost","tender_fee","emd",
        "emd_exemption","performance_security","contract_period","bid_validity",
        "jv_allowed","location","contact","technology_mandatory","scope_background",
        "corrigendum_note","verdict","ai_warning","ld_rate","go_live_deadline",
        "total_project_duration","phase_a_duration","phase_b_duration",
        "tq_min_qualifying_score","tq_total_marks","tq_nascent_estimated_total",
        "prebid_email_subject","prebid_query_format_used","confidence_level",
        "pbg_details","ip_ownership","exit_clause","advance_payment","retention_money",
        "phase_a_total_percent","phase_b_total_percent",
    ]
    LIST_FIELDS = [
        "pq_criteria","tq_criteria","scope_items","scope_sections","key_integrations",
        "payment_terms","payment_schedule","penalty_clauses","notes","submission_checklist",
        "work_schedule","project_matches","action_items","key_strengths","key_risks",
        "hard_disqualifiers","prebid_queries","key_personnel","corrigendum_files",
        "files_processed","quality_flags",
    ]
    for field in STRING_FIELDS:
        val = data.get(field)
        if val is None:
            data[field] = ""
        elif isinstance(val, dict):
            # Snapshot field — keep structured form for response but also set plain value
            data[field] = val  # keep dict for frontend display
        elif not isinstance(val, str):
            data[field] = str(val)
    for field in LIST_FIELDS:
        val = data.get(field)
        if val is None:
            data[field] = []
        elif not isinstance(val, list):
            data[field] = []
    # Sanitize None values inside list items
    for field in LIST_FIELDS:
        items = data.get(field, [])
        for item in items:
            if isinstance(item, dict):
                for k, v in list(item.items()):
                    if v is None:
                        item[k] = "" if k not in ("done",) else False
    return data


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

# Initialise DB file if not present so sync-drive never fails
if not DB_FILE.exists():
    DB_FILE.write_text(json.dumps({"tenders": {}}, indent=2), encoding="utf-8")


# ── STARTUP ───────────────────────────────────────────────────

async def _drive_warm_sync():
    """Run Drive reads after startup so health checks are not blocked."""
    # Load tenders DB from Drive
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

    # Load profile from Drive (so UI edits survive restarts)
    try:
        profile_ok = await asyncio.wait_for(
            asyncio.to_thread(load_from_drive, PROFILE_FILE, "nascent_profile.json"),
            timeout=10,
        )
        if profile_ok:
            print("Loaded nascent_profile.json from Google Drive")
        else:
            print("No profile in Drive — using repo default")
    except Exception as e:
        print(f"Profile Drive load skipped: {e}")

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
    # Always ensure tenders key exists
    if "tenders" not in db:
        db["tenders"] = {}
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(db, indent=2, default=str)
    DB_FILE.write_text(text, encoding="utf-8")
    # Only sync to Drive if file actually has content
    try:
        if drive_available() and len(db.get("tenders", {})) >= 0:
            save_to_drive(DB_FILE)
    except Exception as e:
        print(f"Drive save warning: {e}")


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
    if not _v(tender_data.get("tender_no")):
        flags.append("Tender number missing")
    if not _v(tender_data.get("org_name")):
        flags.append("Organization name missing")
    if not _v(tender_data.get("bid_submission_date")):
        flags.append("Bid submission date missing")
    if not _v(tender_data.get("emd")):
        flags.append("EMD not found")
    if not _v(tender_data.get("estimated_cost")):
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
    # Load Nascent projects for matching
    try:
        profile = json.loads(PROFILE_FILE.read_text(encoding="utf-8")) if PROFILE_FILE.exists() else {}
        if not profile:
            profile = json.loads((BASE_DIR / "nascent_profile.json").read_text(encoding="utf-8"))
        nascent_projects = profile.get("projects", [])
    except Exception:
        nascent_projects = []

    # Build project matching index: category → projects
    cat_map = {}
    for proj in nascent_projects:
        for cat in proj.get("similar_categories", []) + proj.get("domains", []):
            c = cat.lower().strip()
            if c:
                if c not in cat_map:
                    cat_map[c] = []
                cat_map[c].append({
                    "name": proj.get("name", ""),
                    "value": proj.get("value_lakhs", 0) / 100,
                    "status": proj.get("status", ""),
                    "wo_no": proj.get("wo_no", ""),
                    "loi": proj.get("loi_received", ""),
                    "cc": proj.get("completion_cert", ""),
                })

    stats = {
        "total": 0, "bid": 0, "no_bid": 0, "conditional": 0,
        "review": 0, "analysed": 0, "deadline_today": 0, "deadline_3days": 0,
        "won": 0, "submitted": 0,
    }

    enriched = []
    for t in tenders:
        dl = days_left(t.get("deadline", "") or t.get("bid_submission_date", ""))
        item = dict(t)
        item["days_left"] = dl
        item["_days_left_sort"] = dl

        # Auto project matching from brief + eligibility text
        brief_lower = (item.get("brief","") + " " + item.get("org_name","") + " " +
                       item.get("eligibility","") + " " + item.get("tender_name","")).lower()

        matches = []
        partial = []
        for cat, projs in cat_map.items():
            if cat in brief_lower:
                for proj in projs:
                    entry = {**proj, "matched_on": cat}
                    if entry not in matches:
                        matches.append(entry)

        # Score: STRONG if 2+ categories match, PARTIAL if 1
        seen_names = {}
        for m in matches:
            n = m["name"]
            seen_names[n] = seen_names.get(n, 0) + 1
        strong_matches = [m for m in matches if seen_names.get(m["name"],0) >= 2]
        partial_matches = [m for m in matches if seen_names.get(m["name"],0) == 1 and m not in strong_matches]

        # Deduplicate by project name
        def dedup(lst):
            seen = set()
            out = []
            for m in lst:
                if m["name"] not in seen:
                    seen.add(m["name"])
                    out.append(m)
            return out

        item["_strong_matches"] = dedup(strong_matches)[:4]
        item["_partial_matches"] = dedup(partial_matches)[:3]
        item["_match_count"] = len(dedup(strong_matches)) + len(dedup(partial_matches))

        # Use AI project_matches if we have them (more accurate)
        if item.get("project_matches"):
            ai_matches = item["project_matches"]
            item["_strong_matches"] = [m for m in ai_matches if m.get("strength")=="STRONG"][:3]
            item["_partial_matches"] = [m for m in ai_matches if m.get("strength")!="STRONG"][:2]
            item["_match_count"] = len(ai_matches)

        enriched.append(item)
        stats["total"] += 1
        verdict = item.get("verdict","")
        if verdict == "BID": stats["bid"] += 1
        elif verdict == "NO-BID": stats["no_bid"] += 1
        elif verdict == "CONDITIONAL": stats["conditional"] += 1
        elif verdict == "REVIEW": stats["review"] += 1
        if item.get("bid_no_bid_done"): stats["analysed"] += 1
        if item.get("outcome") == "Won" or item.get("status") == "Won": stats["won"] += 1
        if item.get("status") == "Submitted": stats["submitted"] += 1
        if dl == 0: stats["deadline_today"] += 1
        elif 0 < dl <= 3: stats["deadline_3days"] += 1

    tenders_sorted = sorted(enriched, key=lambda t: t.get("_days_left_sort", 999))
    for t in tenders_sorted:
        t.pop("_days_left_sort", None)
    return {"stats": stats, "tenders": tenders_sorted, "nascent_projects": nascent_projects}


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
            passed = prebid_passed(_v(tender_data.get("prebid_query_date", "")))
            ai_result = analyze_with_gemini(all_text, passed)
            # Track API usage for quota display
            try:
                quota_file = OUTPUT_DIR / "api_quota.json"
                today_str = __import__('datetime').date.today().isoformat()
                quota = {"date": today_str, "calls": 0}
                if quota_file.exists():
                    q = json.loads(quota_file.read_text())
                    if q.get("date") == today_str:
                        quota = q
                quota["calls"] = quota.get("calls", 0) + 8  # ~8 Gemini calls per analysis
                quota_file.write_text(json.dumps(quota))
            except Exception:
                pass
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

        # Always sanitize — removes None values, ensures all fields are proper types
        tender_data = sanitize_tender_data(tender_data)

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
        safe_no = re.sub(r'[^\w\-]', '_', _v(tender_data.get("tender_no"), "Report"))[:50]
        output_filename = f"BidNoBid_{safe_no}.docx"
        generator.generate(tender_data, str(OUTPUT_DIR / output_filename))

        if t247_id:
            db_record = get_tender(t247_id)
            db_record.update({
                "t247_id": t247_id,
                "tender_no": _v(tender_data.get("tender_no")),
                "org_name": _v(tender_data.get("org_name")),
                "tender_name": _v(tender_data.get("tender_name")),
                "bid_submission_date": _v(tender_data.get("bid_submission_date")),
                "emd": _v(tender_data.get("emd")),
                "estimated_cost": _v(tender_data.get("estimated_cost")),
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
    # Save to both runtime location AND repo location
    profile_json = json.dumps(data, indent=2, ensure_ascii=False)
    PROFILE_FILE.parent.mkdir(exist_ok=True, parents=True)
    PROFILE_FILE.write_text(profile_json, encoding="utf-8")
    # Also overwrite the repo copy so it survives redeploys via GitHub
    repo_profile = BASE_DIR / "nascent_profile.json"
    try:
        repo_profile.write_text(profile_json, encoding="utf-8")
    except Exception as e:
        print(f"Repo profile write failed (OK if read-only): {e}")
    # Sync to Google Drive
    drive_saved = False
    try:
        if drive_available():
            drive_saved = save_to_drive(PROFILE_FILE, "nascent_profile.json")
            if drive_saved:
                print("✅ Profile saved to Google Drive")
    except Exception as e:
        print(f"Profile Drive save failed (local save still OK): {e}")
    return {"status": "saved", "drive": drive_saved, "path": str(PROFILE_FILE)}


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
        creds_set = bool(os.environ.get("GDRIVE_CREDENTIALS", "").strip())
        folder_set = bool(os.environ.get("GDRIVE_FOLDER_ID", "").strip())
        if not creds_set:
            msg = "GDRIVE_CREDENTIALS not set in Render environment variables"
        elif not folder_set:
            msg = "GDRIVE_FOLDER_ID not set in Render environment variables"
        else:
            msg = "Google Drive not connected — check credentials"
        return JSONResponse({"status": "error", "message": msg}, status_code=400)
    
    db = load_db()
    if not DB_FILE.exists():
        DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        DB_FILE.write_text(json.dumps({"tenders": {}}, indent=2), encoding="utf-8")
    
    ok = save_to_drive(DB_FILE)
    count = len(db.get("tenders", {}))
    if ok:
        # Also sync profile
        try:
            if PROFILE_FILE.exists():
                save_to_drive(PROFILE_FILE, "nascent_profile.json")
        except Exception:
            pass
        return {"status": "ok", "message": f"Synced {count} tenders + profile to Drive"}
    return JSONResponse({"status": "error", "message": "Drive save failed — check Render logs"}, status_code=500)

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
        "tenders_in_memory": tenders_count,
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
        pq_label = ""
        verdict_label = ""
        if pq_match and "idx" in dir():
            try:
                pq_label = f"PQ {idx + 1} updated to {new_status_title}"
            except Exception:
                pass
        if verdict_match and correction_applied.get("type") == "verdict":
            verdict_label = f"Verdict updated to {correction_applied.get('new_verdict', '')}"
        msg_parts = [p for p in [pq_label, verdict_label] if p]
        response["response"] = "Done. " + " | ".join(msg_parts) + ". Correction saved and preview will refresh."
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




# ── RECLASSIFY ALL ────────────────────────────────────────────

@app.post("/reclassify-all")
async def reclassify_all():
    """Re-run bid rules on every tender that has not been manually analysed."""
    from excel_processor import classify_tender, invalidate_rules_cache
    invalidate_rules_cache()
    db = load_db()
    bid = no_bid = conditional = review = 0
    for tid, t in db["tenders"].items():
        if t.get("bid_no_bid_done"):
            continue  # don't overwrite AI-analysed verdicts
        brief       = t.get("brief", "")
        cost_raw    = float(t.get("estimated_cost_raw", 0) or 0)
        eligibility = t.get("eligibility", "")
        checklist   = t.get("checklist", "")
        result = classify_tender(brief, cost_raw, eligibility, checklist)
        t["verdict"]       = result["verdict"]
        t["verdict_color"] = result.get("verdict_color", "")
        t["reason"]        = result.get("reason", "")
        if result["verdict"] == "BID":        bid += 1
        elif result["verdict"] == "NO-BID":   no_bid += 1
        elif result["verdict"] == "CONDITIONAL": conditional += 1
        else:                                 review += 1
    save_db(db)
    return {
        "status": "ok",
        "count": bid + no_bid + conditional + review,
        "bid": bid, "no_bid": no_bid,
        "conditional": conditional, "review": review,
    }


# ── TENDER SKIP / RESTORE ─────────────────────────────────────

@app.post("/tender/{t247_id}/skip")
async def skip_tender(t247_id: str, data: dict = Body(...)):
    db = load_db()
    t = db["tenders"].get(t247_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    t["status"] = "Not Interested"
    t["skip_reason"] = data.get("reason", "Not interested")
    t["status_updated_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "skipped"}


@app.post("/tender/{t247_id}/restore")
async def restore_tender(t247_id: str):
    db = load_db()
    t = db["tenders"].get(t247_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    t["status"] = "Identified"
    t.pop("skip_reason", None)
    t["status_updated_at"] = datetime.now().isoformat()
    db["tenders"][t247_id] = t
    save_db(db)
    return {"status": "restored"}


# ── CHECKLIST ITEM TOGGLE ─────────────────────────────────────

@app.post("/checklist/{t247_id}/item")
async def toggle_checklist_item(t247_id: str, data: dict = Body(...)):
    """Toggle a single checklist item done/undone without replacing the whole list."""
    db = load_db()
    t = db["tenders"].get(t247_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    item_id   = str(data.get("id", ""))
    done      = bool(data.get("done", False))
    checklist = t.get("doc_checklist", [])
    updated   = False
    for item in checklist:
        if str(item.get("id", "")) == item_id or str(item.get("label", "")) == item_id:
            item["done"] = done
            if done:
                item["status"] = "Done"
            elif item.get("status") == "Done":
                item["status"] = "Pending"
            updated = True
            break
    if updated:
        done_count = sum(1 for i in checklist if i.get("done"))
        total = max(len(checklist), 1)
        t["doc_checklist"] = checklist
        t["checklist_pct"] = round(done_count / total * 100)
        db["tenders"][t247_id] = t
        save_db(db)
    return {"status": "ok", "updated": updated}


# ── PRE-BID LETTER GENERATION ─────────────────────────────────

@app.post("/tender/{t247_id}/generate-prebid-letter")
async def generate_prebid_letter(t247_id: str):
    """Generate a pre-bid queries Word document for a tender."""
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    queries = tender.get("prebid_queries", [])
    if not queries:
        from prebid_generator import generate_prebid_queries
        queries = generate_prebid_queries(tender)
    if not queries:
        raise HTTPException(400, "No pre-bid queries available. Analyse the tender first.")
    tender["prebid_queries"] = queries
    generator = BidDocGenerator()
    safe_no = re.sub(r'[^\w\-]', '_', _v(tender.get("tender_no"), t247_id))[:40]
    filename = f"PreBid_{safe_no}.docx"
    output_path = OUTPUT_DIR / filename
    # Generate a focused pre-bid letter document
    # We reuse BidDocGenerator but pass prebid_only mode via a flag
    tender["_prebid_letter_only"] = True
    try:
        generator.generate(tender, str(output_path))
    finally:
        tender.pop("_prebid_letter_only", None)
    if not output_path.exists():
        raise HTTPException(500, "Document generation failed")
    return {
        "status": "ok",
        "filename": filename,
        "download_url": f"/download/{filename}",
        "query_count": len(queries),
    }


# ── SUBMISSION PACKAGE (alias for /submission-package) ────────

@app.post("/generate-docs/{t247_id}")
async def generate_docs(t247_id: str):
    """Alias route called by frontend submission page."""
    if not _has_submission_generator:
        # Fallback: generate the bid analysis report as the submission document
        tender = get_tender(t247_id)
        if not tender:
            raise HTTPException(404, "Tender not found")
        generator = BidDocGenerator()
        safe_no = re.sub(r'[^\w\-]', '_', tender.get("tender_no", t247_id))[:40]
        filename = f"BidNoBid_{safe_no}.docx"
        output_path = OUTPUT_DIR / filename
        generator.generate(tender, str(output_path))
        return {
            "status": "ok",
            "files": [{"name": "Bid Analysis Report", "filename": filename}],
            "zip_url": None,
            "message": f"Generated 1 document",
        }
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    result = generate_submission_package(tender, OUTPUT_DIR)
    if result.get("error"):
        raise HTTPException(500, result["error"])
    return {
        "status": "ok",
        "files": result.get("files", []),
        "zip_url": f"/download-zip/{result['zip_file']}" if result.get("zip_file") else None,
        "message": result.get("message", "Package generated"),
    }


# ── TEST T247 CONNECTION ──────────────────────────────────────

@app.get("/test-t247")
async def test_t247():
    return {
        "status": "info",
        "message": "T247 auto-download is not available on Render free tier. "
                   "Visit tender247.com, download the ZIP manually, then upload in Analyse page.",
    }


# ── SHEET STATUS (Google Sheets — not used, stub for UI) ──────

@app.get("/sheet-status")
async def sheet_status():
    return {
        "connected": False,
        "reason": "Google Sheets integration not configured. Using Google Drive for persistence.",
        "tabs": [],
        "required_tabs": {},
        "optional_tabs": {},
    }

# ── GUIDELINES LIBRARY ──────────────────────────────────────

@app.get("/guidelines")
async def get_guidelines():
    try:
        from guidelines_library import get_all_guidelines
        return {"guidelines": get_all_guidelines()}
    except Exception as e:
        return {"guidelines": [], "error": str(e)}

@app.post("/guidelines")
async def add_guideline(data: dict = Body(...)):
    try:
        from guidelines_library import add_custom_guideline
        gl = add_custom_guideline(
            name=data.get("name",""),
            short=data.get("short",""),
            category=data.get("category","Custom"),
            applies_to=data.get("applies_to",[]),
            key_provisions=data.get("key_provisions",[]),
            authority=data.get("authority",""),
            cite_as=data.get("cite_as",""),
        )
        return {"status":"saved","guideline":gl}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/guidelines/search")
async def search_guidelines(q: str = ""):
    try:
        from guidelines_library import find_relevant_guidelines
        results = find_relevant_guidelines(q)
        return {"guidelines": results, "query": q}
    except Exception as e:
        return {"guidelines":[], "error": str(e)}


# ── HEALTH / DIAGNOSTICS ──────────────────────────────────────

@app.get("/health")
async def health():
    config = load_config()
    db = load_db()
    all_keys = config.get("gemini_api_keys", [])
    primary = config.get("gemini_api_key", "")
    if primary and primary not in all_keys:
        all_keys = [primary] + all_keys
    key_count = len([k for k in all_keys if k and len(k) > 20])
    # Gemini free tier: 1500 req/day per key, resets at midnight UTC (5:30 AM IST)
    # We track calls in a simple daily counter file
    quota_file = OUTPUT_DIR / "api_quota.json"
    quota = {"date": "", "calls": 0}
    try:
        if quota_file.exists():
            quota = json.loads(quota_file.read_text())
    except Exception:
        pass
    today_str = __import__('datetime').date.today().isoformat()
    if quota.get("date") != today_str:
        quota = {"date": today_str, "calls": 0}
    calls_today = quota.get("calls", 0)
    daily_limit = 1500 * max(key_count, 1)
    remaining = max(0, daily_limit - calls_today)
    return {
        "status": "ok",
        "version": "7.0",
        "ai_configured": bool(config.get("gemini_api_key")),
        "gemini_key_count": key_count,
        "drive_sync": drive_available(),
        "drive_connected": drive_available(),
        "ocr_available": ocr_available(),
        "tenders_loaded": len(db.get("tenders", {})),
        "vault_local_files": len(list(VAULT_DIR.iterdir())) if VAULT_DIR.exists() else 0,
        "api_calls_today": calls_today,
        "api_daily_limit": daily_limit,
        "api_remaining": remaining,
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
