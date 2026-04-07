"""
drive_manager.py — Google Drive file system for NIT Bid/No-Bid
Owns the entire NIT-BidNoBid/ folder structure in Google Drive.
"""

import json, os, io, re
flow = None   # global Flow object for OAuth
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

# Global Flow object for OAuth
flow = None

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
    global flow   # make sure we update the module-level flow


        raw_oauth = os.environ.get("OAUTH_CREDENTIALS", "").strip()
        if raw_oauth:
            try:
                import tempfile
                from google_auth_oauthlib.flow import Flow

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
                print("///////////////////////////////////////////////////////////")
                print(f"👉 Please visit this URL to authorize:\n{auth_url}")
                print("///////////////////////////////////////////////////////////")

                return True
            except Exception as e:
                print(f"❌ OAuth init failed: {e}")
                return False

        # Fallback to Service Account
        raw = os.environ.get("GDRIVE_CREDENTIALS", "").strip()
        # ... keep your existing service account fallback logic here ...

    # ── FOLDER MANAGEMENT, UPLOAD/DOWNLOAD, PUBLIC METHODS ───────
    # (keep all your existing methods unchanged)
    # ...
    # ── HELPERS & SINGLETON ──────────────────────────────────────

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

# ── Module-level singleton ──────────────────────────────────────
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
