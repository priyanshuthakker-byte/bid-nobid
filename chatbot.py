"""
Bid Assistant Chatbot
- Answers questions about tenders, Nascent profile, pipeline
- Takes actions: update stage, update rules, generate queries
- Learns from instructions: "we don't bid on X" → updates bid rules
- Uses Gemini API (same free key)
"""

import json
import re
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Any

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)  # Ensure exists
DB_FILE    = OUTPUT_DIR / "tenders_db.json"
CONFIG_PATH = BASE_DIR / "config.json"
PROFILE_PATH = BASE_DIR / "nascent_profile.json"
CHAT_HISTORY_FILE = OUTPUT_DIR / "chat_history.json"


def load_config():
    cfg = {}
    if CONFIG_PATH.exists():
        try: cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except: pass
    # Read from environment variables (for Render deployment)
    import os
    if os.environ.get("GEMINI_API_KEY"):
        cfg["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
    if os.environ.get("GROQ_API_KEY"):
        cfg["groq_api_key"] = os.environ["GROQ_API_KEY"]
    for i in range(2, 6):
        k = os.environ.get(f"GEMINI_API_KEY_{i}")
        if k:
            existing = cfg.get("gemini_api_keys", [])
            if k not in existing:
                existing.append(k)
            cfg["gemini_api_keys"] = existing
    return cfg

def load_db():
    if DB_FILE.exists():
        try: return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except: pass
    return {"tenders": {}}

def save_db(db):
    DB_FILE.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")

def load_profile():
    if PROFILE_PATH.exists():
        try: return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except: pass
    return {}

def save_profile(p):
    PROFILE_PATH.write_text(json.dumps(p, indent=2), encoding="utf-8")

def load_history() -> List[Dict]:
    try:
        if CHAT_HISTORY_FILE.exists():
            return json.loads(CHAT_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []

def save_history(history: List[Dict]):
    try:
        OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
        CHAT_HISTORY_FILE.write_text(
            json.dumps(history[-50:], indent=2, default=str), encoding="utf-8")
    except Exception as e:
        pass  # Don't crash if can't save history


def build_context() -> str:
    """Build minimal context — only what Gemini needs for this message"""
    db = load_db()
    tenders = list(db["tenders"].values())
    profile = load_profile()
    rules = profile.get("bid_rules", {})
    today = date.today()

    total = len(tenders)
    bid   = sum(1 for t in tenders if t.get("verdict") == "BID")
    cond  = sum(1 for t in tenders if t.get("verdict") == "CONDITIONAL")
    nobid = sum(1 for t in tenders if t.get("verdict") == "NO-BID")

    stages = {}
    for t in tenders:
        s = t.get("status", "Identified")
        stages[s] = stages.get(s, 0) + 1

    urgent = []
    for t in tenders:
        dl_str = t.get("deadline", "")
        if not dl_str: continue
        for fmt in ["%d-%m-%Y", "%d/%m/%Y"]:
            try:
                days = (datetime.strptime(dl_str.split()[0], fmt).date() - today).days
                if 0 <= days <= 7 and t.get("verdict") in ["BID","CONDITIONAL"]:
                    urgent.append(f"{t.get('t247_id')}|{t.get('brief','')[:40]}|{days}d")
                break
            except: continue

    finance = profile.get("finance", {})
    dnb  = rules.get("do_not_bid", [])
    pref = rules.get("preferred_sectors", [])[:8]

    return (
        f"DATE:{today.strftime('%d-%b-%Y')} "
        f"TENDERS: total={total} BID={bid} COND={cond} NOBID={nobid} "
        f"PIPELINE:{json.dumps(stages)} "
        f"URGENT:{';'.join(urgent[:5]) if urgent else 'none'} "
        f"NASCENT: turnover=Rs.{finance.get('avg_turnover_last_3_fy',17.18)}Cr "
        f"networth=Rs.{finance.get('net_worth_cr',26.09)}Cr "
        f"CMMI=L3 ISO9001 ISO27001 employees=67 MSME=UDYAM-GJ-01-0007420 "
        f"DO_NOT_BID_COUNT={len(dnb)} "
        f"PREFERRED:{','.join(pref[:6])}"
    )


def build_system_prompt() -> str:
    return """You are Bid Assistant — an intelligent assistant for Nascent Info Technologies Pvt. Ltd.'s tender management system.

You can:
1. ANSWER questions about tenders, deadlines, eligibility, Nascent profile
2. TAKE ACTIONS by returning special JSON commands
3. UPDATE RULES when user says "we don't bid on X" or "add X to no-bid list"
4. SEARCH tenders by any criteria

When user asks you to DO something (not just answer), include an ACTION in your response.

AVAILABLE ACTIONS (include as JSON at end of response if needed):
{"action": "update_rule", "rule_type": "do_not_bid", "keyword": "gis licence supply", "remark": "Not our domain"}
{"action": "update_rule", "rule_type": "conditional", "keyword": "keyword here"}
{"action": "update_rule", "rule_type": "preferred", "keyword": "keyword here"}
{"action": "remove_rule", "rule_type": "do_not_bid", "keyword": "keyword to remove"}
{"action": "update_stage", "t247_id": "12345", "stage": "Documents Ready"}
{"action": "search_tenders", "query": "gis gujarat", "filter": "BID"}
{"action": "show_tender", "t247_id": "12345"}
{"action": "generate_prebid", "t247_id": "12345"}

RULES:
- Be concise and direct — this is a business tool
- Use plain text, no markdown headers
- Numbers should be specific (not "many" — say "23 tenders")
- When updating rules, confirm what you changed
- If asked about a specific tender, give specific details
- Always suggest next action when relevant"""


def call_gemini_chat(messages: List[Dict], context: str, api_key: str) -> str:
    import urllib.request, urllib.error

    # Build single prompt — most reliable approach
    system = build_system_prompt() + "\n\nCURRENT DATA:\n" + context

    conversation = "SYSTEM:\n" + system + "\n\n"
    for msg in messages[-4:]:
        role = "USER" if msg["role"] == "user" else "ASSISTANT"
        conversation += role + ": " + str(msg.get("content","")) + "\n\n"
    conversation += "ASSISTANT:"

    # Same models and format as ai_analyzer.py — proven to work
    models = ["gemini-1.5-pro-latest", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

    for model in models:
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               + model + ":generateContent?key=" + api_key)

        payload = json.dumps({
            "contents": [
                {"parts": [{"text": conversation}]}
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 512,
            }
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            return text

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                continue  # Try next model
            if e.code == 404:
                continue  # Model not found, try next
            # For 400 — log and return friendly message
            return "Sorry, I could not process that request. (API error " + str(e.code) + ")"

        except Exception as e:
            continue

    # Try Groq as final fallback — check all possible key names
    _cfg = load_config()
    groq_key = (
        _cfg.get("groq_api_key") or
        _cfg.get("groq_key") or
        _cfg.get("GROQ_API_KEY") or
        _cfg.get("groq") or ""
    )
    if groq_key:
        try:
            import urllib.request, urllib.error
            url = "https://api.groq.com/openai/v1/chat/completions"
            payload = json.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": conversation}],
                "max_tokens": 512,
                "temperature": 0.3,
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + groq_key},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            logger.error(f"Groq HTTP error {e.code}: {body}")
            return f"Groq API error {e.code}: {body[:100]}"
        except Exception as e:
            logger.error(f"Groq failed: {e}")
            return f"Groq connection failed: {str(e)[:100]}. Check your API key in Render environment variables."
    else:
        logger.warning("No Groq key found in config")

    return "All AI models are currently at quota limit. Gemini quota resets at 5:30 AM IST tomorrow."


def extract_action(response_text: str):
    """Extract action JSON from response if present"""
    try:
        # Look for JSON action block
        matches = re.findall(r'\{[^{}]*"action"[^{}]*\}', response_text)
        for m in matches:
            try:
                return json.loads(m)
            except:
                continue
    except:
        pass
    return None


def execute_action(action: Dict) -> str:
    """Execute the action and return result message"""
    act = action.get("action", "")

    if act == "update_rule":
        rule_type = action.get("rule_type", "")
        keyword   = action.get("keyword", "").lower().strip()
        remark    = action.get("remark", "")
        if not keyword:
            return ""
        profile = load_profile()
        rules = profile.get("bid_rules", {})
        key_map = {"do_not_bid": "do_not_bid",
                   "conditional": "conditional",
                   "preferred":   "preferred_sectors"}
        list_key = key_map.get(rule_type)
        if not list_key:
            return ""
        current = rules.get(list_key, [])
        if keyword not in current:
            current.append(keyword)
            rules[list_key] = current
            if remark and rule_type == "do_not_bid":
                remarks = rules.get("do_not_bid_remarks", {})
                remarks[keyword] = remark
                rules["do_not_bid_remarks"] = remarks
            profile["bid_rules"] = rules
            save_profile(profile)
            return f"Rule added: '{keyword}' → {rule_type.replace('_',' ')}"
        return f"Rule already exists: '{keyword}'"

    elif act == "remove_rule":
        rule_type = action.get("rule_type", "")
        keyword   = action.get("keyword", "").lower().strip()
        profile = load_profile()
        rules = profile.get("bid_rules", {})
        key_map = {"do_not_bid": "do_not_bid",
                   "conditional": "conditional",
                   "preferred":   "preferred_sectors"}
        list_key = key_map.get(rule_type)
        if list_key and keyword in rules.get(list_key, []):
            rules[list_key].remove(keyword)
            profile["bid_rules"] = rules
            save_profile(profile)
            return f"Rule removed: '{keyword}'"
        return f"Rule not found: '{keyword}'"

    elif act == "update_stage":
        t247_id = action.get("t247_id", "")
        stage   = action.get("stage", "")
        db = load_db()
        if t247_id in db["tenders"]:
            db["tenders"][t247_id]["status"] = stage
            db["tenders"][t247_id]["status_updated_at"] = datetime.now().isoformat()
            save_db(db)
            return f"Stage updated for {t247_id}: {stage}"
        return f"Tender {t247_id} not found"

    return ""


def clean_response_text(text: str) -> str:
    """Remove action JSON from display text"""
    text = re.sub(r'\{[^{}]*"action"[^{}]*\}', '', text)
    return text.strip()


def process_message(user_message: str, history: List[Dict]) -> Dict:
    """
    Process a user message and return response with optional action
    """
    config = load_config()
    api_key = config.get("gemini_api_key", "")
    if not api_key:
        return {
            "response": "AI is not configured. Please add your Gemini API key in Settings.",
            "action_result": None
        }

    # Build context
    context = build_context()

    # Add user message to history
    history.append({"role": "user", "content": user_message})

    # Call Gemini
    try:
        response_text = call_gemini_chat(history, context, api_key)
    except Exception as e:
        response_text = "Sorry, I encountered an error: " + str(e)[:100]

    # Extract and execute action
    action = extract_action(response_text)
    action_result = None
    if action:
        try:
            action_result = execute_action(action)
        except Exception as e:
            action_result = "Action failed: " + str(e)[:50]

    # Clean response for display
    clean_text = clean_response_text(response_text)

    # Add to history
    history.append({"role": "assistant", "content": clean_text})
    save_history(history)

    return {
        "response": clean_text,
        "action": action,
        "action_result": action_result,
        "history": history
    }
