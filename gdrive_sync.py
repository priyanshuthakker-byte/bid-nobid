"""
Google Drive Sync v2 — saves/loads tenders_db.json + document vault.
Vault: uploaded company documents (PAN, certs, completion certs, etc.)
are stored in a Drive subfolder and survive Render restarts.
"""

import json, os, io
from pathlib import Path

_drive_service = None
_file_id_cache = {}
_credentials = None

VAULT_FOLDER_NAME = "nascent_vault"


def init_drive():
    global _drive_service, _credentials
    try:
        creds_json = os.environ.get("GDRIVE_CREDENTIALS")
        if not creds_json:
            print("GDRIVE_CREDENTIALS env var not set")
            return False
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds_data = json.loads(creds_json)
        _credentials = creds_data
        creds = Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        _drive_service = build("drive", "v3", credentials=creds)
        _drive_service.files().list(pageSize=1, fields="files(id)").execute()
        print("Google Drive connected successfully")
        return True
    except json.JSONDecodeError as e:
        print(f"GDRIVE_CREDENTIALS is not valid JSON: {e}")
        return False
    except Exception as e:
        print(f"Google Drive init failed: {e}")
        return False


def _reconnect():
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
        print("Google Drive reconnected")
        return True
    except Exception as e:
        print(f"Drive reconnect failed: {e}")
        return False


def _get_or_create_file(filename, folder_id=None):
    global _drive_service, _file_id_cache
    cache_key = f"{folder_id or ''}:{filename}"
    if cache_key in _file_id_cache:
        return _file_id_cache[cache_key]
    try:
        q = f"name='{filename}' and trashed=false"
        if folder_id:
            q += f" and '{folder_id}' in parents"
        results = _drive_service.files().list(q=q, fields="files(id,name)", pageSize=10).execute()
        files = results.get("files", [])
        if files:
            fid = files[0]["id"]
            _file_id_cache[cache_key] = fid
            return fid
        meta = {"name": filename}
        if folder_id:
            meta["parents"] = [folder_id]
        f = _drive_service.files().create(body=meta, fields="id").execute()
        fid = f["id"]
        _file_id_cache[cache_key] = fid
        print(f"Created Drive file: {filename} ({fid})")
        return fid
    except Exception as e:
        print(f"Drive file lookup failed: {e}")
        return None


def _get_or_create_folder(folder_name, parent_id=None):
    """Get or create a folder in Drive. Returns folder_id."""
    global _drive_service, _file_id_cache
    cache_key = f"folder:{parent_id or ''}:{folder_name}"
    if cache_key in _file_id_cache:
        return _file_id_cache[cache_key]
    try:
        q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        results = _drive_service.files().list(q=q, fields="files(id,name)", pageSize=5).execute()
        files = results.get("files", [])
        if files:
            fid = files[0]["id"]
            _file_id_cache[cache_key] = fid
            return fid
        meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            meta["parents"] = [parent_id]
        f = _drive_service.files().create(body=meta, fields="id").execute()
        fid = f["id"]
        _file_id_cache[cache_key] = fid
        print(f"Created Drive folder: {folder_name} ({fid})")
        return fid
    except Exception as e:
        print(f"Drive folder create failed: {e}")
        return None


def save_to_drive(local_path, filename="tenders_db.json"):
    global _drive_service
    if not _drive_service:
        return False
    for attempt in range(2):
        try:
            from googleapiclient.http import MediaIoBaseUpload
            folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip() or None
            file_id = _get_or_create_file(filename, folder_id)
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
                cache_key = f"{folder_id or ''}:{filename}"
                _file_id_cache[cache_key] = result["id"]
            print(f"Saved {filename} to Drive ({len(content)//1024} KB)")
            return True
        except BrokenPipeError:
            _file_id_cache.clear()
            if not _reconnect():
                return False
        except Exception as e:
            if attempt == 0 and ("pipe" in str(e).lower() or "connection" in str(e).lower()):
                _file_id_cache.clear()
                _reconnect()
            else:
                print(f"Drive save failed: {e}")
                return False
    return False


def load_from_drive(local_path, filename="tenders_db.json"):
    global _drive_service
    if not _drive_service:
        return False
    try:
        from googleapiclient.http import MediaIoBaseDownload
        folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip() or None
        file_id = _get_or_create_file(filename, folder_id)
        if not file_id:
            print(f"No Drive file found for {filename}")
            return False
        request = _drive_service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()
        if len(data) < 10:
            print("Drive file is empty")
            return False
        try:
            parsed = json.loads(data)
            tender_count = len(parsed.get("tenders", {}))
            if tender_count == 0:
                print("Drive file has 0 tenders — skipping")
                return False
        except json.JSONDecodeError:
            print("Drive file is not valid JSON")
            return False
        local_path.parent.mkdir(exist_ok=True, parents=True)
        local_path.write_bytes(data)
        print(f"Loaded {filename} from Drive ({len(data)//1024} KB, {tender_count} tenders)")
        return True
    except Exception as e:
        print(f"Drive load failed: {e}")
        return False


