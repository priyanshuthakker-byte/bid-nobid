"""
Bid Assistant v2 — AI that acts like a real bid manager
Can answer, act, edit anything in the system in real-time.
"""
import json, re, os
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Any

BASE_DIR   = Path(__file__).parent
RUNTIME_DIR = Path(os.environ.get("BIDNOBID_RUNTIME_DIR", "/tmp/bid-nobid"))
OUTPUT_DIR = RUNTIME_DIR / "data"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
DB_FILE    = OUTPUT_DIR / "tenders_db.json"
CONFIG_PATH = BASE_DIR / "config.json"
PROFILE_PATH = BASE_DIR / "nascent_profile.json"
CHAT_HISTORY_FILE = OUTPUT_DIR / "chat_history.json"


def load_config():
    cfg = {}
    if CONFIG_PATH.exists():
        try: cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except: pass
    import os
    for key in ["GEMINI_API_KEY","GROQ_API_KEY"]:
        v = os.environ.get(key)
        if v: cfg[key.lower()] = v
    for i in range(2, 6):
        k = os.environ.get(f"GEMINI_API_KEY_{i}")
        if k:
            existing = cfg.get("gemini_api_keys", [])
            if k not in existing: existing.append(k)
            cfg["gemini_api_keys"] = existing
    return cfg

def load_db():
    if DB_FILE.exists():
        try: return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except: pass
    return {"tenders": {}}

def save_db(db):
    DB_FILE.parent.mkdir(exist_ok=True, parents=True)
    DB_FILE.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")

def load_profile():
    for path in [RUNTIME_DIR / "nascent_profile.json", PROFILE_PATH]:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except: pass
    return {}

def save_profile(p):
    for path in [RUNTIME_DIR / "nascent_profile.json", PROFILE_PATH]:
        try:
            path.write_text(json.dumps(p, indent=2), encoding="utf-8")
        except: pass

def load_history() -> List[Dict]:
    try:
        if CHAT_HISTORY_FILE.exists():
            return json.loads(CHAT_HISTORY_FILE.read_text(encoding="utf-8"))
    except: pass
    return []

def save_history(history: List[Dict]):
    try:
        CHAT_HISTORY_FILE.write_text(json.dumps(history[-60:], indent=2, default=str), encoding="utf-8")
    except: pass


_ctx_cache: Dict = {"ts": 0, "val": ""}

