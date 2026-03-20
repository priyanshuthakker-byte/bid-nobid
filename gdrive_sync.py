"""
Google Drive Sync — Nascent Bid/No-Bid
Permanent fix: uses GDRIVE_FILE_ID env var ONLY for tenders_db.json
Service accounts cannot own/create files — we update an existing file only.
"""
import json, os, io
from pathlib import Path

_drive_service = None
_credentials   = None
_file_id_cache = {}

# ── Connection ────────────────────────────────────────────────

def init_drive() -> bool:
    global _drive_service, _credentials, _file_id_cache
    try:
        creds_json = os.environ.get("GDRIVE_CREDENTIALS", "").strip()
        if not creds_json:
            return False

        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        info  = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive"])
        _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        _credentials   = creds
        _file_id_cache = {}   # clear cache on fresh connect
        return True
    except Exception as e:
        print(f"Drive init error: {e}")
        return False


def drive_available() -> bool:
    return _drive_service is not None


def get_service_account_email() -> str:
    try:
        creds_json = os.environ.get("GDRIVE_CREDENTIALS", "")
        if creds_json:
            info = json.loads(creds_json)
            return info.get("client_email", "unknown")
    except Exception:
        pass
    return "unknown"


def _reconnect() -> bool:
    global _drive_service, _file_id_cache
    try:
        from googleapiclient.discovery import build
        if _credentials:
            _drive_service = build("drive", "v3", credentials=_credentials, cache_discovery=False)
            _file_id_cache = {}
            return True
    except Exception:
        pass
    return init_drive()


def _get_db_file_id() -> str | None:
    """Get the tenders_db.json file ID — ALWAYS prefers GDRIVE_FILE_ID env var."""
    # Check cache
    cached = _file_id_cache.get("db_file_id")
    if cached:
        return cached

    # Priority: env var GDRIVE_FILE_ID (set by user in Render)
    fid = os.environ.get("GDRIVE_FILE_ID", "").strip()
    if fid:
        _file_id_cache["db_file_id"] = fid
        return fid

    print("⚠️  GDRIVE_FILE_ID not set in Render environment variables.")
    print(f"   1) Create tenders_db.json in Google Drive")
    print(f"   2) Share it with {get_service_account_email()} as Editor")
    print(f"   3) Copy the file ID from the URL and set GDRIVE_FILE_ID in Render")
    return None


def _ensure_local(local_path: Path) -> Path:
    local_path = Path(local_path)
    if not local_path.exists():
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text('{"tenders": {}}', encoding="utf-8")
    return local_path

# ── DB Sync ───────────────────────────────────────────────────

def save_to_drive(local_path, filename="tenders_db.json") -> dict:
    """Save tenders_db.json to Google Drive. Updates existing file only."""
    global _drive_service

    local_path = _ensure_local(Path(local_path))

    if not _drive_service:
        return {"ok": False, "reason": "Drive not connected — check GDRIVE_CREDENTIALS"}

    file_id = _get_db_file_id()
    if not file_id:
        return {"ok": False, "reason": "GDRIVE_FILE_ID not set in Render env vars"}

    content = local_path.read_bytes()
    if len(content) < 10:
        return {"ok": False, "reason": "Local DB is empty — not syncing"}

    for attempt in range(3):
        try:
            from googleapiclient.http import MediaIoBaseUpload
            media = MediaIoBaseUpload(io.BytesIO(content),
                                      mimetype="application/json",
                                      resumable=False)
            _drive_service.files().update(fileId=file_id, media_body=media).execute()
            size_kb = round(len(content) / 1024, 1)
            print(f"✅ Drive sync OK — {size_kb} KB → file ID {file_id}")
            return {"ok": True, "size_kb": size_kb, "file_id": file_id}

        except Exception as e:
            err = str(e)
            if "403" in err:
                # Service account doesn't have permission
                sa = get_service_account_email()
                print(f"❌ Drive 403: Share the file with {sa} as Editor")
                return {"ok": False, "reason": f"Permission denied — share file with {sa} as Editor"}
            if "404" in err:
                print(f"❌ Drive 404: File ID {file_id} not found — check GDRIVE_FILE_ID in Render")
                return {"ok": False, "reason": f"File not found — check GDRIVE_FILE_ID env var (current: {file_id})"}
            if attempt < 2:
                _reconnect()
            else:
                print(f"❌ Drive save failed after 3 attempts: {e}")
                return {"ok": False, "reason": str(e)}

    return {"ok": False, "reason": "Max retries exceeded"}


