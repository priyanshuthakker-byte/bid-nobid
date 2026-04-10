"""
Tender247 downloader helper.

Best-effort automation that uses Playwright when available.
Designed to be optional: if Playwright or credentials are missing,
API routes return a clean "unavailable" response.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent / "config.json"


def is_playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:
        return False


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _credentials() -> tuple[str, str]:
    cfg = _load_config()
    user = (os.environ.get("T247_USERNAME") or cfg.get("t247_username") or "").strip()
    pwd = (os.environ.get("T247_PASSWORD") or cfg.get("t247_password") or "").strip()
    return user, pwd


def _safe_zip_name(t247_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(t247_id))[:40] or "tender"
    return f"T247_{safe}.zip"


def download_sync(t247_id: str, output_dir: Optional[Path] = None, timeout_ms: int = 120000) -> str:
    """
    Attempt to download tender package ZIP from Tender247.

    Returns saved zip filename (basename) on success.
    Raises RuntimeError on known failures.
    """
    if not is_playwright_available():
        raise RuntimeError("Playwright is not installed")

    username, password = _credentials()
    if not username or not password:
        raise RuntimeError("Tender247 credentials are missing in Settings or environment")

    out_dir = Path(output_dir) if output_dir else Path("/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = _safe_zip_name(t247_id)
    out_path = out_dir / out_name

    from playwright.sync_api import TimeoutError as PwTimeoutError
    from playwright.sync_api import sync_playwright

    detail_url = f"https://www.tender247.com/tender/detail/{t247_id}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Best-effort login handling (selectors vary by portal revisions).
            user_sel = "input[name='username'], input[type='email'], input#username"
            pass_sel = "input[name='password'], input[type='password'], input#password"
            if page.locator(user_sel).count() and page.locator(pass_sel).count():
                page.fill(user_sel, username)
                page.fill(pass_sel, password)
                login_btn = page.locator("button[type='submit'], button:has-text('Login'), input[type='submit']")
                if login_btn.count():
                    login_btn.first.click()
                page.wait_for_timeout(2000)
                page.goto(detail_url, wait_until="domcontentloaded", timeout=timeout_ms)

            selectors = [
                "a:has-text('Download')",
                "button:has-text('Download')",
                "a:has-text('ZIP')",
                "button:has-text('ZIP')",
                "a[download]",
            ]
            target = None
            for sel in selectors:
                loc = page.locator(sel)
                if loc.count() > 0:
                    target = loc.first
                    break
            if target is None:
                raise RuntimeError("Could not find Tender247 download button/link")

            with page.expect_download(timeout=timeout_ms) as dl_info:
                target.click()
            download = dl_info.value
            download.save_as(str(out_path))
            if not out_path.exists() or out_path.stat().st_size == 0:
                raise RuntimeError("Download did not produce a valid ZIP file")
            return out_path.name

        except PwTimeoutError:
            raise RuntimeError("Tender247 page timed out while loading/downloading")
        finally:
            context.close()
            browser.close()
