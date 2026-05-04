"""
Portal Watcher v1.0 — Nascent Info Technologies Bid/No-Bid System

Monitors nprocure / T247 portals for:
1. Corrigendum / addendum published on active tenders
2. Bid opening date alerts (day-of check)
3. Pre-bid response (clarification) documents published
4. Tender status changes (extended deadline, cancelled)

Runs as a background task — checks every 6 hours.
Stores last-known portal state per tender in the DB.
"""

import re
import json
import time
import hashlib
import logging
import threading
import requests
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent
OUTPUT_DIR  = BASE_DIR / "data"
DB_FILE     = OUTPUT_DIR / "tenders_db.json"

# ─────────────────────────────────────────────────────────────────
# DB helpers (standalone — no circular imports with main.py)
# ─────────────────────────────────────────────────────────────────

def _load_db() -> dict:
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tenders": {}}


def _save_db(db: dict):
    DB_FILE.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# PORTAL FETCHER — lightweight, no login needed for public pages
# ─────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Portals that have public tender summary pages
PORTAL_PATTERNS = {
    "nprocure": {
        "search": "https://tender.nprocure.com/TenderSearch.aspx?SearchKey={tender_no}",
        "smc":    "https://smctender.nprocure.com/TenderSearch.aspx?SearchKey={tender_no}",
        "amc":    "https://amctender.nprocure.com/TenderSearch.aspx?SearchKey={tender_no}",
    },
    "t247": {
        "detail": "https://www.tender247.com/keyword/{t247_id}+tender#",
    }
}

# Keywords that indicate a corrigendum was published
CORR_KEYWORDS = [
    "corrigendum", "addendum", "amendment", "rectification",
    "revised", "extension", "postpone", "cancell", "withdrawn",
    "revised date", "extended", "date extended"
]


