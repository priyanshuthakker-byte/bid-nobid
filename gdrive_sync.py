"""
gdrive_sync.py — Google Drive Sync v3
CRITICAL FIX: Service accounts have zero storage quota.
Files MUST be written into a shared folder (GDRIVE_FOLDER_ID) that the user
has shared with the service account from their personal Google Drive.

Without GDRIVE_FOLDER_ID: writes to service account's own Drive = 403 storageQuotaExceeded
With GDRIVE_FOLDER_ID set: writes into your personal Drive folder = works correctly

Setup once:
  1. Create a folder in your Google Drive
  2. Share it with the service account email (Editor access)
  3. Copy the folder ID from the URL
  4. Set GDRIVE_FOLDER_ID=<folder_id> in Render environment variables
"""

import json, os, io
from pathlib import Path

_drive_service = None
_file_id_cache = {}
_credentials   = None


def init_drive():
    global _drive_service, _credentials, _file_id_cache
    _file_id_cache = {}
    try:
        creds_json = os.environ.get("GDRIVE_CREDENTIALS", "").strip()
        if not creds_json:
            print("⚠️ GDRIVE_CREDENTIALS env var not set")
            return False
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds_data   = json.loads(creds_json)
        _credentials = creds_data
        creds = Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        _drive_service = build("drive", "v3", credentials=creds)
        # Verify connection with a lightweight call
        _drive_service.files().list(pageSize=1, fields="files(id)").execute()

        folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
        if not folder_id:
            print("⚠️ GDRIVE_FOLDER_ID not set — Drive writes WILL fail (service accounts have no storage quota)")
            print("   Fix: Create a Drive folder, share with service account email, set GDRIVE_FOLDER_ID in Render")
        else:
            print(f"✅ Google Drive connected | Folder: {folder_id}")
        return True
    except json.JSONDecodeError as e:
        print(f"❌ GDRIVE_CREDENTIALS is not valid JSON: {e}")
        return False
    except Exception as e:
        print(f"❌ Google Drive init failed: {e}")
        return False


def _reconnect():
    global _drive_service, _credentials, _file_id_cache
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
        _file_id_cache = {}
        return True
    except Exception as e:
        print(f"❌ Drive reconnect failed: {e}")
        return False


def _get_folder_id() -> str:
    """Get GDRIVE_FOLDER_ID. Warn clearly if not set."""
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
    if not folder_id:
        # Do NOT proceed without folder_id — will always fail with 403
        raise RuntimeError(
            "GDRIVE_FOLDER_ID env var is not set. "
            "Service accounts cannot write to their own Drive storage. "
            "Fix: Create a Google Drive folder, share with service account email (Editor), "
            "copy the folder ID from the URL, and set GDRIVE_FOLDER_ID in Render."
        )
    return folder_id


def _get_or_create_file(filename: str, folder_id: str) -> str:
    """Find existing file in folder or create a new one. Returns file ID."""
    global _drive_service, _file_id_cache

    if filename in _file_id_cache:
        return _file_id_cache[filename]

    try:
        q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        results = _drive_service.files().list(
            q=q, fields="files(id,name,size)", pageSize=10
        ).execute()
        files = results.get("files", [])

        if files:
            # Pick file with most content (avoids stale 0-byte files)
            files_sorted = sorted(files, key=lambda f: int(f.get("size", 0)), reverse=True)
            fid = files_sorted[0]["id"]
            size_kb = int(files_sorted[0].get("size", 0)) // 1024
            _file_id_cache[filename] = fid
            print(f"📄 Found Drive file: {filename} ({fid}, {size_kb} KB)")
            return fid

        # Create new file inside the shared folder
        meta = {"name": filename, "parents": [folder_id]}
        f   = _drive_service.files().create(body=meta, fields="id").execute()
        fid = f["id"]
        _file_id_cache[filename] = fid
        print(f"📄 Created Drive file: {filename} ({fid}) in folder {folder_id}")
        return fid

    except Exception as e:
        print(f"❌ Drive file lookup failed for {filename}: {e}")
        return None


