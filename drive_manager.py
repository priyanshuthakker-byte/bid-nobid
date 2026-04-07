"""
drive_manager.py — Google Drive file system for NIT Bid/No-Bid
Owns the entire NIT-BidNoBid/ folder structure in Google Drive.

Folder layout (all under GDRIVE_FOLDER_ID root):
  NIT-BidNoBid/                   ← GDRIVE_FOLDER_ID points here
    config/
      nascent_profile.json
      bid_rules.json               ← split from profile for clarity
      tenders_db.json
    vault/
      pan_card.pdf
      cmmi_cert.pdf
      … (22 Nascent company docs)
    tenders/
      283807-SMC-GIS/              ← one folder per tender
        tender_docs.zip
        BidNoBid_analysis.docx
        PreBid_queries.docx
        checklist.json
        submission/
          01_cover_letter.docx
          02_tech_proposal.docx
          …
          FINAL_SUBMISSION.zip
    exports/
      tenders_2026-04.xlsx
      emd_tracker.xlsx

Usage:
    from drive_manager import DriveManager
    dm = DriveManager()
    dm.save_config("tenders_db.json", path)
    dm.load_config("tenders_db.json", dest_path)
    dm.save_vault("pan_card.pdf", path)
    dm.load_vault("pan_card.pdf", dest_path)
    dm.save_tender_file("283807", "BidNoBid_analysis.docx", path)
    dm.save_tender_submission("283807", "01_cover_letter.docx", path)
    dm.get_vault_file("pan_card")   → local Path or None
"""

import json, os, io, re
from pathlib import Path
from typing import Optional

# ── Folder name constants ──────────────────────────────────────
FOLDER_CONFIG     = "config"
FOLDER_VAULT      = "vault"
FOLDER_TENDERS    = "tenders"
FOLDER_EXPORTS    = "exports"
FOLDER_SUBMISSION = "submission"

# Mimetypes
_MIME = {
    "json":  "application/json",
    "pdf":   "application/pdf",
    "docx":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx":  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png":   "image/png",
    "jpg":   "image/jpeg",
    "jpeg":  "image/jpeg",
    "zip":   "application/zip",
    "txt":   "text/plain",
}
_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveManager:
    """
    Single object that owns the entire Drive folder structure.
    All public methods accept local Path objects and handle Drive internals.
    """

    def __init__(self):
        self._svc        = None
        self._creds_data = None
        self._root_id    = None          # GDRIVE_FOLDER_ID
        self._folder_cache: dict = {}    # path_key → folder_id
        self._file_cache:   dict = {}    # (folder_id, filename) → file_id

    # ── INIT ─────────────────────────────────────────────────────
