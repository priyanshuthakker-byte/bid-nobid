"""
Google Drive Sync — saves/loads tenders_db.json to Google Drive
Completely free using Google Drive API v3
"""
import json, os, io
from pathlib import Path

_drive_service = None
_file_id_cache = {}
_credentials = None

def init_drive():
    global _drive_service, _credentials
    try:
        creds_json = os.environ.get("GDRIVE_CREDENTIALS")
        if not creds_json:
            print("⚠️  GDRIVE_CREDENTIALS env var not set")
            return False
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds_data = json.loads(creds_json)
        _credentials = creds_data  # Store for reconnection
        creds = Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        _drive_service = build("drive", "v3", credentials=creds)
        _drive_service.files().list(pageSize=1, fields="files(id)").execute()
        print("✅ Google Drive connected successfully")
        return True
    except json.JSONDecodeError as e:
        print(f"❌ GDRIVE_CREDENTIALS is not valid JSON: {e}")
        return False
    except Exception as e:
        print(f"❌ Google Drive init failed: {e}")
        return False

def _reconnect():
    """Reconnect to Drive if connection broke."""
    global _drive_service, _credentials
    try:
        if not _credentials:
            return False
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_info(
            _credentials,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        _drive_service = build("drive", "v3", credentials=creds)
        print("🔄 Google Drive reconnected")
        return True
    except Exception as e:
        print(f"❌ Drive reconnect failed: {e}")
        return False

def _get_or_create_file(filename, folder_id=None):
    global _drive_service, _file_id_cache
    if filename in _file_id_cache:
        return _file_id_cache[filename]
    try:
        q = f"name='{filename}' and trashed=false"
        if folder_id:
            q += f" and '{folder_id}' in parents"
        results = _drive_service.files().list(q=q, fields="files(id,name)", pageSize=10).execute()
        files = results.get("files", [])
        if files:
            fid = files[0]["id"]
            _file_id_cache[filename] = fid
            print(f"📄 Found Drive file: {filename} ({fid})")
            return fid
        meta = {"name": filename}
        if folder_id:
            meta["parents"] = [folder_id]
        f = _drive_service.files().create(body=meta, fields="id").execute()
        fid = f["id"]
        _file_id_cache[filename] = fid
        print(f"📄 Created Drive file: {filename} ({fid})")
        return fid
    except Exception as e:
        print(f"❌ Drive file lookup failed: {e}")
        return None

def save_to_drive(local_path, filename="tenders_db.json"):
    global _drive_service
    if not _drive_service:
        return False
    # Try up to 2 times — reconnect if broken pipe
    for attempt in range(2):
        try:
            from googleapiclient.http import MediaIoBaseUpload
            folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
            file_id = _get_or_create_file(filename, folder_id if folder_id else None)
            content = local_path.read_bytes()
            media = MediaIoBaseUpload(
                io.BytesIO(content),
                mimetype="application/json",
                resumable=False
            )
            if file_id:
                _drive_service.files().update(fileId=file_id, media_body=media).execute()
            else:
                meta = {"name": filename}
                if folder_id:
                    meta["parents"] = [folder_id]
                result = _drive_service.files().create(
                    body=meta, media_body=media, fields="id"
                ).execute()
                _file_id_cache[filename] = result["id"]
            print(f"✅ Saved {filename} to Drive ({len(content)//1024} KB)")
            return True
        except BrokenPipeError:
            print(f"🔄 Drive broken pipe on attempt {attempt+1} — reconnecting...")
            _file_id_cache.clear()
            if not _reconnect():
                return False
        except Exception as e:
            if attempt == 0 and ("pipe" in str(e).lower() or "connection" in str(e).lower()):
                print(f"🔄 Drive connection error — reconnecting: {e}")
                _file_id_cache.clear()
                _reconnect()
            else:
                print(f"❌ Drive save failed: {e}")
                return False
    return False

def load_from_drive(local_path, filename="tenders_db.json"):
    global _drive_service
    if not _drive_service:
        return False
    try:
        from googleapiclient.http import MediaIoBaseDownload
        folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
        file_id = _get_or_create_file(filename, folder_id if folder_id else None)
        if not file_id:
            print(f"⚠️  No Drive file found for {filename}")
            return False
        request = _drive_service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()
        if len(data) < 10:
            print(f"⚠️  Drive file is empty")
            return False
        try:
            parsed = json.loads(data)
            tender_count = len(parsed.get("tenders", {}))
            if tender_count == 0:
                print(f"⚠️  Drive file has 0 tenders — skipping")
                return False
        except json.JSONDecodeError:
            print(f"❌ Drive file is not valid JSON")
            return False
        local_path.parent.mkdir(exist_ok=True, parents=True)
        local_path.write_bytes(data)
        print(f"✅ Loaded {filename} from Drive ({len(data)//1024} KB, {tender_count} tenders)")
        return True
    except Exception as e:
        print(f"❌ Drive load failed: {e}")
        return False

def is_available():
    return _drive_service is not None


# ── AUTH MODE ─────────────────────────────────────────────────
_auth_mode = "none"

def get_auth_mode() -> str:
    """Return current auth mode: 'service_account' or 'none'."""
    if _drive_service is not None:
        return "service_account"
    return "none"


# ── VAULT FOLDER ─────────────────────────────────────────────
VAULT_FOLDER_NAME = "nascent_vault"

def _get_vault_folder_id():
    """Get or create the nascent_vault folder in Drive. Returns folder_id or None."""
    if not _drive_service:
        return None
    try:
        parent_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip() or None
        q = f"name='{VAULT_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        results = _drive_service.files().list(q=q, fields="files(id,name)", pageSize=5).execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]
        # Create it
        meta = {"name": VAULT_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            meta["parents"] = [parent_id]
        f = _drive_service.files().create(body=meta, fields="id").execute()
        print(f"Created vault folder in Drive: {VAULT_FOLDER_NAME} ({f['id']})")
        return f["id"]
    except Exception as e:
        print(f"Drive vault folder error: {e}")
        return None


# ── VAULT FUNCTIONS ───────────────────────────────────────────

def vault_upload(file_bytes: bytes, filename: str, category: str = "general") -> dict:
    """Upload a document to the Drive vault folder."""
    if not _drive_service:
        return {"success": False, "error": "Drive not connected"}
    try:
        from googleapiclient.http import MediaIoBaseUpload
        from pathlib import Path
        vault_id = _get_vault_folder_id()
        if not vault_id:
            return {"success": False, "error": "Could not create vault folder in Drive"}

        ext = Path(filename).suffix.lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        mimetype = mime_map.get(ext, "application/octet-stream")
        safe_name = f"{category}_{filename}"

        # Check if file already exists — update it
        q = f"name='{safe_name}' and '{vault_id}' in parents and trashed=false"
        existing = _drive_service.files().list(q=q, fields="files(id,name)", pageSize=5).execute().get("files", [])
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mimetype, resumable=False)

        if existing:
            file_id = existing[0]["id"]
            _drive_service.files().update(fileId=file_id, media_body=media).execute()
        else:
            meta = {"name": safe_name, "parents": [vault_id]}
            result = _drive_service.files().create(body=meta, media_body=media, fields="id").execute()
            file_id = result["id"]

        drive_url = f"https://drive.google.com/file/d/{file_id}/view"
        print(f"Vault upload: {safe_name} ({len(file_bytes)//1024} KB)")
        return {
            "success": True,
            "file_id": file_id,
            "filename": safe_name,
            "category": category,
            "drive_url": drive_url,
            "size_kb": len(file_bytes) // 1024,
        }
    except Exception as e:
        print(f"Vault upload failed: {e}")
        return {"success": False, "error": str(e)}


def vault_download(file_id: str) -> bytes:
    """Download a document from the Drive vault by file_id. Returns raw bytes."""
    if not _drive_service:
        return b""
    try:
        from googleapiclient.http import MediaIoBaseDownload
        request = _drive_service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()
    except Exception as e:
        print(f"Vault download failed for {file_id}: {e}")
        return b""


def vault_list() -> list:
    """List all documents in the Drive vault folder."""
    if not _drive_service:
        return []
    try:
        vault_id = _get_vault_folder_id()
        if not vault_id:
            return []
        results = _drive_service.files().list(
            q=f"'{vault_id}' in parents and trashed=false",
            fields="files(id,name,size,mimeType,createdTime,modifiedTime)",
            pageSize=100,
            orderBy="name"
        ).execute()
        files = results.get("files", [])
        return [
            {
                "file_id": f["id"],
                "filename": f["name"],
                "size_kb": round(int(f.get("size", 0)) / 1024, 1),
                "mime_type": f.get("mimeType", ""),
                "created": f.get("createdTime", ""),
                "modified": f.get("modifiedTime", ""),
                "drive_url": f"https://drive.google.com/file/d/{f['id']}/view",
                "category": f["name"].split("_")[0] if "_" in f["name"] else "general",
                "local_only": False,
            }
            for f in files
        ]
    except Exception as e:
        print(f"Vault list failed: {e}")
        return []


def vault_delete(file_id: str) -> bool:
    """Permanently delete a file from the Drive vault."""
    if not _drive_service:
        return False
    try:
        _drive_service.files().delete(fileId=file_id).execute()
        # Clear from cache
        to_remove = [k for k, v in _file_id_cache.items() if v == file_id]
        for k in to_remove:
            del _file_id_cache[k]
        return True
    except Exception as e:
        print(f"Vault delete failed for {file_id}: {e}")
        return False
