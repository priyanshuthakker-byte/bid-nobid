 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/main.py b/main.py
index 25f7ed3e2949aaa7dd930f7d0903ade581f380d5..b3196c384e901d74cc49c3ef92c61937742d976b 100644
--- a/main.py
+++ b/main.py
@@ -3378,202 +3378,51 @@ def _update_t247_sync_state(status: str, message: str = "", total: int = 0, adde
 
 def _run_t247_sync_scheduler():
     """Periodic Tender247 sync loop (manual trigger still available)."""
     _time.sleep(60)
     while not _t247_sync_stop.is_set():
         try:
             cfg = load_config()
             enabled = bool(cfg.get("t247_auto_sync_enabled", True))
             minutes = int(cfg.get("t247_auto_sync_minutes", 180) or 180)
             minutes = max(15, min(720, minutes))
             if enabled:
                 result = _run_t247_sync_once()
                 _update_t247_sync_state(
                     "success",
                     "auto sync completed",
                     total=result.get("total", 0),
                     added=result.get("added", 0),
                     updated=result.get("updated", 0),
                 )
             sleep_for = minutes * 60
         except Exception as e:
             _update_t247_sync_state("error", f"auto sync failed: {e}")
             sleep_for = 600
         _t247_sync_stop.wait(sleep_for)
 
-def _run_daily_digest_scheduler():
-    """Generate email/whatsapp style digest daily at configured time."""
-    _time.sleep(45)
-    while not _t247_sync_stop.is_set():
-        try:
-            cfg = load_config()
-            enabled = bool(cfg.get("daily_digest_enabled", True))
-            hour = int(cfg.get("daily_digest_hour", 9) or 9)
-            minute = int(cfg.get("daily_digest_minute", 0) or 0)
-            hour = max(0, min(23, hour))
-            minute = max(0, min(59, minute))
-            now = datetime.now()
-            today_key = now.strftime("%Y-%m-%d")
-            should_run = enabled and now.hour == hour and now.minute == minute
-            already_done = _digest_state.get("last_generated_date") == today_key
-            if should_run and not already_done:
-                _build_daily_digest()
-        except Exception as e:
-            with _digest_lock:
-                _digest_state["status"] = "error"
-                _digest_state["error"] = str(e)
-        _t247_sync_stop.wait(55)
-
-@app.get("/test-t247")
-async def test_t247():
-    cfg = load_config()
-    token = str(cfg.get("t247_bearer_token", "") or "").strip()
-    if not token:
-        return {"status": "error", "message": "No Bearer token saved. Paste it in Settings → T247 Token."}
-    payload = _t247_decode_jwt(token)
-    if not payload:
-        return {"status": "error", "message": "Invalid token format. Paste the full Bearer token from DevTools."}
-    import time as _t
-    exp = payload.get("exp", 0)
-    remaining_hrs = round((exp - _t.time()) / 3600, 1) if exp else 0
-    if exp and _t.time() > exp:
-        return {"status": "error", "message": f"Token expired {abs(remaining_hrs):.1f}h ago. Log into tender247.com and paste a fresh token."}
-    return {
-        "status": "success",
-        "message": f"✅ Token valid for {remaining_hrs}h | User: {payload.get('bidder_name','?')} | ID: {payload.get('UserId','?')}",
-        "user_id": payload.get("UserId"),
-        "bidder_name": payload.get("bidder_name"),
-        "expires_in_hours": remaining_hrs,
-        "keywords": payload.get("WordHighlight", "").split(",")[:5],
-    }
-
-@app.get("/t247-token-status")
-async def t247_token_status():
-    return await test_t247()
-
-@app.post("/fetch-t247-excel")
-async def fetch_t247_excel(background_tasks: BackgroundTasks):
-    """Manual trigger for Tender247 sync."""
-    try:
-        result = _run_t247_sync_once()
-        _update_t247_sync_state(
-            "success",
-            "manual sync completed",
-            total=result.get("total", 0),
-            added=result.get("added", 0),
-            updated=result.get("updated", 0),
-        )
-        return result
-    except ValueError as e:
-        _update_t247_sync_state("error", str(e))
-        raise HTTPException(400, str(e))
-    except HTTPException:
-        raise
-    except Exception as e:
-        _update_t247_sync_state("error", str(e))
-        raise HTTPException(502, f"T247 sync failed: {e}")
-
-@app.post("/fetch-t247-excel/retry")
-async def retry_t247_sync():
-    return await fetch_t247_excel(BackgroundTasks())
-
-@app.post("/ops/daily-digest/generate")
-async def generate_daily_digest():
-    try:
-        digest = _build_daily_digest()
-        return {"status": "success", **digest}
-    except Exception as e:
-        raise HTTPException(500, f"Digest generation failed: {e}")
-
-@app.get("/ops/daily-digest")
-async def get_daily_digest():
-    p = OUTPUT_DIR / "daily_digest_latest.json"
-    if p.exists():
-        try:
-            data = json.loads(p.read_text(encoding="utf-8"))
-            return {"status": "success", **data, "scheduler": dict(_digest_state)}
-        except Exception:
-            pass
-    digest = _build_daily_digest()
-    return {"status": "success", **digest, "scheduler": dict(_digest_state)}
-
-@app.get("/ops/daily-digest.txt")
-async def download_daily_digest_text():
-    p = OUTPUT_DIR / "daily_digest_latest.txt"
-    if not p.exists():
-        _build_daily_digest()
-    return FileResponse(
-        path=str(p),
-        filename=f"daily_digest_{datetime.now().strftime('%Y%m%d')}.txt",
-        media_type="text/plain",
-    )
-
-@app.get("/t247-sync-status")
-async def t247_sync_status():
-    cfg = load_config()
-    return {
-        "status": "success",
-        "auto_sync_enabled": bool(cfg.get("t247_auto_sync_enabled", True)),
-        "auto_sync_minutes": int(cfg.get("t247_auto_sync_minutes", 180) or 180),
-        **_t247_sync_state,
-
-    }
-
-def _update_t247_sync_state(status: str, message: str = "", total: int = 0, added: int = 0, updated: int = 0):
-    with _t247_sync_lock:
-        _t247_sync_state["last_run_at"] = datetime.now().isoformat()
-        _t247_sync_state["last_status"] = status
-        _t247_sync_state["last_message"] = message
-        _t247_sync_state["last_total"] = int(total or 0)
-        _t247_sync_state["last_added"] = int(added or 0)
-        _t247_sync_state["last_updated"] = int(updated or 0)
-
-def _run_t247_sync_scheduler():
-    """Periodic Tender247 sync loop (manual trigger still available)."""
-    _time.sleep(60)
-    while not _t247_sync_stop.is_set():
-        try:
-            cfg = load_config()
-            enabled = bool(cfg.get("t247_auto_sync_enabled", True))
-            minutes = int(cfg.get("t247_auto_sync_minutes", 180) or 180)
-            minutes = max(15, min(720, minutes))
-            if enabled:
-                result = _run_t247_sync_once()
-                _update_t247_sync_state(
-                    "success",
-                    "auto sync completed",
-                    total=result.get("total", 0),
-                    added=result.get("added", 0),
-                    updated=result.get("updated", 0),
-                )
-            sleep_for = minutes * 60
-        except Exception as e:
-            _update_t247_sync_state("error", f"auto sync failed: {e}")
-            sleep_for = 600
-        _t247_sync_stop.wait(sleep_for)
-
-def _run_daily_digest_scheduler():
+def _run_daily_digest_scheduler():
     """Generate email/whatsapp style digest daily at configured time."""
     _time.sleep(45)
     while not _t247_sync_stop.is_set():
         try:
             cfg = load_config()
             enabled = bool(cfg.get("daily_digest_enabled", True))
             hour = int(cfg.get("daily_digest_hour", 9) or 9)
             minute = int(cfg.get("daily_digest_minute", 0) or 0)
             hour = max(0, min(23, hour))
             minute = max(0, min(59, minute))
             now = datetime.now()
             today_key = now.strftime("%Y-%m-%d")
             should_run = enabled and now.hour == hour and now.minute == minute
             already_done = _digest_state.get("last_generated_date") == today_key
             if should_run and not already_done:
                 _build_daily_digest()
         except Exception as e:
             with _digest_lock:
                 _digest_state["status"] = "error"
                 _digest_state["error"] = str(e)
         _t247_sync_stop.wait(55)
 
 @app.post("/t247/connect")
 async def t247_connect(data: dict = Body(...)):
     """Save T247 email+password and immediately login to verify + store token."""
 
EOF
)
