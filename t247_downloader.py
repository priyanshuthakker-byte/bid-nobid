import zipfile
"""
T247 Downloader v2.0 — Nascent Info Technologies Bid/No-Bid System

WHAT T247 IS:
- tender247.com is a PAID SUBSCRIPTION scraper portal (by i-Sourcing Technologies, Ahmedabad)
- It aggregates tenders from government portals (nprocure, GeM, eProcure etc.)
- Nascent has a paid subscription — login with email + password on tender247.com
- T247 stores the original documents (ZIP/PDF) from the govt portal
- With subscription, you can download the full tender document package from T247

WHAT THIS DOES:
1. Login to www.tender247.com with Nascent subscription credentials
2. Navigate to tender by T247 ID (e.g. 283807)
3. Read all tender details from T247 page
4. Download the tender document ZIP/PDF from T247
5. Return extracted files for AI analysis

DOES NOT touch nprocure / GeM / any government portal directly.
"""

import re
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger(__name__)

T247_BASE  = "https://www.tender247.com"
T247_LOGIN = "https://www.tender247.com/login"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": T247_BASE,
}


class T247Session:
    def __init__(self, username: str, password: str):
        self.username  = username
        self.password  = password
        self.session   = requests.Session()
        self.session.headers.update(HEADERS)
        self.logged_in = False
        self._token    = ""

    def login(self) -> dict:
        try:
            r0 = self.session.get(T247_LOGIN, timeout=20)
            if r0.status_code not in [200, 302]:
                return {"success": False,
                        "message": f"T247 login page unreachable: HTTP {r0.status_code}"}

            # Try JSON API login (Next.js apps)
            for api_url in [
                f"{T247_BASE}/api/auth/login",
                f"{T247_BASE}/api/login",
                f"{T247_BASE}/api/user/login",
                f"{T247_BASE}/api/v1/auth/login",
            ]:
                try:
                    r = self.session.post(
                        api_url,
                        json={"email": self.username, "password": self.password,
                              "username": self.username},
                        headers={"Content-Type": "application/json",
                                 "Accept": "application/json"},
                        timeout=15
                    )
                    if r.status_code == 200:
                        try:
                            data = r.json()
                            token = (data.get("token") or data.get("access_token") or
                                     data.get("jwt") or
                                     (data.get("data") or {}).get("token", ""))
                            if token:
                                self._token = token
                                self.session.headers["Authorization"] = f"Bearer {token}"
                                self.logged_in = True
                                return {"success": True,
                                        "message": f"Logged in to tender247.com as {self.username}"}
                            if data.get("success") or data.get("status") == "ok":
                                self.logged_in = True
                                return {"success": True,
                                        "message": f"Logged in to tender247.com as {self.username}"}
                        except Exception:
                            pass
                except Exception:
                    continue

            # Fallback: HTML form POST
            html = r0.text
            fields = {
                "email": self.username, "password": self.password,
                "username": self.username, "btnLogin": "Login", "Submit": "Login",
            }
            for m in re.finditer(
                r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
                html, re.IGNORECASE
            ):
                if m.group(1) not in fields:
                    fields[m.group(1)] = m.group(2)

            r2 = self.session.post(
                T247_LOGIN, data=fields,
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Referer": T247_LOGIN},
                timeout=20, allow_redirects=True
            )
            if self._is_logged_in(r2.text, r2.url):
                self.logged_in = True
                return {"success": True,
                        "message": f"Logged in to tender247.com as {self.username}"}

            error = self._extract_error(r2.text)
            return {"success": False,
                    "message": f"T247 login failed — {error or 'Invalid email/password. Check Settings → T247 Portal.'}"}

        except requests.exceptions.Timeout:
            return {"success": False, "message": "tender247.com not responding."}
        except requests.exceptions.ConnectionError:
            return {"success": False, "message": "Cannot reach tender247.com."}
        except Exception as e:
            return {"success": False, "message": f"Login error: {str(e)[:200]}"}

    def _is_logged_in(self, html: str, url: str) -> bool:
        html_l = html.lower()
        for sig in ["invalid password", "incorrect password", "user not found",
                    "invalid credentials", "please login", "please sign in"]:
            if sig in html_l:
                return False
        for sig in ["logout", "my account", "dashboard", "my tenders",
                    "subscription", "welcome", "profile", "favourites"]:
            if sig in html_l or sig in url.lower():
                return True
        if "login" not in url.lower() and len(html) > 5000:
            return True
        return False

    def _extract_error(self, html: str) -> str:
        for p in [
            r'<[^>]*class=["\'][^"\']*(?:error|alert|danger)[^"\']*["\'][^>]*>([^<]{5,200})<',
            r'<span[^>]*>([^<]*(?:invalid|incorrect|failed|wrong)[^<]*)</span>',
        ]:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    def get(self, url, **kw):
        return self.session.get(url, **kw)

    def post(self, url, **kw):
        return self.session.post(url, **kw)