def _fetch_page(url: str, timeout: int = 20) -> Optional[str]:
    """Fetch a portal page. Returns HTML or None on failure."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logger.debug(f"Fetch failed {url}: {e}")
    return None


def _page_hash(html: str) -> str:
    """MD5 hash of meaningful page content (strips script/style)."""
    # Remove scripts, styles, dynamic timestamps
    clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'\d{2}:\d{2}:\d{2}', '', clean)  # Remove time values
    clean = re.sub(r'\s+', ' ', clean).strip()
    return hashlib.md5(clean[:50000].encode('utf-8', errors='ignore')).hexdigest()


def _detect_changes(old_html: str, new_html: str) -> dict:
    """Compare old and new page HTML, detect what changed."""
    changes = {
        "corrigendum": False,
        "date_extended": False,
        "cancelled": False,
        "new_documents": False,
        "details": []
    }
    new_lower = new_html.lower()
    old_lower = old_html.lower()

    for kw in CORR_KEYWORDS:
        if kw in new_lower and kw not in old_lower:
            changes["corrigendum"] = True
            changes["details"].append(f"New keyword found: '{kw}'")

    if "cancel" in new_lower and "cancel" not in old_lower:
        changes["cancelled"] = True
        changes["details"].append("Tender may have been cancelled")

    if "extended" in new_lower and "extended" not in old_lower:
        changes["date_extended"] = True
        changes["details"].append("Deadline may have been extended")

    # Check for new dates in new page not in old
    new_dates = set(re.findall(r'\d{2}[-/]\d{2}[-/]\d{4}', new_html))
    old_dates = set(re.findall(r'\d{2}[-/]\d{2}[-/]\d{4}', old_html))
    new_date_additions = new_dates - old_dates
    if new_date_additions:
        changes["details"].append(f"New dates appeared: {', '.join(new_date_additions)}")

    return changes


# ─────────────────────────────────────────────────────────────────
# WATCH ENGINE
# ─────────────────────────────────────────────────────────────────

class PortalWatcher:

    def __init__(self):
        self._stop_flag = threading.Event()
        self._thread   = None
        self._alerts   = []  # in-memory alert queue

    def start(self):
        """Start background watcher thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Portal watcher started")

    def stop(self):
        self._stop_flag.set()

    def _run_loop(self):
        """Check portals every 6 hours."""
        while not self._stop_flag.is_set():
            try:
                self.run_check_once()
            except Exception as e:
                logger.error(f"Watcher loop error: {e}")
            # Sleep 6 hours (check every 6 minutes for dev/testing)
            for _ in range(360):  # 360 × 60s = 6h
                if self._stop_flag.is_set():
                    return
                time.sleep(60)

    def run_check_once(self) -> list:
        """
        Check all active tenders for portal changes.
        Returns list of alerts generated.
        """
        db      = _load_db()
        tenders = db.get("tenders", {})
        alerts  = []
        changed = False

        for tid, tender in tenders.items():
            # Only watch active BID/CONDITIONAL tenders
            if tender.get("verdict") not in ["BID", "CONDITIONAL", "REVIEW"]:
                continue
            if tender.get("status") in ["Submitted", "Won", "Lost", "No-Bid", "Not Interested"]:
                continue
            # Skip expired tenders
            deadline_str = tender.get("deadline", "")
            if deadline_str:
                try:
                    for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]:
                        try:
                            dl = datetime.strptime(deadline_str.split()[0], fmt).date()
                            if dl < date.today() - timedelta(days=3):
                                continue
                            break
                        except Exception:
                            continue
                except Exception:
                    pass

            new_alerts = self._check_tender(tid, tender)
            if new_alerts:
                alerts.extend(new_alerts)
                # Store alerts in tender record
                existing = tender.get("portal_alerts", [])
                existing.extend(new_alerts)
                tender["portal_alerts"] = existing[-20:]  # keep last 20
                tender["last_portal_check"] = datetime.now().isoformat()
                db["tenders"][tid] = tender
                changed = True

            # Always update last_check timestamp
            if not tender.get("last_portal_check") or changed:
                tender["last_portal_check"] = datetime.now().isoformat()
                db["tenders"][tid] = tender
                changed = True

        if changed:
            _save_db(db)

        self._alerts.extend(alerts)
        return alerts

    def _check_tender(self, tid: str, tender: dict) -> list:
        """Check a single tender's portal page for changes."""
        alerts = []
        tender_no   = tender.get("tender_no", "")
        org         = tender.get("org_name", "").lower()
        portal_code = "smc" if "surat" in org else "amc" if "ahmedabad" in org else "nprocure"

        # Build URL to check
        url = None
        if tender_no:
            base = PORTAL_PATTERNS["nprocure"].get(portal_code,
                   PORTAL_PATTERNS["nprocure"]["search"])
            url = base.format(tender_no=requests.utils.quote(tender_no))

        if not url:
            return []

        html = _fetch_page(url)
        if not html:
            return []

        page_hash = _page_hash(html)
        old_hash  = tender.get("portal_page_hash", "")

        # First time checking — just store hash
        if not old_hash:
            tender["portal_page_hash"] = page_hash
            tender["portal_page_html"] = html[:5000]  # store first 5KB
            return []

        # Page hasn't changed
        if page_hash == old_hash:
            return []

        # Page changed — analyse what changed
        old_html = tender.get("portal_page_html", "")
        changes  = _detect_changes(old_html, html)

        # Update stored page
        tender["portal_page_hash"] = page_hash
        tender["portal_page_html"] = html[:5000]

        # Generate alerts
        if changes["corrigendum"]:
            alert = {
                "type":    "corrigendum",
                "t247_id": tid,
                "brief":   tender.get("brief", "")[:60],
                "message": f"Corrigendum/Addendum detected on portal for {tender.get('brief','')[:50]}",
                "details": changes["details"],
                "time":    datetime.now().isoformat(),
                "read":    False,
            }
            alerts.append(alert)
            logger.info(f"CORRIGENDUM ALERT: {tid}")

        if changes["cancelled"]:
            alert = {
                "type":    "cancellation",
                "t247_id": tid,
                "brief":   tender.get("brief", "")[:60],
                "message": f"Tender may have been CANCELLED: {tender.get('brief','')[:50]}",
                "details": changes["details"],
                "time":    datetime.now().isoformat(),
                "read":    False,
            }
            alerts.append(alert)

        if changes["date_extended"]:
            alert = {
                "type":    "extension",
                "t247_id": tid,
                "brief":   tender.get("brief", "")[:60],
                "message": f"Deadline may have been EXTENDED: {tender.get('brief','')[:50]}",
                "details": changes["details"],
                "time":    datetime.now().isoformat(),
                "read":    False,
            }
            alerts.append(alert)

        return alerts

    def get_pending_alerts(self) -> list:
        """Return all unread alerts from in-memory queue + DB."""
        db      = _load_db()
        tenders = db.get("tenders", {})
        all_alerts = []
        for tid, tender in tenders.items():
            for alert in tender.get("portal_alerts", []):
                if not alert.get("read"):
                    all_alerts.append(alert)
        # Sort by time descending
        all_alerts.sort(key=lambda a: a.get("time", ""), reverse=True)
        return all_alerts[:50]

    def mark_alert_read(self, t247_id: str, alert_type: str):
        """Mark a specific alert as read."""
        db = _load_db()
        tender = db["tenders"].get(t247_id, {})
        for alert in tender.get("portal_alerts", []):
            if alert.get("type") == alert_type and not alert.get("read"):
                alert["read"] = True
        db["tenders"][t247_id] = tender
        _save_db(db)

    def check_bid_opening_today(self) -> list:
        """Return tenders whose bid opening date is today."""
        db       = _load_db()
        today    = date.today().strftime("%d-%m-%Y")
        opening  = []
        for tid, tender in db["tenders"].items():
            bod = tender.get("bid_opening_date", "")
            if not bod:
                continue
            # Normalise date
            for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]:
                try:
                    d = datetime.strptime(bod.split()[0], fmt).date()
                    if d == date.today():
                        opening.append({
                            "t247_id": tid,
                            "brief":   tender.get("brief", "")[:60],
                            "org":     tender.get("org_name", ""),
                            "bid_opening_date": bod,
                            "status":  tender.get("status", ""),
                        })
                    break
                except Exception:
                    continue
        return opening


# ─────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────

_watcher = PortalWatcher()


def get_watcher() -> PortalWatcher:
    return _watcher


def start_watcher():
    _watcher.start()


def check_now() -> list:
    """Manual trigger — run one check cycle immediately."""
    return _watcher.run_check_once()


def get_all_alerts() -> list:
    return _watcher.get_pending_alerts()


def get_bid_opening_today() -> list:
    return _watcher.check_bid_opening_today()