# ── VAULT: Document upload/download ────────────────────────────

def _get_vault_folder_id():
    """Get or create the nascent_vault folder in Drive."""
    if not _drive_service:
        return None
    parent_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip() or None
    return _get_or_create_folder(VAULT_FOLDER_NAME, parent_id)


def vault_upload(file_bytes: bytes, filename: str, category: str = "general") -> dict:
    """
    Upload a document to the vault folder in Drive.
    category: 'company', 'financial', 'certification', 'project', 'legal', 'general'
    Returns: {success, file_id, drive_url, filename}
    """
    if not _drive_service:
        return {"success": False, "error": "Drive not connected"}
    try:
        from googleapiclient.http import MediaIoBaseUpload

        vault_id = _get_vault_folder_id()
        if not vault_id:
            return {"success": False, "error": "Could not create vault folder in Drive"}

        # Determine MIME type
        ext = Path(filename).suffix.lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        mimetype = mime_map.get(ext, "application/octet-stream")

        # Check if file already exists in vault
        safe_name = f"{category}_{filename}"
        cache_key = f"{vault_id}:{safe_name}"
        q = f"name='{safe_name}' and '{vault_id}' in parents and trashed=false"
        results = _drive_service.files().list(q=q, fields="files(id,name)", pageSize=5).execute()
        existing = results.get("files", [])

        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mimetype, resumable=False)

        if existing:
            # Update existing
            file_id = existing[0]["id"]
            _drive_service.files().update(fileId=file_id, media_body=media).execute()
        else:
            # Create new
            meta = {"name": safe_name, "parents": [vault_id]}
            result = _drive_service.files().create(body=meta, media_body=media, fields="id").execute()
            file_id = result["id"]
            _file_id_cache[cache_key] = file_id

        drive_url = f"https://drive.google.com/file/d/{file_id}/view"
        print(f"Vault upload: {safe_name} ({len(file_bytes)//1024} KB) -> {file_id}")
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
    """Download a document from the vault by Drive file_id. Returns raw bytes."""
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
    """List all documents in the vault folder."""
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
            }
            for f in files
        ]
    except Exception as e:
        print(f"Vault list failed: {e}")
        return []


def vault_delete(file_id: str) -> bool:
    """Delete a document from the vault."""
    if not _drive_service:
        return False
    try:
        _drive_service.files().delete(fileId=file_id).execute()
        # Clear cache entries for this file_id
        to_remove = [k for k, v in _file_id_cache.items() if v == file_id]
        for k in to_remove:
            del _file_id_cache[k]
        return True
    except Exception as e:
        print(f"Vault delete failed for {file_id}: {e}")
        return False


def save_profile_to_drive(profile_data: dict) -> bool:
    """Save nascent_profile.json to Drive alongside tenders_db.json."""
    if not _drive_service:
        return False
    try:
        from googleapiclient.http import MediaIoBaseUpload
        folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip() or None
        filename = "nascent_profile.json"
        file_id = _get_or_create_file(filename, folder_id)
        content = json.dumps(profile_data, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json", resumable=False)
        if file_id:
            _drive_service.files().update(fileId=file_id, media_body=media).execute()
        else:
            meta = {"name": filename}
            if folder_id:
                meta["parents"] = [folder_id]
            _drive_service.files().create(body=meta, media_body=media, fields="id").execute()
        print(f"Profile saved to Drive ({len(content)//1024} KB)")
        return True
    except Exception as e:
        print(f"Profile Drive save failed: {e}")
        return False


def load_profile_from_drive(local_path: Path) -> bool:
    """Load nascent_profile.json from Drive."""
    if not _drive_service:
        return False
    try:
        from googleapiclient.http import MediaIoBaseDownload
        folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip() or None
        file_id = _get_or_create_file("nascent_profile.json", folder_id)
        if not file_id:
            return False
        request = _drive_service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()
        if len(data) < 10:
            return False
        local_path.write_bytes(data)
        print(f"Profile loaded from Drive ({len(data)//1024} KB)")
        return True
    except Exception as e:
        print(f"Profile Drive load failed: {e}")
        return False


def is_available():
    return _drive_service is not None
