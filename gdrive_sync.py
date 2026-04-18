"""
Google Drive Sync — saves/loads tenders_db.json and nascent_profile.json

IMPORTANT: Service accounts have NO storage quota.
Fix: Share a folder from your personal Google Drive with the service account email.
The GDRIVE_FOLDER_ID must be a folder you own and have shared with the service account
with "Editor" permission. Files will then use YOUR quota, not the service account's.

Steps to fix 403 storageQuotaExceeded:
1. Open Google Drive
2. Create a folder (e.g. "NIT-BidNoBid")
3. Right-click → Share → add service account email with Editor permission
4. Get the folder ID from the URL: drive.google.com/drive/folders/FOLDER_ID_HERE
5. Set GDRIVE_FOLDER_ID=FOLDER_ID_HERE in Render environment variables
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
        _credentials = creds_data
        creds = Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        _drive_service = build("drive", "v3", credentials=creds)
        # Test connection
        _drive_service.files().list(pageSize=1, fields="files(id)").execute()
        sa_email = creds_data.get("client_email", "unknown")
        print(f"✅ Google Drive connected — service account: {sa_email}")
        print(f"   Folder ID: {os.environ.get('GDRIVE_FOLDER_ID','NOT SET')}")
        return True
    except json.JSONDecodeError as e:
        print(f"❌ GDRIVE_CREDENTIALS is not valid JSON: {e}")
        return False
    except Exception as e:
        print(f"❌ Google Drive init failed: {e}")
        return False


def get_service_account_email():
    """Return the service account email for sharing instructions."""
    creds_json = os.environ.get("GDRIVE_CREDENTIALS", "")
    if not creds_json:
        return None
    try:
        return json.loads(creds_json).get("client_email", None)
    except Exception:
        return None


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
        print("🔄 Google Drive reconnected")
        return True
    except Exception as e:
        print(f"❌ Drive reconnect failed: {e}")
        return False


def _get_folder_id():
    """Get folder ID from env. Strips full URLs to just the ID."""
    raw = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
    if not raw:
        return None
    # Strip full URL if user pasted URL instead of just ID
    # e.g. https://drive.google.com/drive/folders/1QQhYIl...?usp=sharing -> 1QQhYIl...
    if "folders/" in raw:
        raw = raw.split("folders/")[-1].split("?")[0].split("/")[0].strip()
    if "?usp=" in raw:
        raw = raw.split("?usp=")[0].strip()
    return raw or None


def _get_or_create_file(filename, folder_id=None):
    global _drive_service, _file_id_cache
    if filename in _file_id_cache:
        return _file_id_cache[filename]
    try:
        q = f"name='{filename}' and trashed=false"
        if folder_id:
            q += f" and '{folder_id}' in parents"
        results = _drive_service.files().list(
            q=q, fields="files(id,name,parents)", pageSize=10
        ).execute()
        files = results.get("files", [])
        if files:
            fid = files[0]["id"]
            _file_id_cache[filename] = fid
            print(f"📄 Found Drive file: {filename} ({fid})")
            return fid
        # Create new file in the shared folder
        meta = {"name": filename}
        if folder_id:
            meta["parents"] = [folder_id]
        f = _drive_service.files().create(
            body=meta, fields="id",
            # supportsAllDrives allows shared drives too
            supportsAllDrives=True
        ).execute()
        fid = f["id"]
        _file_id_cache[filename] = fid
        print(f"📄 Created Drive file: {filename} ({fid})")
        return fid
    except Exception as e:
        print(f"❌ Drive file lookup failed for {filename}: {e}")
        return None


def save_to_drive(local_path, filename="tenders_db.json", content_bytes=None):
    global _drive_service
    if not _drive_service:
        return False

    folder_id = _get_folder_id()
    if not folder_id:
        print("❌ GDRIVE_FOLDER_ID not set — cannot save to Drive")
        print("   Create a folder in Google Drive, share it with the service account,")
        print(f"   and set GDRIVE_FOLDER_ID in Render env vars.")
        return False

    for attempt in range(2):
        try:
            from googleapiclient.http import MediaIoBaseUpload
            file_id = _get_or_create_file(filename, folder_id)
            if content_bytes is not None:
                content = content_bytes
            elif hasattr(local_path, 'read_bytes'):
                content = local_path.read_bytes()
            else:
                content = str(local_path).encode()
            # Guard: never upload empty or trivially small content
            if len(content) < 10:
                print(f"⚠️ Skipping Drive upload of {filename} — content too small ({len(content)} bytes)")
                return False
            # Guard: for tenders_db.json, verify it has tenders
            if filename == "tenders_db.json":
                try:
                    parsed = json.loads(content)
                    if not parsed.get("tenders"):
                        print(f"⚠️ Skipping Drive upload — tenders_db has 0 tenders (would overwrite good data)")
                        return False
                except Exception:
                    print(f"⚠️ Skipping Drive upload — tenders_db is not valid JSON")
                    return False
            media = MediaIoBaseUpload(
                io.BytesIO(content),
                mimetype="application/json",
                resumable=False
            )
            if file_id:
                _drive_service.files().update(
                    fileId=file_id,
                    media_body=media,
                    supportsAllDrives=True
                ).execute()
            else:
                meta = {"name": filename, "parents": [folder_id]}
                result = _drive_service.files().create(
                    body=meta, media_body=media, fields="id",
                    supportsAllDrives=True
                ).execute()
                _file_id_cache[filename] = result["id"]
            print(f"✅ Saved {filename} to Drive ({len(content)//1024} KB)")
            return True

        except Exception as e:
            err_str = str(e)
            if "storageQuotaExceeded" in err_str or "storage quota" in err_str.lower():
                sa_email = get_service_account_email()
                print(f"❌ Drive save failed: Service account has no storage quota.")
                print(f"   FIX: Share your Google Drive folder with {sa_email}")
                print(f"   Steps:")
                print(f"   1. Open drive.google.com")
                print(f"   2. Create folder 'NIT-BidNoBid'")
                print(f"   3. Right-click → Share → add {sa_email} as Editor")
                print(f"   4. Copy folder ID from URL and set as GDRIVE_FOLDER_ID in Render")
                return False
            if attempt == 0 and ("pipe" in err_str.lower() or "connection" in err_str.lower()):
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
        folder_id = _get_folder_id()
        file_id = _get_or_create_file(filename, folder_id)
        if not file_id:
            print(f"⚠️  No Drive file found for {filename}")
            return False
        request = _drive_service.files().get_media(
            fileId=file_id, supportsAllDrives=True
        )
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()
        if len(data) < 5:
            print(f"⚠️  Drive file is empty: {filename}")
            return False
        try:
            parsed = json.loads(data)
            if filename == "tenders_db.json":
                tender_count = len(parsed.get("tenders", {}))
                print(f"✅ Loaded {filename} from Drive ({len(data)//1024} KB, {tender_count} tenders)")
            else:
                print(f"✅ Loaded {filename} from Drive ({len(data)//1024} KB)")
        except json.JSONDecodeError:
            print(f"❌ Drive file is not valid JSON: {filename}")
            return False
        local_path.parent.mkdir(exist_ok=True, parents=True)
        local_path.write_bytes(data)
        return True
    except Exception as e:
        print(f"❌ Drive load failed for {filename}: {e}")
        return False


def is_available():
    return _drive_service is not None


_auth_mode = "none"

def get_auth_mode() -> str:
    if _drive_service is not None:
        return "service_account"
    return "none"


def get_drive_diagnostic():
    """Return diagnostic info to help user fix Drive issues."""
    sa_email = get_service_account_email()
    folder_id = _get_folder_id()
    creds_set = bool(os.environ.get("GDRIVE_CREDENTIALS", "").strip())
    return {
        "connected": is_available(),
        "credentials_set": creds_set,
        "folder_id_set": bool(folder_id),
        "folder_id": folder_id,
        "service_account_email": sa_email,
        "fix_instructions": (
            f"Share a Google Drive folder with {sa_email} as Editor, "
            f"then set GDRIVE_FOLDER_ID to that folder's ID in Render env vars."
        ) if sa_email and not folder_id else (
            f"Share your Google Drive folder with {sa_email} as Editor." 
        ) if sa_email else "Set GDRIVE_CREDENTIALS in Render environment variables.",
    }


# Vault functions
def _get_vault_folder_id() -> str:
    """Get or create a 'vault' subfolder inside the main Drive folder."""
    if not _drive_service:
        return ""
    parent_id = _get_folder_id()
    if not parent_id:
        return ""
    try:
        q = f"name='vault' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        res = _drive_service.files().list(q=q, fields="files(id)", supportsAllDrives=True).execute()
        files = res.get("files", [])
        if files:
            return files[0]["id"]
        # Create vault subfolder
        meta = {"name": "vault", "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
        f = _drive_service.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
        return f.get("id", "")
    except Exception as e:
        print(f"❌ Vault folder error: {e}")
        return parent_id  # fallback to root folder


def vault_upload(file_bytes: bytes, filename: str, category: str = "general") -> dict:
    if not _drive_service:
        return {"success": False, "error": "Drive not connected"}
    vault_id = _get_vault_folder_id()
    if not vault_id:
        return {"success": False, "error": "GDRIVE_FOLDER_ID not set"}
    try:
        from googleapiclient.http import MediaIoBaseUpload
        import mimetypes
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        # Store with category prefix for easy filtering
        stored_name = f"[{category}] {filename}"
        meta = {"name": stored_name, "parents": [vault_id]}
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime, resumable=False)
        result = _drive_service.files().create(
            body=meta, media_body=media, fields="id,name,size,mimeType",
            supportsAllDrives=True
        ).execute()
        return {"success": True, "file_id": result["id"], "name": result["name"], "size": result.get("size", 0)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def vault_list() -> list:
    if not _drive_service:
        return []
    vault_id = _get_vault_folder_id()
    if not vault_id:
        return []
    try:
        q = f"'{vault_id}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'"
        results = _drive_service.files().list(
            q=q, fields="files(id,name,size,modifiedTime,mimeType)",
            orderBy="modifiedTime desc",
            pageSize=200, supportsAllDrives=True
        ).execute()
        files = results.get("files", [])
        # Parse category from [category] prefix
        for f in files:
            name = f.get("name", "")
            if name.startswith("[") and "]" in name:
                f["category"] = name[1:name.index("]")]
                f["display_name"] = name[name.index("]")+2:]
            else:
                f["category"] = "general"
                f["display_name"] = name
        return files
    except Exception as e:
        print(f"❌ Vault list failed: {e}")
        return []


def vault_download(file_id: str) -> bytes:
    if not _drive_service:
        return b""
    try:
        from googleapiclient.http import MediaIoBaseDownload
        request = _drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()
    except Exception as e:
        print(f"❌ Vault download failed: {e}")
        return b""


def vault_delete(file_id: str) -> bool:
    if not _drive_service:
        return False
    try:
        _drive_service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        return True
    except Exception as e:
        print(f"❌ Vault delete failed: {e}")
        return False
