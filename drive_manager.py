"""
drive_manager.py — Google Drive file system for NIT Bid/No-Bid
Owns the entire NIT-BidNoBid/ folder structure in Google Drive.
"""

import json, os, io, re
from pathlib import Path
from typing import Optional

# Global Flow object for OAuth
flow = None

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
        global flow

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
