"""
Google Drive Sync v2 - Fixed version
- Clears file ID cache on each startup (fixes stale cached IDs)
- Properly handles empty/corrupt Drive files
- Uses GDRIVE_FOLDER_ID from environment
"""

import json, os, io
from pathlib import Path

_drive_service = None
_file_id_cache = {}
_credentials = None


def init_drive():
    global _drive_service, _credentials, _file_id_cache
    _file_id_cache = {}  # Always clear cache on init
    try:
        creds_json = os.environ.get("GDRIVE_CREDENTIALS")
        if not creds_json:
            print("⚠️ GDRIVE_CREDENTIALS env var not set")
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
        print("✅ Google Drive connected successfully")
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
        _file_id_cache = {}  # Clear cache on reconnect
        print("🔄 Google Drive reconnected")
        return True
    except Exception as e:
        print(f"❌ Drive reconnect failed: {e}")
        return False


def _get_or_create_file(filename, folder_id=None):
    global _drive_service, _file_id_cache
    # Always search fresh — don't use stale cache from previous deploys
    try:
        q = f"name='{filename}' and trashed=false"
        if folder_id:
            q += f" and '{folder_id}' in parents"
        results = _drive_service.files().list(
            q=q, fields="files(id,name,size)", pageSize=10
        ).execute()
        files = results.get("files", [])
        if files:
            # Pick the largest file (most likely the real data)
            files_sorted = sorted(files, key=lambda f: int(f.get("size", 0)), reverse=True)
            fid = files_sorted[0]["id"]
            size_kb = int(files_sorted[0].get("size", 0)) // 1024
            _file_id_cache[filename] = fid
            print(f"📄 Found Drive file: {filename} ({fid}, {size_kb} KB)")
            return fid
        # Create new file
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
            _file_id_cache.clear()
            if not _reconnect():
                return False
        except Exception as e:
            if attempt == 0 and ("pipe" in str(e).lower() or "connection" in str(e).lower()):
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
            print(f"⚠️ No Drive file found for {filename}")
            return False
        request = _drive_service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()
        if len(data) < 10:
            print(f"⚠️ Drive file is empty ({len(data)} bytes)")
            return False
        try:
            parsed = json.loads(data)
            tender_count = len(parsed.get("tenders", {}))
            if tender_count == 0:
                # Still write the file locally — at least creates the correct JSON structure
                print(f"⚠️ Drive file has 0 tenders — writing empty structure locally")
                local_path.parent.mkdir(exist_ok=True, parents=True)
                local_path.write_bytes(data)
                return True  # Don't skip — empty is still valid
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


# Backward compatibility stub — some versions of main.py import this
def upload_tender_file(local_path, filename=None):
    """Alias for save_to_drive for backward compatibility"""
    return save_to_drive(local_path, filename or local_path.name)

# Also export drive_available as alias for is_available
drive_available = is_available
