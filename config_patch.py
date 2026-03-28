"""
PATCH FOR main.py — replace the existing update_config_route function
(find @app.post("/config") in main.py and replace the entire function with this)

The old function only saved gemini_api_key (single key).
This new version saves all 4 keys + groq + t247 credentials.
"""

@app.post("/config")
async def update_config_route(data: dict = Body(...)):
    config = load_config()

    # ── Gemini keys — handle both single key and array ──
    # New format: array of keys
    if "gemini_api_keys" in data:
        keys = [k.strip() for k in data["gemini_api_keys"] if k and k.strip() and len(k.strip()) > 20]
        if keys:
            config["gemini_api_keys"] = keys
            config["gemini_api_key"] = keys[0]   # primary key = first in list

    # Old format: single key (keep for backwards compat)
    if "gemini_api_key" in data and data["gemini_api_key"] and data["gemini_api_key"] not in (config.get("gemini_api_keys") or []):
        k = data["gemini_api_key"].strip()
        if k and len(k) > 20:
            config["gemini_api_key"] = k
            existing = config.get("gemini_api_keys", [])
            if k not in existing:
                config["gemini_api_keys"] = [k] + existing

    # ── Groq key ──
    if "groq_api_key" in data and data["groq_api_key"]:
        config["groq_api_key"] = data["groq_api_key"].strip()

    # ── T247 credentials ──
    if "t247_username" in data and data["t247_username"]:
        config["t247_username"] = data["t247_username"].strip()
    if "t247_password" in data and data["t247_password"]:
        config["t247_password"] = data["t247_password"]   # don't strip passwords

    save_config(config)

    # Count saved keys
    all_keys = config.get("gemini_api_keys", [])
    if not all_keys and config.get("gemini_api_key"):
        all_keys = [config["gemini_api_key"]]

    return {
        "status": "saved",
        "gemini_keys_saved": len(all_keys),
        "groq_saved": bool(config.get("groq_api_key")),
        "t247_saved": bool(config.get("t247_username")),
    }


# ── Also replace /config GET to return proper status ──
@app.get("/config")
async def get_config_route():
    config = load_config()
    primary = config.get("gemini_api_key", "")
    all_keys = config.get("gemini_api_keys", [])
    if primary and primary not in all_keys:
        all_keys = [primary] + all_keys

    return {
        "gemini_api_key_set": bool(primary or all_keys),
        "gemini_api_key": primary,
        "gemini_keys_count": len(all_keys),
        # Backward compat fields
        "gemini_api_key_2": all_keys[1] if len(all_keys) > 1 else "",
        "gemini_api_key_3": all_keys[2] if len(all_keys) > 2 else "",
        "gemini_api_key_4": all_keys[3] if len(all_keys) > 3 else "",
        "t247_username": config.get("t247_username", ""),
    }


# ── Replace /config-full to return masked key previews ──
@app.get("/config-full")
async def get_config_full():
    config = load_config()
    all_keys = config.get("gemini_api_keys", [])
    primary = config.get("gemini_api_key", "")
    if primary and primary not in all_keys:
        all_keys = [primary] + all_keys
    all_keys = [k for k in all_keys if k and len(k) > 8]

    masked = []
    for k in all_keys:
        if len(k) > 12:
            masked.append(k[:8] + "..." + k[-4:])
        else:
            masked.append(k[:4] + "...")

    return {
        "gemini_api_keys": masked,
        "total_keys": len(all_keys),
        "ai_active": bool(all_keys),
        "groq_configured": bool(config.get("groq_api_key")),
    }