def build_context() -> str:
    """Rich context with 3-min cache to reduce redundant DB reads."""
    import time
    now = time.time()
    if now - _ctx_cache["ts"] < 180 and _ctx_cache["val"]:
        return _ctx_cache["val"]
    db = load_db()
    tenders = list(db["tenders"].values())
    profile = load_profile()
    rules = profile.get("bid_rules", {})
    finance = profile.get("finance", {})
    today = date.today()

    total = len(tenders)
    bid   = sum(1 for t in tenders if t.get("verdict") == "BID")
    cond  = sum(1 for t in tenders if t.get("verdict") == "CONDITIONAL")
    nobid = sum(1 for t in tenders if t.get("verdict") == "NO-BID")
    analysed = sum(1 for t in tenders if t.get("bid_no_bid_done"))

    # Pipeline stages
    stages = {}
    for t in tenders:
        s = t.get("status", "Identified")
        stages[s] = stages.get(s, 0) + 1

    # Urgent tenders
    urgent = []
    for t in tenders:
        dl = t.get("deadline","") or t.get("bid_submission_date","")
        if not dl: continue
        for fmt in ["%d-%m-%Y","%d/%m/%Y","%Y-%m-%d"]:
            try:
                d = datetime.strptime(str(dl).split()[0], fmt).date()
                days = (d - today).days
                if 0 <= days <= 14 and t.get("verdict") in ["BID","CONDITIONAL"]:
                    urgent.append(f"T247:{t.get('t247_id')} | {t.get('brief','')[:40]} | {days}d | {t.get('verdict')}")
                break
            except: continue

    # Recent BID tenders details
    bid_tenders = [t for t in tenders if t.get("verdict")=="BID"][:5]
    bid_details = " | ".join([f"{t.get('t247_id')}:{t.get('brief','')[:30]}" for t in bid_tenders])

    # Projects for experience matching
    projects = profile.get("projects", [])
    proj_summary = " | ".join([f"{p.get('name','')[:20]}(Rs.{p.get('value_lakhs',0)/100:.1f}Cr,{p.get('status','')})" for p in projects[:8]])

    dnb_count = len(rules.get("do_not_bid",[]))
    pref_list = ",".join(rules.get("preferred_sectors",[])[:8])
    cond_list = ",".join(rules.get("conditional",[])[:6])

    result = (
        f"TODAY:{today.strftime('%d-%b-%Y')} | "
        f"TENDERS:total={total},BID={bid},COND={cond},NOBID={nobid},analysed={analysed} | "
        f"PIPELINE:{json.dumps(stages)} | "
        f"URGENT_BID_TENDERS:{';'.join(urgent[:5]) if urgent else 'none'} | "
        f"BID_LIST:{bid_details} | "
        f"NASCENT:turnover_3yr=Rs.{finance.get('avg_turnover_last_3_fy',17.18)}Cr,"
        f"turnover_2yr=Rs.{finance.get('avg_turnover_last_2_fy',17.60)}Cr,"
        f"networth=Rs.{finance.get('net_worth_cr',26.09)}Cr,"
        f"employees=67(21ITdev,11GIS),CMMI_L3_Dec2026,ISO9001_27001_20000_Sep2028,"
        f"MSME=UDYAM-GJ-01-0007420,POA_EXPIRED_31Mar2026 | "
        f"PROJECTS:{proj_summary} | "
        f"RULES:DNB_count={dnb_count},preferred={pref_list},conditional={cond_list}"
    )
    import time
    _ctx_cache["ts"] = time.time()
    _ctx_cache["val"] = result
    return result


SYSTEM_PROMPT = """You are Bid Assistant — the AI brain of Nascent Info Technologies' tender management system.
You have full access to all tender data, company profile, bid rules, and can TAKE ACTIONS.

COMPANY: Nascent Info Technologies Pvt. Ltd., Ahmedabad, Gujarat
- GIS, Smart City, eGovernance, Mobile App development company
- MSME | CMMI L3 | ISO 9001/27001/20000 | 19 years | 67 employees
- POA of Hitesh Patel EXPIRED 31-Mar-2026 — CRITICAL issue

YOUR CAPABILITIES:
1. ANSWER any question about tenders, profile, deadlines, eligibility, pipeline
2. TAKE ACTIONS — return JSON action blocks to modify the system
3. TEACH the system — update bid rules when user gives instructions
4. ANALYSE — compare tender requirements against Nascent's profile
5. DRAFT — write queries, letters, notes in professional format

AVAILABLE ACTIONS (include at end of response as JSON if action needed):
{"action":"update_rule","rule_type":"do_not_bid","keyword":"keyword","remark":"why"}
{"action":"update_rule","rule_type":"conditional","keyword":"keyword","remark":"why"}
{"action":"update_rule","rule_type":"preferred","keyword":"keyword","remark":"why"}
{"action":"remove_rule","rule_type":"do_not_bid","keyword":"keyword"}
{"action":"update_stage","t247_id":"12345","stage":"Documents Ready","note":"optional note"}
{"action":"update_tender_note","t247_id":"12345","note":"note text"}
{"action":"update_tender_outcome","t247_id":"12345","outcome":"Won","loi_date":"15-Apr-2026","contract_value":"5.5","loi_ref":"LOI/XYZ/2026"}
{"action":"add_prebid_query","t247_id":"12345","clause":"Cl.5.1","rfp_text":"exact text","query":"query text","guideline":"guideline name"}
{"action":"update_project","project_id":"P001","field":"loi_received","value":"Yes"}
{"action":"add_guideline","name":"full name","short":"short name","applies_to":["keyword1"],"key_provisions":["provision"],"cite_as":"citation"}
{"action":"search_tenders","query":"search terms","filter":"BID"}
{"action":"mark_bid_decision","t247_id":"12345","decision":"BID","reason":"reason"}

RESPONSE RULES:
- Be direct and specific — this is a business tool, not a chatbot
- No markdown headers — plain professional text
- Numbers must be specific (not "several" — say "23 tenders")
- When taking action, confirm EXACTLY what you changed
- If user gives an instruction like "we don't bid on X" → execute update_rule action
- If user says "we won tender X" → execute update_tender_outcome action
- Always mention if POA needs renewal before submission
- Suggest next steps when relevant
- For guideline questions: cite specific rule/circular number

EXAMPLE INTERACTIONS:
User: "We don't bid on hardware supply"
You: "Understood. Adding 'hardware supply' to Do-Not-Bid rules." + action JSON

User: "What's our CMMI status?"
You: "CMMI V2.0 Level 3, valid till 19-Dec-2026. Certificate no. 68617."

User: "Update T247 98765 to Documents Ready stage"
You: "Done. T247-98765 moved to Documents Ready stage." + action JSON

User: "We won the SMC GIS tender — LOI received 15-Apr"
You: "Congratulations! Updating SMC GIS to Won with LOI date 15-Apr-2026. Remember: POA must be renewed before signing." + action JSON

User: "Raise a query for mandatory Microsoft .NET requirement"  
You: "Raising CVC-backed query on technology neutrality." + action JSON with pre-bid query
"""