class T247TenderFetcher:
    def __init__(self, session: T247Session):
        self.sess = session

    def fetch_tender(self, t247_id: str, download_dir: Path) -> dict:
        result = {"success": False, "tender_details": {}, "downloaded_files": [], "errors": []}
        download_dir.mkdir(parents=True, exist_ok=True)

        url = self._find_tender_url(t247_id)
        if not url:
            result["errors"].append(
                f"Tender {t247_id} not found on tender247.com. "
                "It may be expired or not in your subscription plan."
            )
            return result

        try:
            r = self.sess.get(url, timeout=30)
        except Exception as e:
            result["errors"].append(f"Could not fetch tender page: {e}")
            return result

        if r.status_code == 403:
            result["errors"].append(
                "Access denied on T247 (403). "
                "Your subscription plan may not include document downloads. "
                "Please download the ZIP manually from tender247.com and upload here."
            )
            return result
        if r.status_code != 200:
            result["errors"].append(f"T247 tender page returned HTTP {r.status_code}")
            return result

        result["tender_details"] = self._parse_page(r.text, t247_id, url)
        doc_links = self._find_doc_links(r.text, t247_id)

        if not doc_links:
            result["errors"].append(
                "No document download links found on this T247 page. "
                "Possible reasons: (1) Document not yet published, "
                "(2) Your T247 plan does not include document downloads, "
                "(3) T247 session expired. "
                "Please download the ZIP manually from tender247.com and upload here."
            )
            return result

        downloaded = self._download_files(doc_links, download_dir, t247_id)
        result["downloaded_files"] = downloaded

        if not downloaded:
            result["errors"].append(
                "Documents links found but downloads failed — "
                "your T247 plan may not include document downloads. "
                "Please download ZIP manually from tender247.com and upload here."
            )
            return result

        result["success"] = True
        return result

    def _find_tender_url(self, t247_id: str) -> Optional[str]:
        for url in [
            f"{T247_BASE}/tender/{t247_id}",
            f"{T247_BASE}/keyword/{t247_id}+tender#",
            f"{T247_BASE}/tender-detail/{t247_id}",
        ]:
            try:
                r = self.sess.get(url, timeout=15, allow_redirects=True)
                if r.status_code == 200 and len(r.text) > 1000:
                    if any(kw in r.text.lower() for kw in
                           ["tender", "emd", "bid", "deadline", "department"]):
                        return url
            except Exception:
                continue
        return None

    def _parse_page(self, html: str, t247_id: str, url: str) -> dict:
        details = {"t247_id": t247_id, "source_url": url,
                   "scraped_at": datetime.now().isoformat()}

        label_map = {
            "tender no": "tender_no", "ref no": "ref_no", "reference no": "ref_no",
            "organisation": "org_name", "organization": "org_name", "department": "org_name",
            "title": "brief", "work": "brief", "description": "brief",
            "estimated cost": "estimated_cost", "tender value": "estimated_cost",
            "emd": "emd", "earnest money": "emd", "tender fee": "tender_fee",
            "last date": "deadline", "bid submission": "deadline", "closing date": "deadline",
            "pre bid": "prebid_date", "pre-bid": "prebid_date",
            "opening date": "bid_opening_date", "bid opening": "bid_opening_date",
            "state": "location", "location": "location", "city": "location",
        }

        row_pat = re.compile(
            r'<(?:td|th|dt|label|div|span)[^>]*>\s*([^<]{2,60}?)\s*:?\s*</(?:td|th|dt|label|div|span)>'
            r'\s*<(?:td|dd|div|span)[^>]*>\s*([^<]{1,300}?)\s*</(?:td|dd|div|span)>',
            re.IGNORECASE | re.DOTALL
        )
        for m in row_pat.finditer(html):
            label = re.sub(r'\s+', ' ', m.group(1)).strip().lower()
            value = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            value = re.sub(r'\s+', ' ', value).strip()
            for key, field in label_map.items():
                if key in label and value and len(value) < 400:
                    if not details.get(field):
                        details[field] = value
                    break

        if not details.get("brief"):
            for tag in ["h1", "h2"]:
                m = re.search(rf'<{tag}[^>]*>([^<]{{10,200}})</{tag}>', html, re.IGNORECASE)
                if m:
                    text = m.group(1).strip()
                    if "tender247" not in text.lower():
                        details["brief"] = text
                        break
        return details

    def _find_doc_links(self, html: str, t247_id: str) -> List[str]:
        links = []
        patterns = [
            r'href=["\']([^"\']*\.(?:zip|pdf|docx|doc|xlsx|rar)[^"\']*)["\']',
            r'href=["\']([^"\']*(?:download|getFile|tender_doc)[^"\']*)["\']',
            r'(https?://(?:cdn|files?|docs?|storage)\.tender247\.com[^\s"\'<>]+)',
            r'data-url=["\']([^"\']+)["\']',
            r'data-download=["\']([^"\']+)["\']',
        ]
        for pat in patterns:
            for m in re.finditer(pat, html, re.IGNORECASE):
                href = m.group(1)
                if href.startswith("/"):
                    href = T247_BASE + href
                elif not href.startswith("http"):
                    href = T247_BASE + "/" + href
                if href not in links:
                    links.append(href)
        return links[:10]

    def _download_files(self, urls: List[str], download_dir: Path, t247_id: str) -> List[dict]:
        downloaded = []
        for url in urls:
            try:
                r = self.sess.get(url, timeout=120, stream=True,
                                  headers={"Referer": f"{T247_BASE}/tender/{t247_id}"})
                if r.status_code != 200:
                    continue
                filename = self._get_filename(r, url)
                filepath = download_dir / filename
                with open(filepath, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                size = filepath.stat().st_size
                if size < 500:
                    filepath.unlink()
                    continue
                downloaded.append({
                    "filename": filename, "path": str(filepath),
                    "size_kb": round(size / 1024, 1), "url": url,
                    "downloaded": datetime.now().isoformat(),
                })
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"Download failed {url}: {e}")
        return downloaded

    def _get_filename(self, response, url: str) -> str:
        cd = response.headers.get("Content-Disposition", "")
        m = re.search(r'filename[^;=\n]*=([\'"]?)([^\'";\n]+)\1', cd)
        if m:
            return re.sub(r'[^\w\-_\. ]', '_', m.group(2).strip())[:100]
        path = url.split("?")[0].rstrip("/").split("/")[-1]
        if "." in path and len(path) > 4:
            return re.sub(r'[^\w\-_\. ]', '_', path)[:100]
        ct = response.headers.get("Content-Type", "")
        ext = ".zip"
        if "pdf" in ct:
            ext = ".pdf"
        elif "word" in ct:
            ext = ".docx"
        return f"tender_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"