def save_to_drive(local_path, filename: str = None) -> bool:
    """Upload/update a file in the shared Drive folder."""
    global _drive_service
    if not _drive_service:
        return False

    if filename is None:
        filename = local_path.name if hasattr(local_path, "name") else str(local_path).split("/")[-1]

    for attempt in range(2):
        try:
            folder_id = _get_folder_id()
            from googleapiclient.http import MediaIoBaseUpload

            content = local_path.read_bytes() if hasattr(local_path, "read_bytes") else Path(local_path).read_bytes()
            file_id = _get_or_create_file(filename, folder_id)

            # Detect mimetype
            ext = str(filename).lower().rsplit(".", 1)[-1] if "." in str(filename) else ""
            mimetypes = {
                "json": "application/json",
                "pdf":  "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "png":  "image/png",
                "jpg":  "image/jpeg",
                "jpeg": "image/jpeg",
            }
            mimetype = mimetypes.get(ext, "application/octet-stream")

            media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=False)

            if file_id:
                _drive_service.files().update(fileId=file_id, media_body=media).execute()
            else:
                meta   = {"name": filename, "parents": [folder_id]}
                result = _drive_service.files().create(
                    body=meta, media_body=media, fields="id"
                ).execute()
                _file_id_cache[filename] = result["id"]

            size_kb = len(content) // 1024
            print(f"✅ Saved {filename} to Drive ({size_kb} KB)")
            return True

        except RuntimeError as e:
            # GDRIVE_FOLDER_ID missing — log clearly, don't retry
            print(f"❌ Drive save skipped: {e}")
            return False
        except Exception as e:
            err_str = str(e)
            if "storageQuotaExceeded" in err_str:
                print(
                    "❌ Drive save failed: storageQuotaExceeded\n"
                    "   CAUSE: GDRIVE_FOLDER_ID is not set correctly, or the folder is not shared with the service account.\n"
                    "   FIX: Share your Drive folder with the service account email (Editor access) and set GDRIVE_FOLDER_ID in Render."
                )
                return False
            if attempt == 0 and any(k in err_str.lower() for k in ["pipe", "connection", "broken"]):
                _file_id_cache.clear()
                _reconnect()
                continue
            print(f"❌ Drive save failed: {e}")
            return False
    return False


def load_from_drive(local_path, filename: str = None) -> bool:
    """Download a file from the shared Drive folder to local_path."""
    global _drive_service
    if not _drive_service:
        return False

    if filename is None:
        filename = local_path.name if hasattr(local_path, "name") else str(local_path).split("/")[-1]

    try:
        folder_id = _get_folder_id()
        from googleapiclient.http import MediaIoBaseDownload

        file_id = _get_or_create_file(filename, folder_id)
        if not file_id:
            return False

        request = _drive_service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl  = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()

        data = buf.getvalue()
        if len(data) < 10:
            print(f"⚠️ Drive file {filename} is empty ({len(data)} bytes) — skipping restore")
            return False

        # Validate JSON if applicable
        if str(filename).endswith(".json"):
            try:
                parsed = json.loads(data)
                if filename == "tenders_db.json":
                    count = len(parsed.get("tenders", {}))
                    print(f"✅ Loaded {filename} from Drive ({len(data)//1024} KB, {count} tenders)")
            except json.JSONDecodeError:
                print(f"❌ Drive file {filename} is not valid JSON — skipping")
                return False

        lp = Path(local_path) if not hasattr(local_path, "parent") else local_path
        lp.parent.mkdir(exist_ok=True, parents=True)
        lp.write_bytes(data)
        return True

    except RuntimeError as e:
        print(f"❌ Drive load skipped: {e}")
        return False
    except Exception as e:
        print(f"❌ Drive load failed for {filename}: {e}")
        return False


def is_available() -> bool:
    return _drive_service is not None


# Aliases for backward compatibility
drive_available = is_available

def upload_tender_file(local_path, filename=None):
    return save_to_drive(local_path, filename)