def call_gemini_chat(messages: List[Dict], context: str, api_key: str) -> str:
    import urllib.request, urllib.error

    system = SYSTEM_PROMPT + "\n\nLIVE DATA:\n" + context
    conversation = "SYSTEM:\n" + system + "\n\n"
    for msg in messages[-4:]:
        role = "USER" if msg["role"] == "user" else "ASSISTANT"
        conversation += role + ": " + str(msg.get("content","")) + "\n\n"
    conversation += "ASSISTANT:"

    models = ["gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash-latest"]
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": conversation}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 600},
        }).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=payload,
                headers={"Content-Type":"application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code in [429, 404, 503]: continue
            return f"API error {e.code}"
        except Exception:
            continue

    # Groq fallback
    cfg = load_config()
    groq_key = cfg.get("groq_api_key","") or cfg.get("groq_key","")
    if groq_key:
        try:
            import urllib.request
            url = "https://api.groq.com/openai/v1/chat/completions"
            payload = json.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role":"user","content":conversation}],
                "max_tokens": 800, "temperature": 0.3,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload,
                headers={"Content-Type":"application/json","Authorization":f"Bearer {groq_key}"},
                method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
        except Exception as e:
            return f"All AI models at quota. Groq error: {str(e)[:100]}"

    return "All AI models at quota limit. Quota resets at 5:30 AM IST."


def extract_actions(text: str) -> List[Dict]:
    """Extract ALL action JSON blocks from response."""
    actions = []
    try:
        matches = re.findall(r'\{[^{}]*"action"[^{}]*\}', text, re.DOTALL)
        for m in matches:
            try:
                actions.append(json.loads(m))
            except: pass
    except: pass
    return actions


def _bust_ctx_cache():
    _ctx_cache["ts"] = 0

def execute_action(action: Dict) -> str:
    """Execute action and return human-readable result."""
    act = action.get("action","")

    if act == "update_rule":
        rule_type = action.get("rule_type","")
        keyword   = action.get("keyword","").lower().strip()
        remark    = action.get("remark","")
        if not keyword: return ""
        profile = load_profile()
        rules = profile.get("bid_rules",{})
        key_map = {"do_not_bid":"do_not_bid","conditional":"conditional","preferred":"preferred_sectors"}
        list_key = key_map.get(rule_type)
        if not list_key: return ""
        current = rules.get(list_key,[])
        if keyword not in current:
            current.append(keyword)
            rules[list_key] = current
            if remark and rule_type=="do_not_bid":
                remarks = rules.get("do_not_bid_remarks",{})
                remarks[keyword] = remark
                rules["do_not_bid_remarks"] = remarks
            profile["bid_rules"] = rules
            save_profile(profile)
            _bust_ctx_cache()
            return f"✓ Rule added: '{keyword}' → {rule_type.replace('_',' ')}"
        return f"Rule already exists: '{keyword}'"

    elif act == "remove_rule":
        rule_type = action.get("rule_type","")
        keyword   = action.get("keyword","").lower().strip()
        profile = load_profile()
        rules = profile.get("bid_rules",{})
        key_map = {"do_not_bid":"do_not_bid","conditional":"conditional","preferred":"preferred_sectors"}
        list_key = key_map.get(rule_type)
        if list_key and keyword in rules.get(list_key,[]):
            rules[list_key].remove(keyword)
            profile["bid_rules"] = rules
            save_profile(profile)
            return f"✓ Rule removed: '{keyword}' from {rule_type}"
        return f"Rule not found: '{keyword}'"

    elif act == "update_stage":
        t247_id = str(action.get("t247_id",""))
        stage   = action.get("stage","")
        note    = action.get("note","")
        db = load_db()
        t = db["tenders"].get(t247_id)
        if t:
            t["status"] = stage
            t["status_updated_at"] = datetime.now().isoformat()
            if note: t["notes"] = (t.get("notes","") + f"\n[{date.today()}] {note}").strip()
            db["tenders"][t247_id] = t
            save_db(db)
            return f"✓ T247-{t247_id} → {stage}" + (f" | Note: {note}" if note else "")
        return f"Tender {t247_id} not found"

    elif act == "update_tender_note":
        t247_id = str(action.get("t247_id",""))
        note    = action.get("note","")
        db = load_db()
        t = db["tenders"].get(t247_id)
        if t:
            existing = t.get("notes","")
            t["notes"] = (existing + f"\n[{date.today()}] {note}").strip() if existing else f"[{date.today()}] {note}"
            db["tenders"][t247_id] = t
            save_db(db)
            return f"✓ Note added to T247-{t247_id}"
        return f"Tender {t247_id} not found"

    elif act == "update_tender_outcome":
        t247_id  = str(action.get("t247_id",""))
        outcome  = action.get("outcome","")
        loi_date = action.get("loi_date","")
        loi_ref  = action.get("loi_ref","")
        contract = action.get("contract_value","")
        db = load_db()
        t = db["tenders"].get(t247_id)
        if t:
            t["outcome"] = outcome
            t["status"]  = outcome  # Won / Lost / Submitted
            if loi_date: t["loi_date"] = loi_date
            if loi_ref:  t["loi_ref"]  = loi_ref
            if contract: t["awarded_value_cr"] = contract
            t["outcome_updated_at"] = datetime.now().isoformat()
            db["tenders"][t247_id] = t
            save_db(db)
            parts = [f"✓ T247-{t247_id} outcome: {outcome}"]
            if loi_date: parts.append(f"LOI date: {loi_date}")
            if loi_ref:  parts.append(f"LOI ref: {loi_ref}")
            if contract: parts.append(f"Contract value: Rs.{contract}Cr")
            return " | ".join(parts)
        return f"Tender {t247_id} not found"

    elif act == "add_prebid_query":
        t247_id = str(action.get("t247_id",""))
        db = load_db()
        t = db["tenders"].get(t247_id)
        if t:
            queries = t.get("prebid_queries",[])
            new_q = {
                "query_no": f"Q{len(queries)+1}",
                "clause_ref": action.get("clause","—"),
                "rfp_text": action.get("rfp_text",""),
                "query": action.get("query",""),
                "guideline_cited": action.get("guideline",""),
                "clarification_sought": action.get("clarification","Written confirmation required"),
                "priority": action.get("priority","MEDIUM"),
                "added_by": "chat",
                "added_at": datetime.now().isoformat(),
            }
            queries.append(new_q)
            t["prebid_queries"] = queries
            db["tenders"][t247_id] = t
            save_db(db)
            return f"✓ Pre-bid query added to T247-{t247_id} ({len(queries)} total)"
        return f"Tender {t247_id} not found"

    elif act == "mark_bid_decision":
        t247_id  = str(action.get("t247_id",""))
        decision = action.get("decision","")
        reason   = action.get("reason","")
        db = load_db()
        t = db["tenders"].get(t247_id)
        if t:
            t["verdict"]         = decision
            t["verdict_reason"]  = reason
            t["decision_date"]   = date.today().isoformat()
            t["decision_by"]     = "Chat AI"
            db["tenders"][t247_id] = t
            save_db(db)
            return f"✓ T247-{t247_id} bid decision: {decision} | Reason: {reason}"
        return f"Tender {t247_id} not found"

    elif act == "update_project":
        project_id = action.get("project_id","")
        field      = action.get("field","")
        value      = action.get("value","")
        profile = load_profile()
        projects = profile.get("projects",[])
        for proj in projects:
            if proj.get("id") == project_id:
                proj[field] = value
                save_profile(profile)
                return f"✓ Project {project_id}: {field} = {value}"
        return f"Project {project_id} not found"

    elif act == "add_guideline":
        try:
            from guidelines_library import add_custom_guideline
            gl = add_custom_guideline(
                name=action.get("name",""),
                short=action.get("short",""),
                category=action.get("category","Custom"),
                applies_to=action.get("applies_to",[]),
                key_provisions=action.get("key_provisions",[]),
                authority=action.get("authority",""),
                cite_as=action.get("cite_as",""),
            )
            return f"✓ Guideline added: {gl['short']}"
        except Exception as e:
            return f"Guideline add failed: {e}"

    elif act == "search_tenders":
        query  = action.get("query","").lower()
        filter_v = action.get("filter","")
        db = load_db()
        results = []
        for tid, t in db["tenders"].items():
            text = f"{t.get('brief','')} {t.get('org_name','')} {t.get('location','')}".lower()
            if query in text:
                if not filter_v or t.get("verdict") == filter_v:
                    results.append(f"T247-{tid}: {t.get('brief','')[:50]} | {t.get('verdict','')} | {t.get('deadline','')}")
        if results:
            return f"Found {len(results)} tenders:\n" + "\n".join(results[:10])
        return f"No tenders found matching '{query}'"

    return ""


def clean_response(text: str) -> str:
    """Remove action JSON blocks from display text."""
    text = re.sub(r'\{[^{}]*"action"[^{}]*\}', '', text, flags=re.DOTALL)
    return text.strip()


def process_message(user_message: str, history: List[Dict]) -> Dict:
    """Process user message. Return response + any action results."""
    config = load_config()
    api_key = config.get("gemini_api_key","")
    if not api_key:
        return {
            "response": "AI not configured. Add Gemini API key in Settings.",
            "action_result": None
        }

    context = build_context()
    history.append({"role":"user","content":user_message})

    try:
        response_text = call_gemini_chat(history, context, api_key)
    except Exception as e:
        response_text = f"Error: {str(e)[:100]}"

    # Execute all actions
    actions = extract_actions(response_text)
    action_results = []
    for action in actions:
        try:
            result = execute_action(action)
            if result:
                action_results.append(result)
        except Exception as e:
            action_results.append(f"Action error: {str(e)[:50]}")

    clean_text = clean_response(response_text)
    history.append({"role":"assistant","content":clean_text})
    save_history(history)

    return {
        "response":      clean_text,
        "action":        actions[0] if actions else None,
        "action_result": " | ".join(action_results) if action_results else None,
        "all_actions":   actions,
        "history":       history,
    }
