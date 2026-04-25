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
import asyncio
import uuid
from pathlib import Path
from datetime import datetime, date
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Request, BackgroundTasks, Depends
from typing import List
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from extractor import TenderExtractor, read_document
from doc_generator import BidDocGenerator
from nascent_checker import NascentChecker
from ai_analyzer import analyze_with_gemini, merge_results, load_config, save_config, get_all_api_keys, call_gemini
try:
    from ai_analyzer import analyze_with_gemini_parallel
    PARALLEL_ANALYST_AVAILABLE = True
except ImportError:
    PARALLEL_ANALYST_AVAILABLE = False
    analyze_with_gemini_parallel = None
try:
    from core.api_pool import get_pool, get_slots, refresh_pool
    API_POOL_AVAILABLE = True
except ImportError:
    API_POOL_AVAILABLE = False
try:
    import doc_editor
    DOC_EDITOR_AVAILABLE = True
except ImportError:
    DOC_EDITOR_AVAILABLE = False
    doc_editor = None
from excel_processor import process_excel
from prebid_generator import generate_prebid_queries
from chatbot import process_message, load_history
from gdrive_sync import init_drive, save_to_drive, load_from_drive, is_available as drive_available
from tracker import (get_deadline_alerts, get_pipeline_stats,
                     get_win_loss_stats, generate_doc_checklist,
                     PIPELINE_STAGES, STAGE_COLORS)
from core.config import settings
from core.database import engine, Base, get_db_session
from core.models import User, WorkItem, TenderRecord, TenderSource, IngestedTender, ClauseEvidence
from core.auth import hash_password, verify_password, create_access_token, decode_token
from core.worker import work_queue
from core.ingestion import registry as ingestion_registry, JsonApiSource, CpppFeedSource, StatePortalTableSource
try:
    from submission_generator import generate_submission_package
    SUBMISSION_GEN_AVAILABLE = True
except Exception:
    SUBMISSION_GEN_AVAILABLE = False
    def generate_submission_package(tender, output_dir):
        return {"error": "submission_generator not available"}
try:
    from pdf_merger import merge_submission_package, get_doc_order_preview
    PDF_MERGE_AVAILABLE = True
except Exception:
    PDF_MERGE_AVAILABLE = False
    def merge_submission_package(*a, **k):
        return {"status": "error", "errors": ["pdf_merger not available"]}
    def get_doc_order_preview(*a, **k):
        return []

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
DRAFTS_DIR = OUTPUT_DIR / "drafts"
LATEST_EXCEL_FILE = OUTPUT_DIR / "latest_tenders_import.xlsx"
DRAFTS_DIR.mkdir(exist_ok=True, parents=True)

# ── FIX 2: Threading lock for safe concurrent DB access ─────────────────────
_db_lock = threading.RLock()
db_lock = _db_lock
import time as _time
_last_drive_restore = 0.0  # rate-limit Drive calls in load_db to once per minute

# ── Background Job Store — file-based (survives OOM restarts) ──────────────
JOBS_DIR = OUTPUT_DIR / "jobs"
JOBS_DIR.mkdir(exist_ok=True, parents=True)
_jobs_lock = threading.Lock()

def _job_file(job_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "", job_id)
    return JOBS_DIR / f"{safe}.json"

def _set_job(job_id: str, **kwargs):
    with _jobs_lock:
        jf = _job_file(job_id)
        try:
            existing = json.loads(jf.read_text()) if jf.exists() else {}
        except Exception:
            existing = {}
        existing.update(kwargs)
        # Don't persist huge base64 doc inline — store separately
        doc_b64 = existing.pop("doc_b64", None)
        try:
            jf.write_text(json.dumps(existing))
        except Exception:
            pass
        if doc_b64:
            try:
                (JOBS_DIR / f"{re.sub(r'[^a-zA-Z0-9_\\-]', '', job_id)}.b64").write_text(doc_b64)
            except Exception:
                pass

def _get_job(job_id: str) -> dict:
    with _jobs_lock:
        jf = _job_file(job_id)
        if not jf.exists():
            return {}
        try:
            data = json.loads(jf.read_text())
            # Re-attach b64 if result is being fetched and file exists
            b64f = JOBS_DIR / f"{re.sub(r'[^a-zA-Z0-9_\\-]', '', job_id)}.b64"
            if data.get("status") == "done" and b64f.exists():
                if data.get("result"):
                    data["result"]["doc_b64"] = b64f.read_text()
            return data
        except Exception:
            return {}



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
auth_scheme = HTTPBearer(auto_error=False)