def load_from_drive(local_path, filename="tenders_db.json") -> bool:
    """Load tenders_db.json from Google Drive on startup."""
    global _drive_service

    if not _drive_service:
        return False

    file_id = _get_db_file_id()
    if not file_id:
        return False

    for attempt in range(3):
        try:
            from googleapiclient.http import MediaIoBaseDownload
            request = _drive_service.files().get_media(fileId=file_id)
            buf  = io.BytesIO()
            dl   = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = dl.next_chunk()

            content = buf.getvalue()
            if len(content) < 10:
                print(f"⚠️  Drive file is empty (size={len(content)})")
                return False

            # Validate JSON
            parsed = json.loads(content)
            if "tenders" not in parsed:
                print("⚠️  Drive file has no 'tenders' key")
                return False

            local_path = Path(local_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(content)
            count = len(parsed.get("tenders", {}))
            print(f"✅ Loaded {count} tenders from Google Drive")
            return True

        except Exception as e:
            err = str(e)
            if "403" in err:
                sa = get_service_account_email()
                print(f"❌ Drive read 403: Share file with {sa} as Editor")
                return False
            if "404" in err:
                fid = _get_db_file_id()
                print(f"❌ Drive read 404: File ID {fid} not found")
                return False
            if attempt < 2:
                _reconnect()
            else:
                print(f"❌ Drive load failed: {e}")
                return False

    return False

# ── Tender File Storage ───────────────────────────────────────

def upload_tender_file(local_path: str, t247_id: str) -> dict:
    """Upload a tender document (ZIP/PDF) to Drive under tender_files/{t247_id}/"""
    global _drive_service

    if not _drive_service:
        return {"ok": False, "reason": "Drive not connected"}

    local_path = Path(local_path)
    if not local_path.exists():
        return {"ok": False, "reason": "File not found"}

    try:
        import mimetypes
        from googleapiclient.http import MediaIoBaseUpload

        # Find or create folder for this tender
        folder_name = f"tender_{t247_id}"
        q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = _drive_service.files().list(q=q, fields="files(id,name)").execute()
        folders = results.get("files", [])

        if folders:
            folder_id = folders[0]["id"]
        else:
            meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
            folder = _drive_service.files().create(body=meta, fields="id").execute()
            folder_id = folder["id"]

        # Upload file
        mime = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        meta = {"name": local_path.name, "parents": [folder_id]}
        content = local_path.read_bytes()
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime, resumable=False)
        f = _drive_service.files().create(body=meta, media_body=media, fields="id").execute()
        return {"ok": True, "file_id": f["id"], "folder_id": folder_id}

    except Exception as e:
        return {"ok": False, "reason": str(e)}


def download_tender_file(t247_id: str, filename: str, dest_path: str) -> bool:
    """Download a previously uploaded tender file from Drive."""
    global _drive_service
    if not _drive_service:
        return False
    try:
        from googleapiclient.http import MediaIoBaseDownload
        q = f"name='{filename}' and trashed=false"
        results = _drive_service.files().list(q=q, fields="files(id,name)").execute()
        files = results.get("files", [])
        if not files:
            return False
        file_id = files[0]["id"]
        request = _drive_service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        Path(dest_path).write_bytes(buf.getvalue())
        return True
    except Exception:
        return False


def list_tender_files(t247_id: str) -> list:
    """List files stored in Drive for a tender."""
    global _drive_service
    if not _drive_service:
        return []
    try:
        folder_name = f"tender_{t247_id}"
        q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = _drive_service.files().list(q=q, fields="files(id,name)").execute()
        folders = results.get("files", [])
        if not folders:
            return []
        folder_id = folders[0]["id"]
        q2 = f"'{folder_id}' in parents and trashed=false"
        r2 = _drive_service.files().list(q=q2, fields="files(id,name,size,modifiedTime)").execute()
        return r2.get("files", [])
    except Exception:
        return []


def delete_tender_file(file_id: str) -> bool:
    """Delete a file from Drive by its file ID."""
    global _drive_service
    if not _drive_service:
        return False
    try:
        _drive_service.files().delete(fileId=file_id).execute()
        return True
    except Exception:
        return False