def init(self) -> bool:
    """Connect to Drive and ensure folder structure exists. Returns True on success."""

    raw_oauth = os.environ.get("OAUTH_CREDENTIALS", "").strip()
    if raw_oauth:
        try:
            import json, tempfile
            from google_auth_oauthlib.flow import Flow
            from googleapiclient.discovery import build

            # Save credentials.json temporarily
            creds_file = tempfile.NamedTemporaryFile(delete=False)
            creds_file.write(raw_oauth.encode("utf-8"))
            creds_file.close()

            SCOPES = ["https://www.googleapis.com/auth/drive"]
            flow = Flow.from_client_secrets_file(
                creds_file.name,
                scopes=SCOPES,
                redirect_uri=os.environ.get("OAUTH_REDIRECT_URI", "https://bid-nobid.onrender.com/oauth-callback")
            )

            # Generate authorization URL
            auth_url, _ = flow.authorization_url(prompt='consent')
            print(f"👉 Please visit this URL to authorize: {auth_url}")

            # NOTE: You need to implement a /oauth-callback route in your FastAPI app
            # Example:
            # @app.get("/oauth-callback")
            # def oauth_callback(request: Request):
            #     code = request.query_params.get("code")
            #     flow.fetch_token(code=code)
            #     creds = flow.credentials
            #     self._svc = build("drive", "v3", credentials=creds)
            #     print("✅ Drive connected with OAuth")

            return True
        except Exception as e:
            print(f"❌ OAuth init failed: {e}")
            return False

    # Fallback to Service Account
    raw = os.environ.get("GDRIVE_CREDENTIALS", "").strip()
    ...



    # ── FOLDER MANAGEMENT ────────────────────────────────────────
    def _ensure_folder(self, name: str, parent_id: str = None) -> Optional[str]:
        """
        Get or create a folder by name under parent_id (or root).
        Returns folder_id.
        """
        parent_id = parent_id or self._root_id
        cache_key = f"{parent_id}/{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]
        try:
            q = (f"name='{name}' and mimeType='{_FOLDER_MIME}' "
                 f"and '{parent_id}' in parents and trashed=false")
            res = self._svc.files().list(q=q, fields="files(id)", pageSize=5).execute()
            files = res.get("files", [])
            if files:
                fid = files[0]["id"]
                self._folder_cache[cache_key] = fid
                return fid
            # Create it
            meta = {"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]}
            f = self._svc.files().create(body=meta, fields="id").execute()
            fid = f["id"]
            self._folder_cache[cache_key] = fid
            print(f"📁 Created Drive folder: {name}")
            return fid
        except Exception as e:
            print(f"❌ Folder ensure failed for '{name}': {e}")
            return None

    def _tender_folder_id(self, t247_id: str, label: str = "") -> Optional[str]:
        """
        Get or create tenders/<t247_id>-<label>/ folder.
        label is a short slug like 'SMC-GIS' — optional but makes Drive readable.
        """
        tenders_id = self._ensure_folder(FOLDER_TENDERS)
        if not tenders_id:
            return None
        folder_name = f"{t247_id}-{_slug(label)}" if label else str(t247_id)
        return self._ensure_folder(folder_name, parent_id=tenders_id)

    def _submission_folder_id(self, t247_id: str, label: str = "") -> Optional[str]:
        """Get or create tenders/<tender>/submission/ folder."""
        tender_id = self._tender_folder_id(t247_id, label)
        if not tender_id:
            return None
        return self._ensure_folder(FOLDER_SUBMISSION, parent_id=tender_id)

    # ── CORE UPLOAD / DOWNLOAD ────────────────────────────────────
    def _upload(self, local_path: Path, filename: str, folder_id: str) -> bool:
        """Upload or update a file in the given Drive folder."""
        if not self._svc or not folder_id:
            return False
        try:
            from googleapiclient.http import MediaIoBaseUpload
            content  = local_path.read_bytes()
            ext      = local_path.suffix.lstrip(".").lower()
            mimetype = _MIME.get(ext, "application/octet-stream")
            media    = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=False)
            cache_key = (folder_id, filename)
            file_id   = self._file_cache.get(cache_key) or self._find_file(filename, folder_id)
            if file_id:
                self._svc.files().update(fileId=file_id, media_body=media).execute()
            else:
                meta   = {"name": filename, "parents": [folder_id]}
                result = self._svc.files().create(body=meta, media_body=media, fields="id").execute()
                file_id = result["id"]
            self._file_cache[cache_key] = file_id
            print(f"✅ Drive upload: {filename} ({len(content)//1024} KB)")
            return True
        except Exception as e:
            _log_drive_error(e, f"upload {filename}")
            return False

    def _download(self, filename: str, folder_id: str, dest: Path) -> bool:
        """Download a file from Drive folder to dest path."""
        if not self._svc or not folder_id:
            return False
        try:
            from googleapiclient.http import MediaIoBaseDownload
            file_id = self._file_cache.get((folder_id, filename)) or self._find_file(filename, folder_id)
            if not file_id:
                return False
            req = self._svc.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            dl  = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
            data = buf.getvalue()
            if len(data) < 4:
                print(f"⚠️  Drive file {filename} is empty — skipping")
                return False
            dest.parent.mkdir(exist_ok=True, parents=True)
            dest.write_bytes(data)
            print(f"✅ Drive download: {filename} ({len(data)//1024} KB)")
            return True
        except Exception as e:
            _log_drive_error(e, f"download {filename}")
            return False

    def _find_file(self, filename: str, folder_id: str) -> Optional[str]:
        """Search for a file by name in a folder. Returns file_id or None."""
        try:
            q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
            res = self._svc.files().list(q=q, fields="files(id,size)", pageSize=5).execute()
            files = res.get("files", [])
            if not files:
                return None
            # Prefer non-empty files
            files_sorted = sorted(files, key=lambda f: int(f.get("size", 0)), reverse=True)
            fid = files_sorted[0]["id"]
            self._file_cache[(folder_id, filename)] = fid
            return fid
        except Exception:
            return None

    # ── PUBLIC: CONFIG FILES ──────────────────────────────────────
    def save_config(self, filename: str, local_path: Path) -> bool:
        """Save a config file (nascent_profile.json / tenders_db.json) to config/."""
        folder_id = self._ensure_folder(FOLDER_CONFIG)
        return self._upload(local_path, filename, folder_id)

    def load_config(self, filename: str, dest: Path) -> bool:
        """Load a config file from config/ to dest path."""
        folder_id = self._ensure_folder(FOLDER_CONFIG)
        return self._download(filename, folder_id, dest)

    # ── PUBLIC: VAULT ─────────────────────────────────────────────
    def save_vault(self, filename: str, local_path: Path) -> bool:
        """Save a vault document to vault/."""
        folder_id = self._ensure_folder(FOLDER_VAULT)
        return self._upload(local_path, filename, folder_id)

    def load_vault(self, filename: str, dest: Path) -> bool:
        """Download a vault document from vault/ to dest."""
        folder_id = self._ensure_folder(FOLDER_VAULT)
        return self._download(filename, folder_id, dest)

    def get_vault_file(self, doc_id: str, local_vault_dir: Path) -> Optional[Path]:
        """
        Find vault file by doc_id (e.g. 'pan_card').
        First checks local cache, then downloads from Drive if missing.
        Returns local Path or None.
        """
        # Check local first
        for ext in [".pdf", ".docx", ".png", ".jpg", ".jpeg"]:
            candidate = local_vault_dir / f"{doc_id}{ext}"
            if candidate.exists() and candidate.stat().st_size > 100:
                return candidate
        # Try Drive
        folder_id = self._ensure_folder(FOLDER_VAULT)
        if not folder_id:
            return None
        for ext in [".pdf", ".docx", ".png", ".jpg", ".jpeg"]:
            dest = local_vault_dir / f"{doc_id}{ext}"
            if self._download(f"{doc_id}{ext}", folder_id, dest):
                return dest
        return None

    def list_vault(self) -> list:
        """List all files in vault/ on Drive. Returns [{name, size_kb, file_id}]."""
        folder_id = self._ensure_folder(FOLDER_VAULT)
        if not folder_id:
            return []
        try:
            q = f"'{folder_id}' in parents and trashed=false"
            res = self._svc.files().list(q=q, fields="files(id,name,size)", pageSize=100).execute()
            return [
                {"name": f["name"], "size_kb": int(f.get("size", 0)) // 1024, "file_id": f["id"]}
                for f in res.get("files", [])
            ]
        except Exception:
            return []

    # ── PUBLIC: TENDER FILES ──────────────────────────────────────
    def save_tender_file(self, t247_id: str, filename: str,
                         local_path: Path, label: str = "") -> bool:
        """Save a file to tenders/<tender>/ (analysis report, pre-bid, zip, etc.)."""
        folder_id = self._tender_folder_id(t247_id, label)
        return self._upload(local_path, filename, folder_id)

    def save_tender_submission(self, t247_id: str, filename: str,
                               local_path: Path, label: str = "") -> bool:
        """Save a final submission doc to tenders/<tender>/submission/."""
        folder_id = self._submission_folder_id(t247_id, label)
        return self._upload(local_path, filename, folder_id)

    def get_tender_folder_url(self, t247_id: str, label: str = "") -> Optional[str]:
        """Return a Drive URL for the tender's folder (for opening in browser)."""
        folder_id = self._tender_folder_id(t247_id, label)
        if folder_id:
            return f"https://drive.google.com/drive/folders/{folder_id}"
        return None

    # ── PUBLIC: EXPORTS ───────────────────────────────────────────
    def save_export(self, filename: str, local_path: Path) -> bool:
        """Save an export file (xlsx, csv) to exports/."""
        folder_id = self._ensure_folder(FOLDER_EXPORTS)
        return self._upload(local_path, filename, folder_id)

    # ── STARTUP RESTORE ──────────────────────────────────────────
    def restore_on_startup(self, db_path: Path, profile_path: Path,
                            vault_dir: Path, vault_docs_list: list = None):
        """
        Called at app startup. Restores:
          - tenders_db.json  from config/
          - nascent_profile.json from config/
          - vault files from vault/ (only if not already local)
        vault_docs_list: list of {id, name, ...} dicts from main.py VAULT_DOCS_LIST
        """
        # tenders_db.json
        try:
            ok = self.load_config("tenders_db.json", db_path)
            if ok:
                try:
                    data = json.loads(db_path.read_text(encoding="utf-8"))
                    count = len(data.get("tenders", {}))
                    print(f"✅ Restored tenders_db.json ({count} tenders)")
                except Exception:
                    pass
            else:
                print("⚠️  tenders_db.json not on Drive yet — starting fresh")
        except Exception as e:
            print(f"⚠️  DB restore error: {e}")

        # nascent_profile.json
        try:
            ok = self.load_config("nascent_profile.json", profile_path)
            if ok:
                print("✅ Restored nascent_profile.json from Drive")
            else:
                print("⚠️  nascent_profile.json not on Drive — using bundled version")
        except Exception as e:
            print(f"⚠️  Profile restore error: {e}")

        # Vault — restore only missing files, don't re-download what's already local
        vault_dir.mkdir(exist_ok=True, parents=True)
        restored = 0
        pass  # VAULT_DOCS_LIST passed as parameter
        try:
            folder_id = self._ensure_folder(FOLDER_VAULT)
            if folder_id and vault_docs_list:
                for doc in vault_docs_list:
                    doc_id = doc["id"]
                    already = any((vault_dir / f"{doc_id}{ext}").exists()
                                  for ext in [".pdf", ".docx", ".png", ".jpg", ".jpeg"])
                    if already:
                        continue
                    for ext in [".pdf", ".docx", ".png", ".jpg", ".jpeg"]:
                        dest = vault_dir / f"{doc_id}{ext}"
                        if self._download(f"{doc_id}{ext}", folder_id, dest):
                            restored += 1
                            break
            print(f"✅ Vault restore: {restored} files downloaded from Drive")
        except Exception as e:
            print(f"⚠️  Vault restore error: {e}")


# ── HELPERS ──────────────────────────────────────────────────────
def _slug(text: str) -> str:
    """Convert text to a safe folder name slug."""
    return re.sub(r"[^\w\-]", "-", str(text).strip())[:30].strip("-")


def _log_drive_error(e: Exception, context: str = ""):
    err = str(e)
    if "storageQuotaExceeded" in err:
        print(
            f"❌ Drive {context} failed: storageQuotaExceeded\n"
            "   CAUSE: Service account Drive quota exceeded (15 GB free limit) OR\n"
            "          service account does not have Editor permission on the folder.\n"
            "   FIX: Go to Google Drive → right-click NIT-BidNoBid folder → Share\n"
            "        → add service account email as Editor."
        )
    elif "insufficientPermissions" in err or "forbidden" in err.lower():
        print(
            f"❌ Drive {context} failed: insufficientPermissions\n"
            "   FIX: Share the NIT-BidNoBid folder with the service account email (Editor role)."
        )
    else:
        print(f"❌ Drive {context} failed: {e}")


# ── Module-level singleton ────────────────────────────────────────
_dm: Optional[DriveManager] = None

def get_drive() -> DriveManager:
    global _dm
    if _dm is None:
        _dm = DriveManager()
    return _dm

def init_drive() -> bool:
    return get_drive().init()

def is_available() -> bool:
    return get_drive().is_available()

drive_available = is_available