def _current_user(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if not credentials:
        return {}
    payload = decode_token(credentials.credentials)
    return payload or {}


def _require_role(credentials: HTTPAuthorizationCredentials | None, allowed_roles: set[str]) -> dict:
    user = _current_user(credentials)
    if not user:
        raise HTTPException(401, "Authentication required")
    if user.get("role") not in allowed_roles:
        raise HTTPException(403, "Insufficient role")
    return user


def _handle_tender_scoring(payload: dict) -> dict:
    """Initial worker handler; can evolve into full ML scoring."""
    risk_score = float(payload.get("risk_score", 0) or 0)
    margin_score = float(payload.get("margin_score", 0) or 0)
    fit_score = float(payload.get("fit_score", 0) or 0)
    win_probability = max(0.0, min(100.0, (margin_score * 0.35 + fit_score * 0.45 + (100 - risk_score) * 0.20)))
    return {
        "win_probability": round(win_probability, 2),
        "recommended": "BID" if win_probability >= 65 else "CONDITIONAL" if win_probability >= 45 else "NO-BID",
    }


def _extract_clause_candidates(text_blob: str) -> list[dict]:
    text_blob = text_blob or ""
    patterns = {
        "emd": r"(?:emd|earnest money deposit)[^.\n]{0,140}",
        "turnover": r"(?:turnover|annual turnover)[^.\n]{0,180}",
        "experience": r"(?:experience|work order|completion certificate)[^.\n]{0,200}",
        "security": r"(?:performance security|pbg|bank guarantee)[^.\n]{0,180}",
        "timeline": r"(?:timeline|completion period|contract period|delivery period)[^.\n]{0,180}",
    }
    found = []
    for ctype, pattern in patterns.items():
        for m in re.finditer(pattern, text_blob, flags=re.IGNORECASE):
            snippet = m.group(0).strip()
            if len(snippet) < 12:
                continue
            found.append({
                "clause_type": ctype,
                "clause_text": snippet[:500],
                "evidence_text": snippet[:500],
                "confidence": 0.55,
            })
            if len(found) >= 100:
                return found
    return found


def _handle_ingestion_sync(payload: dict) -> dict:
    if not settings.database_enabled:
        raise RuntimeError("DATABASE_URL is required")
    source_name = str(payload.get("source_name", "")).strip()
    endpoint = str(payload.get("endpoint", "")).strip()
    source_type = str(payload.get("source_type", "json_api")).strip().lower()
    if not source_name:
        raise RuntimeError("source_name is required")
    if source_type == "json_api":
        source = JsonApiSource(endpoint=endpoint)
    elif source_type == "cppp_feed":
        source = CpppFeedSource(endpoint=endpoint)
    elif source_type == "state_portal_table":
        source = StatePortalTableSource(endpoint=endpoint)
    else:
        raise RuntimeError(f"unsupported source_type: {source_type}")
    items = source.fetch()
    inserted = 0
    with get_db_session() as db:
        for row in items[:500]:
            ext_id = str(row.get("id") or row.get("external_id") or row.get("tender_id") or "")
            if not ext_id:
                ext_id = f"{source_name}-{uuid.uuid4().hex[:12]}"
            exists = (
                db.query(IngestedTender)
                .filter(IngestedTender.source_name == source_name, IngestedTender.external_id == ext_id)
                .first()
            )
            if exists:
                continue
            title = str(row.get("title") or row.get("brief") or row.get("tender_name") or "")
            org = str(row.get("org_name") or row.get("organization") or "")
            deadline = str(row.get("deadline") or row.get("bid_submission_date") or "")
            ref = str(row.get("ref_no") or row.get("reference_no") or "")
            raw_text = str(row.get("raw_text") or row.get("description") or "")
            db.add(
                IngestedTender(
                    source_name=source_name,
                    external_id=ext_id,
                    title=title[:1000],
                    org_name=org[:255],
                    deadline=deadline[:120],
                    reference_no=ref[:255],
                    payload_json=json.dumps(row, default=str),
                    raw_text=raw_text[:20000],
                )
            )
            inserted += 1
    return {"inserted": inserted, "fetched": len(items), "source_name": source_name, "source_type": source_type}


def _handle_clause_index(payload: dict) -> dict:
    if not settings.database_enabled:
        raise RuntimeError("DATABASE_URL is required")
    t247_id = str(payload.get("t247_id", "")).strip()
    source_record_id = int(payload.get("source_record_id") or 0)
    if not t247_id and not source_record_id:
        raise RuntimeError("t247_id or source_record_id is required")

    if t247_id:
        tender = get_tender(t247_id)
        text_blob = " ".join([
            str(tender.get("eligibility", "")),
            str(tender.get("checklist", "")),
            str(tender.get("raw_text", "")),
            str(tender.get("brief", "")),
        ])
    else:
        with get_db_session() as db:
            row = db.query(IngestedTender).filter(IngestedTender.id == source_record_id).first()
            if not row:
                raise RuntimeError("source record not found")
            text_blob = " ".join([row.title or "", row.raw_text or "", row.payload_json or ""])

    clauses = _extract_clause_candidates(text_blob)
    with get_db_session() as db:
        if t247_id:
            db.query(ClauseEvidence).filter(ClauseEvidence.t247_id == t247_id).delete()
        elif source_record_id:
            db.query(ClauseEvidence).filter(ClauseEvidence.source_record_id == source_record_id).delete()
        for c in clauses:
            db.add(
                ClauseEvidence(
                    t247_id=t247_id,
                    source_record_id=source_record_id,
                    clause_type=c["clause_type"],
                    clause_text=c["clause_text"],
                    evidence_text=c["evidence_text"],
                    confidence=c["confidence"],
                )
            )
    return {"indexed": len(clauses), "t247_id": t247_id, "source_record_id": source_record_id}

# ── FIX 4: Lifespan replaces deprecated @app.on_event("startup") ────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Clean up old job files on startup
    import time as _startup_time
    try:
        if JOBS_DIR.exists():
            cutoff = _startup_time.time() - 7200  # 2 hours
            for jf in JOBS_DIR.glob("*.json"):
                try:
                    if jf.stat().st_mtime < cutoff:
                        jf.unlink(missing_ok=True)
                        b64f = jf.with_suffix(".b64")
                        if b64f.exists(): b64f.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass
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
    if settings.database_enabled and engine is not None:
        try:
            Base.metadata.create_all(bind=engine)
            work_queue.register("tender_scoring", _handle_tender_scoring)
            work_queue.register("ingestion_sync", _handle_ingestion_sync)
            work_queue.register("clause_index", _handle_clause_index)
            work_queue.start()
            with get_db_session() as db:
                if not db.query(TenderSource).first():
                    db.add(TenderSource(name="manual", source_type="manual", base_url="", is_active=True, config_json="{}"))
            print("Postgres connected and worker started")
        except Exception as e:
            print(f"Database bootstrap failed: {e}")
    else:
        print("DATABASE_URL not set — running in file-db mode")
    # Restore profile from Drive (always, to get latest edits)
    try:
        if drive_available():
            _prof_local = BASE_DIR / "nascent_profile.json"
            _prof_drive = OUTPUT_DIR / "nascent_profile.json"
            if load_from_drive(_prof_drive, filename="nascent_profile.json"):
                _prof_bytes = _prof_drive.read_bytes()
                if len(_prof_bytes) > 50:
                    _prof_local.write_bytes(_prof_bytes)
                    print("✅ Profile restored from Drive")
    except Exception:
        pass

    # Background Drive sync every 5 minutes — safety net if a save_db call was skipped
    def _periodic_drive_sync():
        _time.sleep(90)  # wait for startup to settle
        while True:
            try:
                if drive_available() and DB_FILE.exists():
                    raw = DB_FILE.read_bytes()
                    if len(raw) > 10 and json.loads(raw).get("tenders"):
                        save_to_drive(DB_FILE)
            except Exception as _pe:
                print(f"⚠️ Periodic Drive sync error: {_pe}")
            _time.sleep(300)

    threading.Thread(target=_periodic_drive_sync, daemon=True, name="drive-sync").start()

    yield
    # Shutdown: nothing needed
    try:
        work_queue.stop()
    except Exception:
        pass

app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

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
    global _last_drive_restore
    # Fast path: local disk has data
    with _db_lock:
        if DB_FILE.exists():
            try:
                raw = DB_FILE.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                if parsed.get("tenders"):
                    return parsed
            except Exception:
                pass
    # Slow path: Drive restore — rate-limited to once per 60s to stop log spam
    now = _time.time()
    if drive_available() and (now - _last_drive_restore) > 60.0:
        _last_drive_restore = now
        try:
            DB_FILE.parent.mkdir(exist_ok=True, parents=True)
            if load_from_drive(DB_FILE):
                data = json.loads(DB_FILE.read_text(encoding="utf-8"))
                if data.get("tenders"):
                    print(f"🔄 load_db: restored {len(data['tenders'])} tenders from Drive")
                    return data
        except Exception:
            pass
    return {"tenders": {}}

def save_db(db: dict):
    with _db_lock:
        DB_FILE.parent.mkdir(exist_ok=True, parents=True)
        DB_FILE.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")
    try:
        ok = save_to_drive(DB_FILE)
        if not ok and drive_available():
            print(f"⚠️ Drive sync skipped/failed — {len(db.get('tenders', {}))} tenders on disk only")
    except Exception as _e:
        print(f"⚠️ Drive sync exception: {_e}")

def get_tender(t247_id: str) -> dict:
    return load_db()["tenders"].get(str(t247_id), {})

def save_tender(t247_id: str, data: dict):
    db = load_db()
    db["tenders"][str(t247_id)] = data
    save_db(db)

def _safe_doc_type(doc_type: str) -> str:
    return re.sub(r"[^\w\-]", "_", (doc_type or "analysis").strip().lower())[:40] or "analysis"

def _draft_path(t247_id: str, doc_type: str = "analysis") -> Path:
    safe_tid = re.sub(r"[^\w\-]", "_", str(t247_id))[:40] or "unknown"
    return DRAFTS_DIR / f"{safe_tid}_{_safe_doc_type(doc_type)}.md"

def _build_tender_draft(tender: dict, doc_type: str = "analysis") -> str:
    title = tender.get("tender_name") or tender.get("brief") or f"Tender {tender.get('t247_id', '')}"
    verdict = tender.get("verdict", "REVIEW")
    sections = [
        f"# {doc_type.replace('_', ' ').title()} Draft",
        "",
        "## Tender",
        f"- T247 ID: {tender.get('t247_id','')}",
        f"- Reference: {tender.get('ref_no','')}",
        f"- Name: {title}",
        f"- Organization: {tender.get('org_name','')}",
        f"- Deadline: {tender.get('deadline','')}",
        f"- Verdict: {verdict}",
        "",
        "## Executive Summary",
        str(tender.get("reason", "Summary pending.")),
        "",
        "## Key Eligibility",
        str(tender.get("eligibility", "Not available")),
        "",
        "## Compliance & Risk Notes",
        str(tender.get("compliance_notes", tender.get("risk_flags", "Add compliance comments here."))),
        "",
        "## Pre-Bid Queries",
    ]
    queries = tender.get("prebid_queries", []) or []
    if queries:
        for i, q in enumerate(queries, 1):
            qtxt = q.get("query") if isinstance(q, dict) else str(q)
            sections.append(f"{i}. {qtxt}")
    else:
        sections.append("1. No pending pre-bid queries.")
    sections.extend(["", "## Draft Document Body", "Write or edit final content here before download."])
    return "\n".join(sections)

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


@app.get("/health/deep")
async def health_deep():
    db_state = "disabled"
    if settings.database_enabled and engine is not None:
        try:
            with get_db_session() as db:
                db.execute(text("SELECT 1"))
            db_state = "ok"
        except Exception:
            db_state = "error"
    return {
        "status": "ok" if db_state != "error" else "degraded",
        "database": db_state,
        "drive": "ok" if drive_available() else "disabled",
        "worker": "running" if settings.database_enabled else "disabled",
        "sources": ingestion_registry.list_sources(),
    }

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


@app.post("/auth/bootstrap-admin")
async def bootstrap_admin(data: dict = Body(...)):
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required for user auth")
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if len(username) < 3 or len(password) < 8:
        raise HTTPException(400, "Username/password too short")
    with get_db_session() as db:
        if db.query(User).filter(User.username == username).first():
            return {"status": "exists"}
        user = User(username=username, password_hash=hash_password(password), role="admin")
        db.add(user)
    return {"status": "created", "username": username}


@app.post("/auth/login")
async def login(data: dict = Body(...)):
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required for user auth")
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    with get_db_session() as db:
        user = db.query(User).filter(User.username == username, User.is_active == True).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")
        token = create_access_token(subject=user.username, role=user.role)
    return {"access_token": token, "token_type": "bearer", "role": user.role}


@app.get("/auth/me")
async def auth_me(credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    user = _current_user(credentials)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"username": user.get("sub"), "role": user.get("role")}


@app.post("/work-items")
async def create_work_item(
    data: dict = Body(...),
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin", "analyst"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required for work queue")
    work_type = str(data.get("work_type", "")).strip()
    payload = data.get("payload", {})
    if not work_type:
        raise HTTPException(400, "work_type is required")
    with get_db_session() as db:
        item = WorkItem(work_type=work_type, payload_json=json.dumps(payload))
        db.add(item)
        db.flush()
        item_id = item.id
    return {"status": "queued", "id": item_id}


@app.get("/work-items/{item_id}")
async def get_work_item(
    item_id: int,
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin", "analyst", "viewer"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required for work queue")
    with get_db_session() as db:
        item = db.query(WorkItem).filter(WorkItem.id == item_id).first()
        if not item:
            raise HTTPException(404, "Work item not found")
        return {
            "id": item.id,
            "work_type": item.work_type,
            "status": item.status,
            "attempts": item.attempts,
            "payload": json.loads(item.payload_json or "{}"),
            "result": json.loads(item.result_json or "{}"),
            "error": item.error_text,
        }


@app.post("/platform/sync-json-to-postgres")
async def sync_json_to_postgres(credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    _require_role(credentials, {"admin"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required")
    db_data = load_db()
    tenders = db_data.get("tenders", {})
    synced = 0
    with get_db_session() as db:
        for t247_id, t in tenders.items():
            row = db.query(TenderRecord).filter(TenderRecord.t247_id == str(t247_id)).first()
            if not row:
                row = TenderRecord(t247_id=str(t247_id))
                db.add(row)
            row.verdict = str(t.get("verdict", ""))
            row.org_name = str(t.get("org_name", ""))
            row.tender_name = str(t.get("tender_name", t.get("brief", "")))
            row.estimated_cost = str(t.get("estimated_cost", t.get("estimated_cost_cr", "")))
            row.status = str(t.get("status", "Identified"))
            row.payload_json = json.dumps(t, default=str)
            synced += 1
    return {"status": "ok", "synced": synced}


@app.get("/platform/tenders")
async def list_platform_tenders(
    limit: int = 50,
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin", "analyst", "viewer"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required")
    limit = max(1, min(limit, 500))
    with get_db_session() as db:
        rows = (
            db.query(TenderRecord)
            .order_by(TenderRecord.updated_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "items": [
                {
                    "t247_id": r.t247_id,
                    "verdict": r.verdict,
                    "org_name": r.org_name,
                    "tender_name": r.tender_name,
                    "estimated_cost": r.estimated_cost,
                    "win_probability": r.win_probability,
                    "risk_score": r.risk_score,
                    "status": r.status,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else "",
                }
                for r in rows
            ]
        }


@app.get("/platform/sources")
async def list_platform_sources(credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    _require_role(credentials, {"admin", "analyst", "viewer"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required")
    with get_db_session() as db:
        rows = db.query(TenderSource).order_by(TenderSource.name.asc()).all()
        return {
            "items": [
                {
                    "id": r.id,
                    "name": r.name,
                    "source_type": r.source_type,
                    "base_url": r.base_url,
                    "is_active": r.is_active,
                    "config": json.loads(r.config_json or "{}"),
                }
                for r in rows
            ]
        }


@app.post("/platform/sources")
async def upsert_platform_source(
    data: dict = Body(...),
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required")
    name = str(data.get("name", "")).strip()
    source_type = str(data.get("source_type", "json_api")).strip()
    base_url = str(data.get("base_url", "")).strip()
    config = data.get("config", {}) or {}
    if not name:
        raise HTTPException(400, "name is required")
    with get_db_session() as db:
        row = db.query(TenderSource).filter(TenderSource.name == name).first()
        if not row:
            row = TenderSource(name=name, source_type=source_type)
            db.add(row)
        row.source_type = source_type
        row.base_url = base_url
        row.is_active = bool(data.get("is_active", True))
        row.config_json = json.dumps(config, default=str)
    return {"status": "saved", "name": name}


@app.post("/platform/ingestion/run")
async def run_ingestion(
    data: dict = Body(...),
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin", "analyst"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required")
    source_name = str(data.get("source_name", "")).strip()
    if not source_name:
        raise HTTPException(400, "source_name is required")
    with get_db_session() as db:
        src = db.query(TenderSource).filter(TenderSource.name == source_name).first()
        if not src:
            raise HTTPException(404, "source not found")
        payload = {
            "source_name": src.name,
            "source_type": src.source_type,
            "endpoint": src.base_url,
            "config": json.loads(src.config_json or "{}"),
        }
        item = WorkItem(work_type="ingestion_sync", payload_json=json.dumps(payload))
        db.add(item)
        db.flush()
        work_id = item.id
    return {"status": "queued", "work_item_id": work_id}


@app.post("/platform/ingestion/preview")
async def preview_ingestion(
    data: dict = Body(...),
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin", "analyst"})
    source_type = str(data.get("source_type", "json_api")).strip().lower()
    endpoint = str(data.get("endpoint", "")).strip()
    if not endpoint:
        raise HTTPException(400, "endpoint is required")

    if source_type == "json_api":
        source = JsonApiSource(endpoint=endpoint)
    elif source_type == "cppp_feed":
        source = CpppFeedSource(endpoint=endpoint)
    elif source_type == "state_portal_table":
        source = StatePortalTableSource(endpoint=endpoint)
    else:
        raise HTTPException(400, f"Unsupported source_type: {source_type}")

    rows = source.fetch()
    return {
        "status": "ok",
        "source_type": source_type,
        "count": len(rows),
        "sample": rows[:5],
    }


@app.get("/platform/ingested")
async def list_ingested(
    limit: int = 100,
    source_name: str = "",
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin", "analyst", "viewer"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required")
    limit = max(1, min(limit, 1000))
    with get_db_session() as db:
        q = db.query(IngestedTender)
        if source_name:
            q = q.filter(IngestedTender.source_name == source_name)
        rows = q.order_by(IngestedTender.updated_at.desc()).limit(limit).all()
        return {
            "items": [
                {
                    "id": r.id,
                    "source_name": r.source_name,
                    "external_id": r.external_id,
                    "title": r.title,
                    "org_name": r.org_name,
                    "deadline": r.deadline,
                    "reference_no": r.reference_no,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else "",
                }
                for r in rows
            ]
        }


@app.post("/platform/clauses/index")
async def enqueue_clause_index(
    data: dict = Body(...),
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin", "analyst"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required")
    payload = {
        "t247_id": str(data.get("t247_id", "")).strip(),
        "source_record_id": int(data.get("source_record_id") or 0),
    }
    with get_db_session() as db:
        item = WorkItem(work_type="clause_index", payload_json=json.dumps(payload))
        db.add(item)
        db.flush()
        work_id = item.id
    return {"status": "queued", "work_item_id": work_id}


@app.get("/platform/clauses")
async def list_clause_evidence(
    t247_id: str = "",
    source_record_id: int = 0,
    clause_type: str = "",
    limit: int = 200,
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    _require_role(credentials, {"admin", "analyst", "viewer"})
    if not settings.database_enabled:
        raise HTTPException(400, "DATABASE_URL is required")
    limit = max(1, min(limit, 2000))
    with get_db_session() as db:
        q = db.query(ClauseEvidence)
        if t247_id:
            q = q.filter(ClauseEvidence.t247_id == t247_id)
        if source_record_id:
            q = q.filter(ClauseEvidence.source_record_id == source_record_id)
        if clause_type:
            q = q.filter(ClauseEvidence.clause_type == clause_type)
        rows = q.order_by(ClauseEvidence.created_at.desc()).limit(limit).all()
        return {
            "items": [
                {
                    "id": r.id,
                    "t247_id": r.t247_id,
                    "source_record_id": r.source_record_id,
                    "clause_type": r.clause_type,
                    "clause_text": r.clause_text,
                    "evidence_text": r.evidence_text,
                    "confidence": r.confidence,
                }
                for r in rows
            ]
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
        LATEST_EXCEL_FILE.write_bytes(content)
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

@app.post("/sync-excel-latest")
async def sync_excel_latest():
    if not LATEST_EXCEL_FILE.exists():
        raise HTTPException(404, "No previously imported Excel found. Import once first.")
    tenders = process_excel(str(LATEST_EXCEL_FILE))
    db = load_db()
    added = updated = 0
    for t in tenders:
        tid = str(t.get("t247_id", ""))
        if not tid:
            continue
        if tid in db["tenders"]:
            db["tenders"][tid].update(t)
            updated += 1
        else:
            db["tenders"][tid] = t
            added += 1
    save_db(db)
    return {"status": "success", "source": str(LATEST_EXCEL_FILE), "total": len(tenders), "added": added, "updated": updated}

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
    Generate pre-bid query letter as Word doc (.docx) with proper NIT letterhead.
    Returns base64-encoded doc for direct browser download.
    """
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found. Analyse tender first.")

    queries = tender.get("prebid_queries", [])
    if not queries:
        raise HTTPException(400, "No pre-bid queries found. Analyse this tender first.")

    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt, Cm, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        import base64, io

        doc = DocxDocument()

        # Page setup
        sec = doc.sections[0]
        sec.page_width = Cm(21); sec.page_height = Cm(29.7)
        sec.left_margin = sec.right_margin = Cm(2.5)
        sec.top_margin = sec.bottom_margin = Cm(2)

        # Header — NIT details
        def add_para(text="", bold=False, size=11, align=WD_ALIGN_PARAGRAPH.LEFT, color=None, space_after=6):
            p = doc.add_paragraph()
            p.alignment = align
            p.paragraph_format.space_after = Pt(space_after)
            p.paragraph_format.space_before = Pt(0)
            run = p.add_run(text)
            run.bold = bold
            run.font.size = Pt(size)
            if color:
                run.font.color.rgb = RGBColor(*color)
            return p

        add_para("NASCENT INFO TECHNOLOGIES PVT. LTD.", bold=True, size=14,
                 align=WD_ALIGN_PARAGRAPH.CENTER, color=(0, 70, 127), space_after=2)
        add_para("A-805, Shapath IV, SG Highway, Prahlad Nagar, Ahmedabad – 380015",
                 size=9, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
        add_para("📞 +91-79-40200400 | ✉ nascent.tender@nascentinfo.com | 🌐 www.nascentinfo.com",
                 size=9, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
        add_para("CIN: U72200GJ2006PTC048723 | PAN: AACCN3670J | GSTIN: 24AACCN3670J1ZG | MSME: UDYAM-GJ-01-0007420",
                 size=8, align=WD_ALIGN_PARAGRAPH.CENTER, color=(100,100,100), space_after=4)

        # Horizontal line
        hr = doc.add_paragraph()
        hr.paragraph_format.space_after = Pt(8)
        hr_run = hr.add_run("─" * 90)
        hr_run.font.size = Pt(8); hr_run.font.color.rgb = RGBColor(0, 70, 127)

        # Date + Ref
        today_str = datetime.now().strftime("%d %B %Y")
        add_para(f"Date: {today_str}", size=10, space_after=10)

        # To block
        org = str(tender.get("org_name") or "The Tender Inviting Authority")
        tender_no = str(tender.get("tender_no") or t247_id)
        tender_name = str(tender.get("tender_name") or tender.get("brief") or "")
        contact = str(tender.get("contact") or "")

        add_para("To,", size=11, bold=True, space_after=0)
        add_para(f"The Tender Inviting Authority", size=11, space_after=0)
        add_para(org, size=11, space_after=2)
        if contact and contact not in ("—", "Not specified"):
            add_para(f"Contact: {contact}", size=10, color=(80,80,80), space_after=2)

        # Subject
        subj_p = doc.add_paragraph()
        subj_p.paragraph_format.space_before = Pt(10)
        subj_p.paragraph_format.space_after = Pt(10)
        subj_run = subj_p.add_run(f"Subject: Pre-Bid Queries — Ref: {tender_no}")
        subj_run.bold = True; subj_run.font.size = Pt(11)
        subj_run.font.color.rgb = RGBColor(0, 70, 127)

        if tender_name and tender_name != "—":
            add_para(f"Tender: {tender_name[:120]}", size=10, color=(80,80,80), space_after=6)

        add_para("Respected Sir/Madam,", size=11, space_after=6)
        add_para(
            "With reference to the above-mentioned tender, Nascent Info Technologies Pvt. Ltd. "
            "requests clarification on the following points. We request your kind consideration "
            "and responses at the earliest, preferably before the pre-bid meeting:",
            size=11, space_after=10
        )

        # Query table
        tbl = doc.add_table(rows=1, cols=4)
        tbl.style = "Table Grid"
        hdr = tbl.rows[0].cells
        for cell, text, w in zip(hdr, ["Sr.", "Clause Ref", "RFP Extract", "Our Query"], [1.2, 2.5, 6, 6]):
            cell.text = text
            cell.paragraphs[0].runs[0].bold = True
            cell.paragraphs[0].runs[0].font.size = Pt(9)
            cell.width = Cm(w)

        for i, q in enumerate(queries, 1):
            if isinstance(q, dict):
                clause = str(q.get("clause_ref") or q.get("clause") or "—")
                rfp_text = str(q.get("rfp_text") or "—")
                qtxt = str(q.get("query") or q.get("text") or str(q))
            else:
                clause = "—"; rfp_text = "—"; qtxt = str(q)

            row = tbl.add_row().cells
            row[0].text = str(i)
            row[1].text = clause
            row[2].text = rfp_text[:200]
            row[3].text = qtxt[:300]
            for cell in row:
                cell.paragraphs[0].runs[0].font.size = Pt(9)

        # Closing
        doc.add_paragraph()
        add_para("We hope for your prompt response to ensure clarity for a competitive bid.",
                 size=11, space_after=14)
        add_para("Yours sincerely,", size=11, space_after=20)
        add_para("Authorised Signatory", size=11, bold=True, space_after=2)
        add_para("Nascent Info Technologies Pvt. Ltd.", size=11, space_after=2)
        add_para("MSME | CMMI L3 V2.0 | ISO 9001 | ISO 27001 | ISO 20000", size=9, color=(100,100,100))

        # Save to bytes → base64
        buf = io.BytesIO()
        doc.save(buf)
        doc_bytes = buf.getvalue()
        doc_b64 = base64.b64encode(doc_bytes).decode("utf-8")

        fname = f"PreBid_{re.sub(r'[^\w\-]', '_', tender_no)[:40]}.docx"

        # Also save to disk for /download/ fallback
        try:
            (OUTPUT_DIR / fname).write_bytes(doc_bytes)
        except Exception:
            pass

        return {
            "status": "success",
            "filename": fname,
            "query_count": len(queries),
            "download_url": f"/download/{fname}",
            "doc_b64": doc_b64,
        }

    except Exception as e:
        import traceback
        raise HTTPException(500, f"Pre-bid letter generation failed: {str(e)}\n{traceback.format_exc()[:500]}")


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
    t = get_tender(t247_id)
    if not t:
        raise HTTPException(404, f"Tender {t247_id} not found")
    return t

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
async def process_files(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...), t247_id: str = ""):
    if not files:
        raise HTTPException(400, "No files uploaded")

    # Read files into memory immediately (before background task)
    file_contents = []
    for upload in files:
        content = await upload.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File {upload.filename} too large (max 50MB)")
        file_contents.append((upload.filename or "upload", content))

    job_id = str(uuid.uuid4())[:12]
    import time
    _set_job(job_id, status="running", progress="Starting…", result=None, error=None, t247_id=t247_id, started_at=time.time())
    background_tasks.add_task(_run_analysis_job, job_id, file_contents, t247_id)
    return {"job_id": job_id, "status": "running"}


@app.get("/analyse-status/{job_id}")
async def analyse_status(job_id: str):
    import time as _time
    job = _get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    # Auto-fail jobs stuck > 8 minutes (Gemini timeout / crash)
    if job.get("status") == "running" and job.get("started_at"):
        elapsed = _time.time() - job["started_at"]
        if elapsed > 480:
            _set_job(job_id, status="error", error=f"Analysis timed out after {int(elapsed)}s. Check Gemini API key and try again.")
            return _get_job(job_id)
    return job


def _run_analysis_job(job_id: str, file_contents: list, t247_id: str):
    """Runs in background thread — full analysis pipeline.
    Acquires a JobSlot so Render stays within RAM ceiling while
    multiple tenders are analyzed concurrently."""
    slot_held = False
    if API_POOL_AVAILABLE:
        try:
            slots = get_slots()
            _set_job(job_id, progress=f"Queued (active={slots.snapshot()['active']}/{slots.max})…")
            if slots.acquire(timeout=900.0):
                slot_held = True
            else:
                _set_job(job_id, status="error", error="Analyst queue busy — try again in a minute.")
                return
        except Exception:
            slot_held = False
    tmp_dir = tempfile.mkdtemp(prefix="tender_", dir=str(TEMP_DIR))
    job_ok = False
    try:
        extract_dir = Path(tmp_dir) / "extracted"
        extract_dir.mkdir()

        _set_job(job_id, progress="Extracting documents…")
        for fname, content in file_contents:
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
            _set_job(job_id, status="error", error="No readable documents found in uploaded files.")
            return

        corr = [f for f in doc_files if any(k in f.name.lower() for k in ["corrigendum","addendum","amendment","corr_","addend","revised","rectification"])]
        main_files = [f for f in doc_files if f not in corr]

        _set_job(job_id, progress="Reading documents…")
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

        _set_job(job_id, progress="Reading full text…")
        all_text = ""
        MAX_CORPUS = 350_000  # ~350KB — keeps RAM under 512MB on Render free tier
        for f in sorted(doc_files, key=lambda x: (0 if any(k in x.name.lower() for k in ["rfp","nit","tender","bid"]) else 1 if any(k in x.name.lower() for k in ["corrigendum","addendum"]) else 2)):
            if len(all_text) >= MAX_CORPUS:
                break
            t = read_document(f)
            if t and t.strip():
                remaining = MAX_CORPUS - len(all_text)
                all_text += f"\n\n=== FILE: {f.name} ===\n{t[:remaining]}"
        # Free doc file handles from memory before AI pipeline
        del doc_files

        config = load_config()
        api_key = config.get("gemini_api_key", "")
        ai_used = False
        passed = prebid_passed(tender_data.get("prebid_query_date", ""))

        if api_key and all_text.strip():
            _set_job(job_id, progress="AI pipeline starting (parallel 9 segments)…")
            def _seg_progress(stage, done, total):
                try:
                    _set_job(job_id, progress=f"AI · {stage} · {done}/{total}")
                except Exception:
                    pass
            if PARALLEL_ANALYST_AVAILABLE:
                ai_result = analyze_with_gemini_parallel(all_text, passed, progress_cb=_seg_progress)
            else:
                ai_result = analyze_with_gemini(all_text, passed)
            if "error" not in ai_result:
                tender_data = merge_results(tender_data, ai_result, passed)
                ai_used = True
            else:
                err_msg = ai_result.get("error", "AI error")
                tender_data["ai_warning"] = err_msg
                # If quota exhausted, still return regex-extracted data as partial result
                if "429" in err_msg or "quota" in err_msg.lower() or "503" in err_msg:
                    tender_data["ai_warning"] = f"Gemini quota exhausted — showing regex-extracted data only. Try again in 1 hour. ({err_msg[:80]})"
                _set_job(job_id, progress=f"AI unavailable — using basic extraction…")
        elif not api_key:
            tender_data["ai_warning"] = "Gemini API key not configured. Go to Settings → Gemini AI Keys."

        raw_text_preview = all_text[:20000]
        del all_text  # free corpus memory before eligibility check
        _set_job(job_id, progress="Checking eligibility…")
        checker = NascentChecker()
        if not tender_data.get("overall_verdict"):
            tender_data["pq_criteria"] = checker.check_all(tender_data.get("pq_criteria", []))
            tender_data["tq_criteria"] = checker.check_all(tender_data.get("tq_criteria", []))
            tender_data["overall_verdict"] = checker.get_overall_verdict(tender_data["pq_criteria"] + tender_data["tq_criteria"])

        _set_job(job_id, progress="Generating Word report…")
        output_filename = ""
        doc_b64 = ""
        try:
            import base64
            generator = BidDocGenerator()
            safe_no = re.sub(r'[^\w\-]', '_', tender_data.get("tender_no", "Report"))[:50]
            output_filename = f"BidNoBid_{safe_no}.docx"
            out_path = str(OUTPUT_DIR / output_filename)
            generator.generate(tender_data, out_path)
            # Read back as base64 so frontend can download directly (Render disk is ephemeral)
            with open(out_path, "rb") as docf:
                doc_b64 = base64.b64encode(docf.read()).decode("utf-8")
        except Exception as doc_err:
            tender_data["doc_warning"] = f"Word report failed: {str(doc_err)[:100]}"

        if t247_id:
            _set_job(job_id, progress="Saving to database…")
            db_record = get_tender(t247_id)
            db_record.update({
                "t247_id": t247_id,
                "tender_no": tender_data.get("tender_no"),
                "org_name": tender_data.get("org_name"),
                "tender_name": tender_data.get("tender_name"),
                "bid_submission_date": tender_data.get("bid_submission_date"),
                "emd": tender_data.get("emd"),
                "estimated_cost": tender_data.get("estimated_cost"),
                "verdict": (tender_data.get("overall_verdict") or {}).get("verdict", ""),
                "verdict_color": (tender_data.get("overall_verdict") or {}).get("color", ""),
                "bid_no_bid_done": True,
                "report_file": output_filename,
                "analysed_at": datetime.now().isoformat(),
                "has_corrigendum": tender_data.get("has_corrigendum", False),
                "ai_used": ai_used,
                "scope_items": tender_data.get("scope_items", []),
                "contract_period": tender_data.get("contract_period", ""),
                "pq_criteria": tender_data.get("pq_criteria", []),
                "tq_criteria": tender_data.get("tq_criteria", []),
                "payment_terms": tender_data.get("payment_terms", []),
                "notes": tender_data.get("notes", []),
                "overall_verdict": tender_data.get("overall_verdict", {}),
                "prebid_queries": tender_data.get("prebid_queries", []),
                "raw_text": raw_text_preview,
                "prebid_passed": passed,
                "scope_sections": tender_data.get("scope_sections", []),
                "tq_total_marks": tender_data.get("tq_total_marks"),
                "tq_nascent_estimated_total": tender_data.get("tq_nascent_estimated_total"),
                "submission_checklist": tender_data.get("submission_checklist", []),
            })
            save_tender(t247_id, db_record)

        _set_job(job_id,
            status="done",
            progress="Complete",
            result={
                "status": "success",
                "ai_used": ai_used,
                "has_corrigendum": tender_data.get("has_corrigendum", False),
                "files_processed": [fc[0] for fc in file_contents],
                "tender_data": tender_data,
                "download_file": output_filename,
                "doc_b64": doc_b64,
                "doc_filename": output_filename,
            }
        )
        job_ok = True

    except Exception as e:
        import traceback
        _set_job(job_id, status="error", error=str(e), traceback=traceback.format_exc())
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if slot_held and API_POOL_AVAILABLE:
            try:
                get_slots().release(job_ok)
            except Exception:
                pass



# ══ GENERATE DOCS ════════════════════════════════════════════════════════════
@app.post("/generate-docs/{t247_id}")
async def generate_docs(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found. Analyse the tender first.")
    try:
        import base64, io, zipfile as _zf
        files_b64 = []   # [{name, filename, b64}]
        zip_b64 = ""
        zip_filename = ""

        if SUBMISSION_GEN_AVAILABLE:
            pkg = generate_submission_package(tender, OUTPUT_DIR)
            if "error" not in pkg and pkg.get("files"):
                pkg_dir = Path(pkg.get("pkg_dir", ""))
                # Encode each docx as base64
                for item in pkg.get("files", []):
                    fpath = pkg_dir / item["filename"]
                    if fpath.exists():
                        b64 = base64.b64encode(fpath.read_bytes()).decode()
                        files_b64.append({
                            "name": item["name"],
                            "filename": item["filename"],
                            "b64": b64,
                            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        })
                # Encode ZIP
                if pkg.get("zip_file"):
                    zip_path = OUTPUT_DIR / pkg["zip_file"]
                    if zip_path.exists():
                        zip_b64 = base64.b64encode(zip_path.read_bytes()).decode()
                        zip_filename = pkg["zip_file"]

                return {
                    "status": "success",
                    "files": files_b64,
                    "zip_b64": zip_b64,
                    "zip_filename": zip_filename,
                    "doc_count": len(files_b64),
                }

        # Fallback — just bid/no-bid report
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
        out_path = OUTPUT_DIR / output_filename
        generator.generate(tender_data, str(out_path))
        b64 = base64.b64encode(out_path.read_bytes()).decode()
        return {
            "status": "success",
            "files": [{"name": "Bid/No-Bid Report", "filename": output_filename, "b64": b64,
                       "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}],
            "zip_b64": "",
            "zip_filename": "",
            "doc_count": 1,
        }
    except Exception as e:
        import traceback
        raise HTTPException(500, f"Document generation failed: {str(e)}\n{traceback.format_exc()[:400]}")


@app.post("/generate-technical-proposal/{t247_id}")
async def generate_technical_proposal(t247_id: str):
    """
    UI compatibility endpoint. For now maps to generate-docs package output.
    """
    result = await generate_docs(t247_id)
    files = result.get("files", [])
    first = files[0]["filename"] if files else result.get("download_file")
    return {"status": "success", "filename": first}

@app.post("/tender/{t247_id}/technical-proposal")
async def generate_technical_proposal_alias(t247_id: str):
    return await generate_technical_proposal(t247_id)

@app.post("/merge-submission-pdf/{t247_id}")
async def merge_submission_pdf(t247_id: str):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    if not PDF_MERGE_AVAILABLE:
        raise HTTPException(500, "pdf_merger not available")

    safe_no = re.sub(r'[^\w\-]', '_', str(tender.get("tender_no", t247_id)))[:30]
    pkg_dir = OUTPUT_DIR / f"SubmissionPackage_{safe_no}"
    source_dirs = [pkg_dir, OUTPUT_DIR]
    source_dirs = [d for d in source_dirs if d.exists()]
    if not source_dirs:
        raise HTTPException(400, "No generated documents found. Generate docs first.")

    merged = merge_submission_package(
        t247_id=t247_id,
        tender_data=tender,
        source_dirs=source_dirs,
        output_dir=OUTPUT_DIR,
        include_cover=True,
    )
    if merged.get("status") != "success":
        errs = merged.get("errors", ["Merge failed"])
        # Return a non-5xx response so UI can show a friendly message instead of hard failure.
        return {
            "status": "unavailable",
            "message": "Merged PDF could not be generated in this environment.",
            "errors": errs,
        }

    fname = merged.get("filename")
    return {
        "status": "success",
        "filename": fname,
        "download_url": f"/download/{fname}" if fname else "",
        "page_count": merged.get("page_count", 0),
        "file_count": merged.get("file_count", 0),
    }

@app.post("/tender/{t247_id}/merge-pdf")
async def merge_submission_pdf_alias(t247_id: str):
    return await merge_submission_pdf(t247_id)

@app.post("/tender/{t247_id}/draft/generate")
async def generate_tender_draft(t247_id: str, data: dict = Body(default={})):
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    doc_type = _safe_doc_type(data.get("doc_type", "analysis"))
    content = _build_tender_draft(tender, doc_type)
    path = _draft_path(t247_id, doc_type)
    path.write_text(content, encoding="utf-8")
    return {"status": "success", "t247_id": t247_id, "doc_type": doc_type, "filename": path.name, "content": content}

@app.get("/tender/{t247_id}/draft")
async def get_tender_draft(t247_id: str, doc_type: str = "analysis"):
    doc_type = _safe_doc_type(doc_type)
    path = _draft_path(t247_id, doc_type)
    if not path.exists():
        tender = get_tender(t247_id)
        if not tender:
            raise HTTPException(404, "Tender not found")
        content = _build_tender_draft(tender, doc_type)
        path.write_text(content, encoding="utf-8")
    return {"status": "success", "t247_id": t247_id, "doc_type": doc_type, "filename": path.name, "content": path.read_text(encoding="utf-8")}

@app.post("/tender/{t247_id}/draft/save")
async def save_tender_draft(t247_id: str, data: dict = Body(...)):
    content = str(data.get("content", ""))
    doc_type = _safe_doc_type(data.get("doc_type", "analysis"))
    if not content.strip():
        raise HTTPException(400, "Draft content is empty")
    path = _draft_path(t247_id, doc_type)
    path.write_text(content, encoding="utf-8")
    return {"status": "saved", "filename": path.name, "size_kb": round(path.stat().st_size / 1024, 1)}

@app.post("/tender/{t247_id}/draft/chat-edit")
async def chat_edit_tender_draft(t247_id: str, data: dict = Body(...)):
    instruction = str(data.get("instruction", "")).strip()
    doc_type = _safe_doc_type(data.get("doc_type", "analysis"))
    content = str(data.get("content", "")).strip()
    if not instruction:
        raise HTTPException(400, "Edit instruction is required")
    if not content:
        draft = await get_tender_draft(t247_id, doc_type)
        content = draft.get("content", "")

    cfg = load_config()
    keys = get_all_api_keys()
    if not keys:
        raise HTTPException(400, "No AI API key configured in Settings")

    prompt = (
        "You are an expert tender document editor.\n"
        "Rewrite the draft as per the user's instruction.\n"
        "Keep factual details intact unless user asks to change.\n"
        "Return only updated draft text.\n\n"
        f"USER INSTRUCTION:\n{instruction}\n\n"
        f"CURRENT DRAFT:\n{content}"
    )

    edited = None
    for key in keys:
        try:
            out = call_gemini(prompt, key)
            if out and len(out.strip()) > 10:
                edited = out.strip()
                break
        except Exception:
            continue
    if not edited:
        raise HTTPException(503, "AI edit unavailable right now (quota or API issue)")

    path = _draft_path(t247_id, doc_type)
    path.write_text(edited, encoding="utf-8")
    return {"status": "success", "t247_id": t247_id, "doc_type": doc_type, "filename": path.name, "content": edited}

@app.get("/tender/{t247_id}/draft/download")
async def download_tender_draft(t247_id: str, doc_type: str = "analysis"):
    doc_type = _safe_doc_type(doc_type)
    path = _draft_path(t247_id, doc_type)
    if not path.exists():
        raise HTTPException(404, "Draft not found")
    return FileResponse(str(path), filename=path.name, media_type="text/markdown")

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
@app.get("/config-full")
@app.get("/config")
async def get_config_route(request: Request):
    check_admin(request)
    config = load_config()
    key = config.get("gemini_api_key", "")
    keys = config.get("gemini_api_keys", [])
    # Ensure primary key is first in list
    if key and key not in keys:
        keys = [key] + keys
    groq_key = config.get("groq_api_key", "")
    return {
        "gemini_api_key_set": bool(key),
        "gemini_api_key": key,
        "gemini_api_key_preview": (key[:8] + "..." + key[-4:]) if key else "",
        "gemini_api_keys": keys,
        "gemini_api_key_2": keys[1] if len(keys) > 1 else "",
        "gemini_api_key_3": keys[2] if len(keys) > 2 else "",
        "gemini_api_key_4": keys[3] if len(keys) > 3 else "",
        "groq_api_key": groq_key,
        "t247_username": config.get("t247_username", ""),
    }

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
    # Also accept individual key fields from UI (key1/key2/key3/key4)
    ui_keys = []
    for field in ["gemini_api_key", "gemini_api_key_2", "gemini_api_key_3", "gemini_api_key_4"]:
        v = str(data.get(field, "") or "").strip()
        if v and len(v) > 20:
            ui_keys.append(v)
    if ui_keys and not data.get("gemini_api_keys"):
        config["gemini_api_key"] = ui_keys[0]
        config["gemini_api_keys"] = ui_keys
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
    profile = load_profile()
    projects = profile.get("projects", []) or []
    if not profile.get("project_tabs"):
        grouped = {}
        for p in projects:
            tab = str((p or {}).get("tab", "General") or "General").strip() or "General"
            grouped.setdefault(tab, []).append(p)
        profile["project_tabs"] = [{"name": name, "projects": items} for name, items in grouped.items()] or [{"name": "General", "projects": []}]
    return profile

@app.post("/profile")
async def update_profile(data: dict = Body(...)):
    project_tabs = data.get("project_tabs", []) or []
    if project_tabs:
        flat_projects = []
        for tab in project_tabs:
            tab_name = str((tab or {}).get("name", "General")).strip() or "General"
            for p in (tab or {}).get("projects", []) or []:
                if isinstance(p, dict):
                    item = dict(p)
                    item["tab"] = tab_name
                    flat_projects.append(item)
        data["projects"] = flat_projects

    payload = json.dumps(data, indent=2, ensure_ascii=False)
    payload_bytes = payload.encode("utf-8")

    # Write to all locations
    repo_path = BASE_DIR / "nascent_profile.json"
    try:
        repo_path.write_text(payload, encoding="utf-8")
    except Exception as e:
        print(f"⚠️ repo profile write failed: {e}")

    runtime_dir = Path(os.environ.get("BIDNOBID_RUNTIME_DIR", "/tmp/bid-nobid"))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / "nascent_profile.json"
    try:
        runtime_path.write_text(payload, encoding="utf-8")
    except Exception as e:
        print(f"⚠️ runtime profile write failed: {e}")

    # Drive backup — this is the ONLY one that survives redeploy
    drive_saved = False
    try:
        if drive_available():
            prof_tmp = OUTPUT_DIR / "nascent_profile.json"
            OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
            prof_tmp.write_bytes(payload_bytes)
            drive_saved = save_to_drive(prof_tmp, filename="nascent_profile.json")
            if drive_saved:
                print("✅ Profile saved to Drive")
            else:
                print("⚠️ Profile Drive save failed")
    except Exception as e:
        print(f"⚠️ Profile Drive backup failed: {e}")

    try:
        from excel_processor import invalidate_rules_cache
        invalidate_rules_cache()
    except (ImportError, AttributeError):
        pass

    return {"status": "saved", "drive_synced": drive_saved}


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

@app.get("/analytics/win-loss")
async def get_win_loss_alias():
    return get_win_loss_stats()

@app.get("/analytics")
async def get_analytics_alias():
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
@app.post("/tender/{t247_id}/bid-result")
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

@app.post("/bid-result")
async def save_bid_result_without_path(data: dict = Body(...)):
    t247_id = str(data.get("t247_id", "")).strip()
    if not t247_id:
        raise HTTPException(400, "t247_id is required")
    return await save_bid_result(t247_id, data)

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

@app.get("/skipped-tenders")
async def get_skipped_alias():
    db = load_db()
    return {"tenders": [t for t in db["tenders"].values() if t.get("status") == "Not Interested"]}

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

@app.get("/letterhead/status")
async def letterhead_status():
    lh_docx = OUTPUT_DIR / "letterhead.docx"
    return {
        "has_letterhead": lh_docx.exists(),
        "filename": lh_docx.name if lh_docx.exists() else "",
        "size_kb": round(lh_docx.stat().st_size / 1024, 1) if lh_docx.exists() else 0,
    }

@app.get("/letterhead-status")
async def letterhead_status_alias():
    return await letterhead_status()

@app.post("/letterhead/upload")
async def upload_letterhead(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(400, "Upload a .docx file for letterhead template")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 50MB)")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUTPUT_DIR / "letterhead.docx"
    dest.write_bytes(content)
    return {"status": "ok", "filename": dest.name, "size_kb": round(dest.stat().st_size / 1024, 1)}

@app.post("/upload-letterhead")
async def upload_letterhead_alias(file: UploadFile = File(...)):
    return await upload_letterhead(file)

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
    if not (u and p):
        return {"status": "error", "message": "T247 credentials not configured"}
    try:
        import requests as _req
        sess = _req.Session()
        sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        login_r = sess.get("https://www.tender247.com/login", timeout=15)
        csrf = ""
        import re as _re
        m = _re.search(r'name=["\']_token["\'] value=["\']([^"\']+)["\']', login_r.text)
        if m:
            csrf = m.group(1)
        post_r = sess.post("https://www.tender247.com/login", data={
            "_token": csrf, "email": u, "password": p,
        }, timeout=15, allow_redirects=True)
        if "dashboard" in post_r.url or "logout" in post_r.text.lower():
            return {"status": "success", "message": f"Connected as {u}"}
        if "invalid" in post_r.text.lower() or "wrong" in post_r.text.lower():
            return {"status": "error", "message": "Invalid credentials"}
        return {"status": "success", "message": f"Login attempted as {u} — check manually if unsure"}
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {e}"}


def _t247_login_session(cfg: dict):
    """Return an authenticated requests.Session for tender247.com or raise."""
    import requests as _req, re as _re
    u = str(cfg.get("t247_username", "") or "").strip()
    p = str(cfg.get("t247_password", "") or "").strip()
    if not (u and p):
        raise ValueError("T247 credentials not configured. Add them in Settings.")
    sess = _req.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    login_page = sess.get("https://www.tender247.com/login", timeout=20)
    csrf = ""
    m = _re.search(r'name=["\']_token["\'] value=["\']([^"\']+)["\']', login_page.text)
    if m:
        csrf = m.group(1)
    post_r = sess.post("https://www.tender247.com/login", data={
        "_token": csrf, "email": u, "password": p,
    }, timeout=20, allow_redirects=True)
    if "invalid" in post_r.text.lower() or ("login" in post_r.url and "dashboard" not in post_r.url):
        raise ValueError("T247 login failed — check credentials in Settings.")
    return sess


@app.post("/fetch-t247-excel")
async def fetch_t247_excel(background_tasks: BackgroundTasks):
    """Auto-download today's tender list from tender247.com and import it."""
    cfg = load_config()
    try:
        sess = _t247_login_session(cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        import requests as _req
        # Try common T247 Excel export URLs
        export_urls = [
            "https://www.tender247.com/tenders/export",
            "https://www.tender247.com/export/tenders",
            "https://www.tender247.com/tenders/download-excel",
            "https://www.tender247.com/dashboard/export",
        ]
        xl_bytes = None
        used_url = ""
        for url in export_urls:
            try:
                r = sess.get(url, timeout=60, stream=True)
                ct = r.headers.get("Content-Type", "")
                cd = r.headers.get("Content-Disposition", "")
                if r.status_code == 200 and ("spreadsheet" in ct or "excel" in ct or ".xlsx" in cd or ".xls" in cd or len(r.content) > 5000):
                    xl_bytes = r.content
                    used_url = url
                    break
            except Exception:
                continue
        if not xl_bytes:
            # Try scraping dashboard for a download link
            dash = sess.get("https://www.tender247.com/dashboard", timeout=20)
            import re as _re
            links = _re.findall(r'href=["\']([^"\']*(?:export|excel|download)[^"\']*)["\']', dash.text, _re.I)
            for link in links[:5]:
                if not link.startswith("http"):
                    link = "https://www.tender247.com" + link
                try:
                    r = sess.get(link, timeout=60)
                    if r.status_code == 200 and len(r.content) > 5000:
                        xl_bytes = r.content
                        used_url = link
                        break
                except Exception:
                    continue
        if not xl_bytes:
            raise HTTPException(502, "Could not locate Excel export on tender247.com. Try importing Excel manually.")
        # Save and process
        LATEST_EXCEL_FILE.write_bytes(xl_bytes)
        import tempfile as _tmp
        tmp = Path(_tmp.mktemp(suffix=".xlsx", dir=str(TEMP_DIR)))
        tmp.write_bytes(xl_bytes)
        tenders = process_excel(str(tmp))
        try:
            tmp.unlink()
        except Exception:
            pass
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
                existing["days_left"] = t.get("days_left", existing.get("days_left", 999))
                existing["deadline_status"] = t.get("deadline_status", existing.get("deadline_status","OK"))
                db["tenders"][tid] = existing
                updated += 1
            else:
                db["tenders"][tid] = t
                added += 1
        save_db(db)
        return {"status": "success", "total": len(tenders), "added": added, "updated": updated,
                "source_url": used_url, "file_size_kb": len(xl_bytes) // 1024}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"T247 fetch failed: {e}")


@app.get("/tender/{t247_id}/doc-download")
async def download_tender_docs(t247_id: str):
    """Download tender documents from tender247.com as zip, return as file."""
    cfg = load_config()
    try:
        sess = _t247_login_session(cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        import requests as _req, re as _re, io as _io
        safe_tid = re.sub(r"[^\w\-]", "_", str(t247_id))[:40]
        # Try direct zip download URLs for this tender
        download_urls = [
            f"https://www.tender247.com/tenders/{t247_id}/download",
            f"https://www.tender247.com/tenders/{t247_id}/documents",
            f"https://www.tender247.com/download-documents/{t247_id}",
            f"https://www.tender247.com/tenders/download/{t247_id}",
        ]
        zip_bytes = None
        for url in download_urls:
            try:
                r = sess.get(url, timeout=60)
                ct = r.headers.get("Content-Type", "")
                if r.status_code == 200 and ("zip" in ct or "octet" in ct or len(r.content) > 1000):
                    zip_bytes = r.content
                    break
            except Exception:
                continue
        if not zip_bytes:
            # Try tender detail page to find download link
            detail_r = sess.get(f"https://www.tender247.com/tenders/{t247_id}", timeout=20)
            links = _re.findall(r'href=["\']([^"\']*(?:download|document|zip)[^"\']*)["\']', detail_r.text, _re.I)
            for link in links[:5]:
                if not link.startswith("http"):
                    link = "https://www.tender247.com" + link
                try:
                    r = sess.get(link, timeout=60)
                    if r.status_code == 200 and len(r.content) > 1000:
                        zip_bytes = r.content
                        break
                except Exception:
                    continue
        if not zip_bytes:
            raise HTTPException(404, f"No documents found for T247 ID {t247_id} — download manually from tender247.com")
        from fastapi.responses import Response
        ct = "application/zip"
        fname = f"T247_{safe_tid}_docs.zip"
        if not zipfile.is_zipfile(_io.BytesIO(zip_bytes)):
            ct = "application/octet-stream"
            fname = f"T247_{safe_tid}_docs.bin"
        return Response(content=zip_bytes, media_type=ct,
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Document download failed: {e}")


@app.post("/auto-download/{t247_id}")
async def auto_download_tender(t247_id: str):
    return await download_tender_docs(t247_id)

@app.post("/tender/{t247_id}/auto-download")
async def auto_download_tender_alias(t247_id: str):
    return await download_tender_docs(t247_id)


# ── TENDER UPDATE (alias for status) ─────────────────────────
@app.post("/tender/{t247_id}/update")
async def update_tender_fields(t247_id: str, body: dict = Body(...)):
    with db_lock:
        db = load_db()
        t = db["tenders"].get(str(t247_id))
        if not t:
            raise HTTPException(404, "Tender not found")
        allowed = {"verdict","reason","status","notes","notes_internal","pipeline_stage",
                   "outcome","outcome_value","outcome_competitor","outcome_notes"}
        for k, v in body.items():
            if k in allowed:
                t[k] = v
        t["updated_at"] = datetime.now().isoformat()
        db["tenders"][str(t247_id)] = t
        save_db(db)
    return {"status": "ok", "t247_id": t247_id}


# ── VAULT (doc storage — disk-backed so it survives restarts) ────
_VAULT_FILE = OUTPUT_DIR / "vault_index.json"
_vault_lock = threading.Lock()

def _load_vault() -> list:
    with _vault_lock:
        try:
            if _VAULT_FILE.exists():
                return json.loads(_VAULT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

def _save_vault(vault: list):
    with _vault_lock:
        _VAULT_FILE.parent.mkdir(exist_ok=True, parents=True)
        _VAULT_FILE.write_text(json.dumps(vault, indent=2), encoding="utf-8")

@app.get("/vault/list")
async def vault_list():
    vault = _load_vault()
    return {"files": [{k: v for k, v in f.items() if k != "b64"} for f in vault]}

@app.post("/vault/upload")
async def vault_upload(file: UploadFile = File(...), category: str = "general"):
    import base64
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 50MB)")
    entry = {
        "id": str(uuid.uuid4()),
        "name": file.filename,
        "category": category,
        "size": len(data),
        "uploaded_at": datetime.now().isoformat(),
        "b64": base64.b64encode(data).decode(),
        "mime": file.content_type or "application/octet-stream",
    }
    vault = _load_vault()
    vault.append(entry)
    _save_vault(vault)
    return {"status": "ok", "file": {k: v for k, v in entry.items() if k != "b64"}}

@app.delete("/vault/delete/{file_id}")
async def vault_delete(file_id: str):
    vault = _load_vault()
    before = len(vault)
    vault = [f for f in vault if f.get("id") != file_id]
    _save_vault(vault)
    return {"status": "ok", "deleted": before - len(vault)}

@app.get("/vault/download/{file_id}")
async def vault_download(file_id: str):
    import base64
    from fastapi.responses import Response
    vault = _load_vault()
    f = next((x for x in vault if x.get("id") == file_id), None)
    if not f:
        raise HTTPException(404, "File not found")
    return Response(content=base64.b64decode(f["b64"]),
                    media_type=f.get("mime","application/octet-stream"),
                    headers={"Content-Disposition": f'attachment; filename="{f["name"]}"'})


# ── POST-AWARD MILESTONES ─────────────────────────────────────
@app.get("/post-award/{t247_id}/milestones")
async def get_milestones(t247_id: str):
    with db_lock:
        db = load_db()
        t = db["tenders"].get(str(t247_id), {})
    return {"milestones": t.get("milestones", []), "invoices": t.get("invoices", [])}

@app.get("/milestones/{t247_id}")
async def get_milestones_alias(t247_id: str):
    return await get_milestones(t247_id)

@app.post("/post-award/{t247_id}/milestones")
async def add_milestone(t247_id: str, body: dict = Body(...)):
    with db_lock:
        db = load_db()
        t = db["tenders"].get(str(t247_id))
        if not t:
            raise HTTPException(404, "Tender not found")
        ms = t.get("milestones", [])
        new_ms = {
            "id": str(uuid.uuid4())[:8],
            "name": body.get("name", "Milestone"),
            "due_date": body.get("due_date", ""),
            "value_pct": body.get("value_pct", 0),
            "status": body.get("status", "Pending"),
            "notes": body.get("notes", ""),
            "created_at": datetime.now().isoformat(),
        }
        ms.append(new_ms)
        t["milestones"] = ms
        db["tenders"][str(t247_id)] = t
        save_db(db)
    return {"status": "ok", "milestone": new_ms}

@app.post("/post-award/{t247_id}/milestones/setup")
async def setup_milestones(t247_id: str, body: dict = Body(...)):
    with db_lock:
        db = load_db()
        t = db["tenders"].get(str(t247_id))
        if not t:
            raise HTTPException(404, "Tender not found")
        t["milestones"] = body.get("milestones", [])
        db["tenders"][str(t247_id)] = t
        save_db(db)
    return {"status": "ok", "count": len(t["milestones"])}

@app.post("/milestones/{t247_id}/setup")
async def setup_milestones_alias(t247_id: str, body: dict = Body(default={})):
    return await setup_milestones(t247_id, {"milestones": body.get("milestones", [])})

@app.patch("/post-award/{t247_id}/milestones/{mid}")
async def update_milestone(t247_id: str, mid: str, body: dict = Body(...)):
    with db_lock:
        db = load_db()
        t = db["tenders"].get(str(t247_id))
        if not t:
            raise HTTPException(404, "Tender not found")
        for ms in t.get("milestones", []):
            if ms.get("id") == mid:
                ms.update({k: v for k, v in body.items()})
                ms["updated_at"] = datetime.now().isoformat()
                break
        db["tenders"][str(t247_id)] = t
        save_db(db)
    return {"status": "ok"}

@app.post("/milestones/{t247_id}/{mid}/done")
async def mark_milestone_done_alias(t247_id: str, mid: str):
    return await update_milestone(t247_id, mid, {"status": "Done"})

@app.post("/post-award/{t247_id}/invoice")
async def add_invoice(t247_id: str, body: dict = Body(...)):
    with db_lock:
        db = load_db()
        t = db["tenders"].get(str(t247_id))
        if not t:
            raise HTTPException(404, "Tender not found")
        invoices = t.get("invoices", [])
        inv = {
            "id": str(uuid.uuid4())[:8],
            "invoice_no": body.get("invoice_no", ""),
            "amount": body.get("amount", 0),
            "date": body.get("date", ""),
            "status": body.get("status", "Raised"),
            "milestone_id": body.get("milestone_id", ""),
            "created_at": datetime.now().isoformat(),
        }
        invoices.append(inv)
        t["invoices"] = invoices
        db["tenders"][str(t247_id)] = t
        save_db(db)
    return {"status": "ok", "invoice": inv}

@app.post("/tender/{t247_id}/invoice")
async def add_invoice_alias(t247_id: str, body: dict = Body(...)):
    return await add_invoice(t247_id, body)

@app.post("/post-award/{t247_id}/{doc_type}")
async def post_award_doc(t247_id: str, doc_type: str, body: dict = Body(...)):
    """Generic post-award doc save (amc, extension, etc.)"""
    with db_lock:
        db = load_db()
        t = db["tenders"].get(str(t247_id))
        if not t:
            raise HTTPException(404, "Tender not found")
        t[f"pa_{doc_type}"] = body
        t[f"pa_{doc_type}_updated"] = datetime.now().isoformat()
        db["tenders"][str(t247_id)] = t
        save_db(db)
    return {"status": "ok", "type": doc_type}

@app.post("/tender/{t247_id}/letter/{doc_type}")
async def post_award_doc_alias(t247_id: str, doc_type: str, body: dict = Body(default={})):
    if not body:
        body = {"generated_at": datetime.now().isoformat()}
    return await post_award_doc(t247_id, doc_type, body)


# ══════════════════════════════════════════════════════════════════════════════
# v8.1 — DOC PREVIEW + AI-EDIT + LOCAL DOWNLOAD + VERSIONS + RISK + COMPLIANCE
# ══════════════════════════════════════════════════════════════════════════════

def _locate_tender_docx(t247_id: str) -> Path:
    """Find latest BidNoBid_*.docx for given tender id; fallback to report_file field."""
    tender = get_tender(t247_id) or {}
    rf = tender.get("report_file")
    if rf:
        p = (OUTPUT_DIR / Path(rf).name)
        if p.exists():
            return p
    candidates = list(OUTPUT_DIR.glob(f"BidNoBid_*{t247_id}*.docx"))
    if not candidates:
        candidates = sorted(OUTPUT_DIR.glob("BidNoBid_*.docx"),
                            key=lambda f: f.stat().st_mtime, reverse=True)
        if tender.get("tender_no"):
            tn = re.sub(r'[^\w\-]', '_', str(tender.get("tender_no")))[:50]
            matched = [p for p in candidates if tn in p.stem]
            if matched:
                return matched[0]
    if candidates:
        return candidates[0]
    raise HTTPException(404, f"No report found for {t247_id}. Run Analyse first.")


@app.get("/tender/{t247_id}/doc-html")
async def tender_doc_html(t247_id: str):
    """Render current report docx as HTML for in-browser preview."""
    if not DOC_EDITOR_AVAILABLE:
        raise HTTPException(500, "doc_editor not available")
    docx_path = _locate_tender_docx(t247_id)
    html = doc_editor.docx_to_html(docx_path)
    return {"status": "ok", "html": html, "filename": docx_path.name,
            "modified": datetime.fromtimestamp(docx_path.stat().st_mtime).isoformat(),
            "size_kb": round(docx_path.stat().st_size / 1024, 1)}


@app.post("/tender/{t247_id}/ai-edit")
async def tender_ai_edit(t247_id: str, body: dict = Body(...)):
    """Apply chatbot instruction to current doc HTML. Returns edited HTML (not saved)."""
    if not DOC_EDITOR_AVAILABLE:
        raise HTTPException(500, "doc_editor not available")
    html = (body.get("html") or "").strip()
    instruction = (body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "instruction required")
    if not html:
        docx_path = _locate_tender_docx(t247_id)
        html = doc_editor.docx_to_html(docx_path)
    result = doc_editor.ai_edit_html(html, instruction)
    if "error" in result:
        raise HTTPException(502, result["error"])
    return {"status": "ok", "html": result["html"], "chars": result["chars"]}


@app.post("/tender/{t247_id}/doc-save")
async def tender_doc_save(t247_id: str, body: dict = Body(...)):
    """Save HTML back to docx. Creates a version snapshot first."""
    if not DOC_EDITOR_AVAILABLE:
        raise HTTPException(500, "doc_editor not available")
    html = body.get("html") or ""
    note = body.get("note") or "manual edit"
    if len(html) < 20:
        raise HTTPException(400, "HTML content empty")
    docx_path = _locate_tender_docx(t247_id)
    try:
        doc_editor.snapshot_version(docx_path, note="auto-snapshot before save")
    except Exception as e:
        print(f"[doc-save] snapshot skipped: {e}")
    try:
        doc_editor.html_to_docx(html, docx_path)
    except Exception as e:
        raise HTTPException(500, f"Save failed: {e}")
    import base64
    b64 = base64.b64encode(docx_path.read_bytes()).decode()
    return {"status": "ok", "filename": docx_path.name,
            "size_kb": round(docx_path.stat().st_size / 1024, 1),
            "doc_b64": b64, "note": note,
            "saved_at": datetime.now().isoformat()}


@app.get("/tender/{t247_id}/doc-download")
async def tender_doc_download_local(t247_id: str):
    """Explicit local-download button endpoint — streams docx with correct filename."""
    docx_path = _locate_tender_docx(t247_id)
    return FileResponse(
        path=str(docx_path),
        filename=docx_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@app.get("/tender/{t247_id}/doc-versions")
async def tender_doc_versions(t247_id: str):
    if not DOC_EDITOR_AVAILABLE:
        raise HTTPException(500, "doc_editor not available")
    docx_path = _locate_tender_docx(t247_id)
    return {"status": "ok", "filename": docx_path.name,
            "versions": doc_editor.list_versions(docx_path)}


@app.post("/tender/{t247_id}/doc-restore/{version_id}")
async def tender_doc_restore(t247_id: str, version_id: str):
    if not DOC_EDITOR_AVAILABLE:
        raise HTTPException(500, "doc_editor not available")
    docx_path = _locate_tender_docx(t247_id)
    result = doc_editor.restore_version(docx_path, version_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return {"status": "ok", **result}


@app.get("/tender/{t247_id}/risk-score")
async def tender_risk_score(t247_id: str):
    if not DOC_EDITOR_AVAILABLE:
        raise HTTPException(500, "doc_editor not available")
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    return {"status": "ok", **doc_editor.compute_risk_score(tender)}


@app.get("/tender/{t247_id}/compliance-matrix")
async def tender_compliance_matrix(t247_id: str):
    if not DOC_EDITOR_AVAILABLE:
        raise HTTPException(500, "doc_editor not available")
    tender = get_tender(t247_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    matrix = doc_editor.build_compliance_matrix(tender)
    covered = sum(1 for m in matrix if m.get("color") == "GREEN")
    partial = sum(1 for m in matrix if m.get("color") == "AMBER")
    gap     = sum(1 for m in matrix if m.get("color") == "RED")
    return {"status": "ok", "total": len(matrix), "covered": covered,
            "partial": partial, "gap": gap, "matrix": matrix}


@app.get("/platform/api-pool-stats")
async def api_pool_stats():
    """Observability for key-pool + analyst slots."""
    if not API_POOL_AVAILABLE:
        return {"status": "unavailable"}
    try:
        refresh_pool()
        return {
            "status": "ok",
            "slots": get_slots().snapshot(),
            "keys": get_pool().stats(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/platform/analyst-capacity")
async def analyst_capacity():
    """Quick sanity check — how many analysts can run today?"""
    if not API_POOL_AVAILABLE:
        return {"status": "unavailable", "hint": "core.api_pool import failed"}
    refresh_pool()
    stats = get_pool().stats()
    slots = get_slots().snapshot()
    total_rpd = sum(max(0, 1400 - s.get("rpd_used", 0)) for s in stats)  # 1400 cap per key
    per_tender_calls = 9  # 9 segments
    max_today = max(0, int(total_rpd / per_tender_calls))
    return {
        "status": "ok",
        "api_keys_configured": len(stats),
        "concurrent_slots": slots.get("max"),
        "active_now": slots.get("active"),
        "completed_today": slots.get("completed_today"),
        "theoretical_daily_capacity": max_today,
        "recommendation": "Add more GEMINI_API_KEY_2..5 env vars to increase throughput"
            if len(stats) < 3 else "Capacity is sufficient for 10+ tenders/day.",
    }