# ─────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────

def auto_download_tender(t247_id, tender_no, portal_code,
                         username, password, download_dir) -> dict:
    """Main entry point. Login to T247, download documents for t247_id."""
    sess = T247Session(username=username, password=password)
    lr   = sess.login()
    if not lr["success"]:
        return {"success": False, "tender_details": {}, "downloaded_files": [],
                "errors": [lr["message"]], "total_files": 0, "total_size_kb": 0,
                "portal_used": "tender247.com"}
    fetcher = T247TenderFetcher(sess)
    result  = fetcher.fetch_tender(t247_id=t247_id, download_dir=Path(download_dir))
    result["portal_used"]   = "tender247.com"
    result["total_files"]   = len(result.get("downloaded_files", []))
    result["total_size_kb"] = sum(f.get("size_kb", 0) for f in result.get("downloaded_files", []))
    return result


def test_credentials(username: str, password: str, portal_url: str = None) -> dict:
    """Test T247 subscription login."""
    return T247Session(username=username, password=password).login()


def get_supported_portals() -> dict:
    return {"tender247": T247_BASE,
            "info": "Subscription scraper aggregating Indian govt tenders"}


def resolve_excel_link(link: str) -> dict:
    """Parse T247 link or bare ID from Excel hyperlink."""
    info = {"portal": "unknown", "t247_id": "", "tender_no": "",
            "portal_code": "", "direct_url": str(link).strip()}
    link = str(link).strip()
    if re.match(r'^\d{4,10}$', link):
        info.update({"portal": "t247", "t247_id": link,
                     "direct_url": f"{T247_BASE}/tender/{link}"})
    elif "tender247.com" in link.lower():
        info["portal"] = "t247"
        m = re.search(r'/tender/(\d+)', link)
        if m:
            info["t247_id"]    = m.group(1)
            info["direct_url"] = f"{T247_BASE}/tender/{m.group(1)}"
    elif "nprocure.com" in link.lower():
        info["portal"] = "nprocure_direct"
    elif "gem.gov.in" in link.lower():
        info["portal"] = "gem"
    return info
