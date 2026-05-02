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


def _offline_chat(user_message: str, history: List[Dict]) -> Dict:
    """
    Fully rule-based Bid Assistant — zero API calls.
    Handles pipeline queries, profile lookups, action commands, deadline alerts.
    """
    db      = load_db()
    profile = load_profile()
    tenders = list(db.get("tenders", {}).values())
    rules   = profile.get("bid_rules", {})
    finance = profile.get("finance", {})
    certs   = profile.get("certifications", {})
    company = profile.get("company", {})
    employees = profile.get("employees", {})
    projects  = profile.get("projects", [])
    today   = date.today()
    msg     = user_message.lower().strip()

    response = ""
    actions_to_exec = []

    # ── 1. HELP ──────────────────────────────────────────────────
    if any(x in msg for x in ["help", "what can you", "commands", "what do you"]):
        response = (
            "Offline Bid Assistant — no API key needed. I can:\n"
            "- Pipeline: 'show pipeline', 'urgent deadlines', 'how many BID tenders'\n"
            "- Profile: 'our CMMI', 'our turnover', 'our employees', 'our projects'\n"
            "- Search: 'find GIS tenders', 'tenders in Gujarat'\n"
            "- Deadline: 'what's due this week', 'deadlines next 30 days'\n"
            "- Actions: 'move T247 XXXXX to Documents Ready', 'add rule: no hardware supply'\n"
            "           'we won T247 XXXXX', 'mark T247 XXXXX as BID'\n"
            "- Compliance: 'cert status', 'POA expiry'\n"
            "- Stats: 'show stats', 'win rate'\n"
            "Add Gemini API key in Settings for full AI conversation."
        )
        return _offline_return(response, history, user_message)

    # ── 2. PIPELINE / STATS ───────────────────────────────────────
    if any(x in msg for x in ["pipeline", "stats", "summary", "how many", "count", "total tender"]):
        bid   = [t for t in tenders if t.get("verdict") == "BID"]
        cond  = [t for t in tenders if t.get("verdict") == "CONDITIONAL"]
        nobid = [t for t in tenders if t.get("verdict") == "NO-BID" or t.get("verdict") == "NO_BID"]
        analysed = [t for t in tenders if t.get("bid_no_bid_done")]
        stages = {}
        for t in tenders:
            s = t.get("status","Identified")
            stages[s] = stages.get(s,0)+1
        won  = sum(1 for t in tenders if t.get("outcome") == "Won")
        lost = sum(1 for t in tenders if t.get("outcome") == "Lost")
        sub  = sum(1 for t in tenders if t.get("outcome") == "Submitted")
        win_rate = f"{won/(won+lost)*100:.0f}%" if (won+lost) > 0 else "N/A"
        response = (
            f"Pipeline Summary ({today.strftime('%d-%b-%Y')})\n"
            f"Total tenders: {len(tenders)} | Analysed: {len(analysed)}\n"
            f"Verdict: BID={len(bid)} | CONDITIONAL={len(cond)} | NO-BID={len(nobid)}\n"
            f"Outcomes: Won={won} | Submitted={sub} | Lost={lost} | Win rate: {win_rate}\n"
            f"Stages: {' | '.join(f'{k}:{v}' for k,v in sorted(stages.items(),key=lambda x:-x[1])[:6])}"
        )
        return _offline_return(response, history, user_message)

    # ── 3. URGENT / DEADLINES ─────────────────────────────────────
    if any(x in msg for x in ["urgent", "deadline", "due", "this week", "next week", "expir", "closing"]):
        days_window = 30
        if "week" in msg: days_window = 7
        if "today" in msg: days_window = 1
        # parse custom days: "next 14 days"
        m = re.search(r"(\d+)\s*days?", msg)
        if m:
            days_window = int(m.group(1))

        urgent = []
        for t in tenders:
            dl = t.get("deadline","") or t.get("bid_submission_date","")
            if not dl: continue
            for fmt in ["%d-%m-%Y","%d/%m/%Y","%Y-%m-%d","%d %b %Y","%d-%b-%Y"]:
                try:
                    d = datetime.strptime(str(dl).split()[0].strip(), fmt).date()
                    days_left = (d - today).days
                    if 0 <= days_left <= days_window:
                        verdict = t.get("verdict","?")
                        urgent.append((days_left, d, t.get("t247_id","?"), t.get("brief","")[:45], verdict, t.get("status","?")))
                    break
                except: continue
        urgent.sort(key=lambda x: x[0])

        if urgent:
            lines = [f"Deadlines in next {days_window} days ({len(urgent)} tenders):"]
            for days_left, d, tid, brief, verdict, status in urgent[:12]:
                flag = "TODAY" if days_left == 0 else f"{days_left}d"
                lines.append(f"  [{flag}] T247-{tid} | {brief} | {verdict} | {status} | {d.strftime('%d-%b')}")
            response = "\n".join(lines)
        else:
            response = f"No tenders with deadlines in next {days_window} days (of {len(tenders)} total)."
        return _offline_return(response, history, user_message)

    # ── 4. FIND / SEARCH TENDERS ─────────────────────────────────
    if any(x in msg for x in ["find", "search", "show tenders", "tenders in", "tenders for", "gis tender", "smart city tender"]):
        # Extract search terms
        query_words = re.sub(r"find|search|show|tenders?|in|for|about|related to|with", "", msg).strip()
        query_words = [w for w in query_words.split() if len(w) > 2]
        filter_verdict = ""
        if "bid " in msg: filter_verdict = "BID"
        if "conditional" in msg: filter_verdict = "CONDITIONAL"
        if "no-bid" in msg or "no bid" in msg: filter_verdict = "NO-BID"

        matches = []
        for t in tenders:
            text = f"{t.get('brief','')} {t.get('org_name','')} {t.get('location','')} {t.get('sector','')}".lower()
            score = sum(1 for w in query_words if w in text)
            if score > 0 or not query_words:
                if not filter_verdict or t.get("verdict","") == filter_verdict:
                    matches.append((score, t))
        matches.sort(key=lambda x: -x[0])

        if matches:
            lines = [f"Found {len(matches)} matching tender(s):"]
            for score, t in matches[:10]:
                tid   = t.get("t247_id","?")
                brief = t.get("brief","")[:50]
                v     = t.get("verdict","?")
                dl    = t.get("deadline","")[:10] if t.get("deadline") else "—"
                cost  = t.get("estimated_cost_cr","")
                lines.append(f"  T247-{tid} | {v} | {brief} | Due: {dl}" + (f" | Rs.{cost}Cr" if cost else ""))
            response = "\n".join(lines)
        else:
            response = f"No tenders found matching: {', '.join(query_words) if query_words else '(all)'}"
        return _offline_return(response, history, user_message)

    # ── 5. SPECIFIC TENDER LOOKUP ─────────────────────────────────
    m_tid = re.search(r"t247[-\s]?(\d{4,8})|tender\s+(\d{4,8})", msg, re.IGNORECASE)
    if m_tid:
        tid = m_tid.group(1) or m_tid.group(2)
        t = db.get("tenders",{}).get(tid)
        if t:
            lines = [
                f"T247-{tid}: {t.get('brief','')[:80]}",
                f"Org: {t.get('org_name','')}",
                f"Verdict: {t.get('verdict','?')} | Status: {t.get('status','?')}",
                f"Deadline: {t.get('deadline','')} | Est. Cost: Rs.{t.get('estimated_cost_cr','?')}Cr",
                f"EMD: {t.get('emd','')} | Analysis: {'Done' if t.get('bid_no_bid_done') else 'Pending'}",
            ]
            if t.get("notes"): lines.append(f"Notes: {t.get('notes','')[:200]}")
            response = "\n".join(lines)
        else:
            response = f"Tender T247-{tid} not found in database ({len(tenders)} total)."
        return _offline_return(response, history, user_message)

    # ── 6. COMPANY PROFILE QUERIES ───────────────────────────────
    if any(x in msg for x in ["cmmi", "cmmi level", "cmmi status", "cmmi certificate"]):
        cmmi = certs.get("cmmi", {})
        response = (
            f"CMMI Status: {cmmi.get('version','V2.0 (DEV)')} Level {cmmi.get('level',3)}\n"
            f"Benchmark ID: {cmmi.get('benchmark_id','68617')}\n"
            f"Valid: {cmmi.get('valid_from','14-Dec-2023')} to {cmmi.get('valid_to','19-Dec-2026')}\n"
            f"Issuer: {cmmi.get('issuer','CUNIX Infotech Pvt. Ltd.')}\n"
            f"Status: {cmmi.get('status','ACTIVE')}"
        )
        return _offline_return(response, history, user_message)

    if any(x in msg for x in ["iso", "iso 9001", "iso 27001", "iso 20000", "certification", "cert status"]):
        iso9  = certs.get("iso_9001", {})
        iso27 = certs.get("iso_27001", {})
        iso20 = certs.get("iso_20000", {})
        cmmi  = certs.get("cmmi", {})
        response = (
            f"Certification Status (all ACTIVE):\n"
            f"  CMMI V2.0 Level 3 — valid till {cmmi.get('valid_to','19-Dec-2026')}\n"
            f"  ISO 9001:2015 (Cert: {iso9.get('cert_no','25EQPE64')}) — till {iso9.get('valid_to','08-Sep-2028')}\n"
            f"  ISO/IEC 27001:2022 (Cert: {iso27.get('cert_no','25EQPG58')}) — till {iso27.get('valid_to','08-Sep-2028')}\n"
            f"  ISO/IEC 20000-1:2018 (Cert: {iso20.get('cert_no','25ZQZQ030409IT')}) — till {iso20.get('valid_to','08-Sep-2028')}\n"
            f"  MSME / Udyam: {company.get('udyam','UDYAM-GJ-01-0007420')} — Lifetime\n"
            f"  GSTIN: {company.get('gstin','24AACCN3670J1ZG')} — Active\n"
            f"  OGC CityLayers 2.0 — Active\n"
            f"NOT HELD: CERT-In, STQC, SAP Partner, Oracle Partner, Esri Partner"
        )
        return _offline_return(response, history, user_message)

    if any(x in msg for x in ["turnover", "revenue", "financial", "our finance", "avg turnover"]):
        by_yr = finance.get("turnover_by_year", {})
        lines = ["Turnover by year:"]
        for fy, val in sorted(by_yr.items()):
            lines.append(f"  {fy}: Rs. {val} Cr")
        lines.append(f"Avg 2-yr: Rs. {finance.get('avg_turnover_last_2_fy',17.60)} Cr")
        lines.append(f"Avg 3-yr: Rs. {finance.get('avg_turnover_last_3_fy',17.18)} Cr")
        lines.append(f"Avg 5-yr: Rs. {finance.get('avg_turnover_last_5_fy',16.23)} Cr")
        lines.append(f"Net Worth: Rs. {finance.get('net_worth_cr',26.09)} Cr")
        lines.append(f"CA: {finance.get('ca_name','Anuj J. Sharedalal')}")
        response = "\n".join(lines)
        return _offline_return(response, history, user_message)

    if any(x in msg for x in ["employee", "staff", "team", "headcount", "manpower", "how many people"]):
        emp = employees
        response = (
            f"Employee Strength: {emp.get('total_confirmed',67)} total (payroll)\n"
            f"  GIS Specialists: {emp.get('gis_staff',11)}\n"
            f"  IT/Software Developers: {emp.get('it_dev_staff',21)}\n"
            f"  Others: QA, PM, BA, Data, Admin, Management\n"
            f"Note: Employee Strength Certificate available as per tender annexure format."
        )
        return _offline_return(response, history, user_message)

    if any(x in msg for x in ["project", "our project", "past project", "experience", "portfolio"]):
        lines = [f"Project Portfolio ({len(projects)} projects):"]
        for p in projects:
            status = p.get("status","?")
            role   = p.get("role","Solo")
            lines.append(
                f"  {p.get('name','')} | {p.get('client','')} | Rs.{p.get('value_cr',0)}Cr | {status} | {role}"
            )
        total = sum(p.get("value_cr",0) for p in projects)
        solo  = [p for p in projects if p.get("role","Solo") == "Solo"]
        largest = sorted(projects, key=lambda x: x.get("value_cr",0), reverse=True)
        response = "\n".join(lines)
        response += f"\nTotal portfolio: Rs. {total:.2f} Cr | Solo projects: {len(solo)}"
        response += f"\nLargest: {largest[0].get('name','')} (Rs. {largest[0].get('value_cr',0)} Cr)" if largest else ""
        return _offline_return(response, history, user_message)

    # ── 7. POA / COMPLIANCE ───────────────────────────────────────
    if any(x in msg for x in ["poa", "power of attorney", "signatory", "hitesh patel", "compliance"]):
        poa_alert = company.get("poa_alert","POA expires 31-Mar-2026")
        response = (
            f"Power of Attorney: Hitesh Patel (Chief Administrative Officer)\n"
            f"Validity: {company.get('poa_validity','01/04/2025 - 31/03/2026')}\n"
            f"STATUS: {poa_alert}\n"
            f"Action required: Renew POA before signing any bid document."
        )
        return _offline_return(response, history, user_message)

    # ── 8. TECH STACK ─────────────────────────────────────────────
    if any(x in msg for x in ["technology", "tech stack", "what tech", "our technology", "gis software", "what can we"]):
        tech = profile.get("technology", {})
        response = (
            f"Nascent Technology Stack:\n"
            f"  GIS: {', '.join(tech.get('gis',[]))}\n"
            f"  Backend: {', '.join(tech.get('backend',[]))}\n"
            f"  Frontend: {', '.join(tech.get('frontend',[]))}\n"
            f"  Mobile: {', '.join(tech.get('mobile',[]))}\n"
            f"  Database: {', '.join(tech.get('database',[]))}\n"
            f"  Cloud: {', '.join(tech.get('cloud',[]))}\n"
            f"NOT PRIMARY (verify): {', '.join(tech.get('not_primary',[]))}"
        )
        return _offline_return(response, history, user_message)

    # ── 9. BID RULES ──────────────────────────────────────────────
    if any(x in msg for x in ["bid rule", "do not bid", "dnb rule", "preferred sector", "conditional rule", "no bid rule"]):
        dnb   = rules.get("do_not_bid",[])
        cond  = rules.get("conditional",[])
        pref  = rules.get("preferred_sectors",[])
        response = (
            f"Bid Rules:\n"
            f"DO NOT BID ({len(dnb)} rules): {', '.join(dnb[:20])}{'...' if len(dnb)>20 else ''}\n\n"
            f"CONDITIONAL ({len(cond)} rules): {', '.join(cond)}\n\n"
            f"PREFERRED ({len(pref)} sectors): {', '.join(pref[:20])}{'...' if len(pref)>20 else ''}"
        )
        return _offline_return(response, history, user_message)

    # ── 10. WIN RATE ──────────────────────────────────────────────
    if any(x in msg for x in ["win rate", "won how many", "how many won", "success rate", "won tenders"]):
        won   = [t for t in tenders if t.get("outcome") == "Won"]
        lost  = [t for t in tenders if t.get("outcome") == "Lost"]
        sub   = [t for t in tenders if t.get("outcome") == "Submitted"]
        total_decided = len(won) + len(lost)
        win_pct = f"{len(won)/total_decided*100:.0f}%" if total_decided > 0 else "N/A"
        response = (
            f"Bid Outcomes:\n"
            f"  Won: {len(won)} tenders\n"
            f"  Lost: {len(lost)} tenders\n"
            f"  Submitted (awaiting): {len(sub)}\n"
            f"  Win rate: {win_pct} (of decided bids)\n"
        )
        if won:
            top_won = sorted(won, key=lambda x: float(x.get("awarded_value_cr",0) or 0), reverse=True)
            response += f"  Largest win: T247-{top_won[0].get('t247_id','?')} | {top_won[0].get('brief','')[:40]}"
        return _offline_return(response, history, user_message)

    # ── 11. ACTIONS: ADD RULE ─────────────────────────────────────
    # "add rule: no hardware supply" / "don't bid on hardware supply" / "we don't bid on X"
    m_rule = re.search(
        r"(?:add\s+rule[:\s]+|don.t\s+bid\s+on\s+|we\s+don.t\s+bid\s+on\s+|"
        r"never\s+bid\s+on\s+|skip\s+tenders?\s+(?:with|for|about)\s+|"
        r"do\s+not\s+bid\s+on\s+)(.+)",
        msg, re.IGNORECASE
    )
    if m_rule:
        keyword = m_rule.group(1).strip().rstrip(".,!").lower()
        if len(keyword) > 2:
            action = {"action":"update_rule","rule_type":"do_not_bid","keyword":keyword,"remark":"Added via chat"}
            result = execute_action(action)
            response = f"Done. '{keyword}' added to Do-Not-Bid rules.\n{result}"
            return _offline_return(response, history, user_message)

    # "add preferred sector: tourism portal"
    m_pref = re.search(
        r"(?:add\s+preferred(?:\s+sector)?[:\s]+|we\s+bid\s+on\s+|prefer\s+tenders?\s+(?:for|about)\s+)(.+)",
        msg, re.IGNORECASE
    )
    if m_pref:
        keyword = m_pref.group(1).strip().rstrip(".,!").lower()
        if len(keyword) > 2:
            action = {"action":"update_rule","rule_type":"preferred","keyword":keyword,"remark":"Added via chat"}
            result = execute_action(action)
            response = f"Done. '{keyword}' added to Preferred Sectors.\n{result}"
            return _offline_return(response, history, user_message)

    # ── 12. ACTIONS: UPDATE STAGE ─────────────────────────────────
    m_stage = re.search(
        r"(?:move|update|set|change)\s+t247[-\s]?(\d{4,8})\s+to\s+(.+)",
        msg, re.IGNORECASE
    )
    if m_stage:
        tid   = m_stage.group(1)
        stage = m_stage.group(2).strip().rstrip(".,!")
        action = {"action":"update_stage","t247_id":tid,"stage":stage}
        result = execute_action(action)
        response = f"Done. T247-{tid} status updated.\n{result}" if result else f"Tender T247-{tid} not found."
        return _offline_return(response, history, user_message)

    # ── 13. ACTIONS: WE WON / WE LOST ────────────────────────────
    m_outcome = re.search(
        r"(?:we\s+)?(?:won|lost|submitted)\s+(?:t247[-\s]?)?(\d{4,8})",
        msg, re.IGNORECASE
    )
    if m_outcome:
        tid = m_outcome.group(1)
        outcome = "Won" if "won" in msg else ("Lost" if "lost" in msg else "Submitted")
        # Extract LOI date if present
        loi_m = re.search(r"loi\s+(?:date\s+)?(\d{1,2}[-/]\w{3,}[-/]\d{2,4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})", msg, re.I)
        loi_date = loi_m.group(1) if loi_m else ""
        val_m = re.search(r"(?:rs\.?\s*|value\s+)(\d+(?:\.\d+)?)\s*(?:cr|crore)", msg, re.I)
        val = val_m.group(1) if val_m else ""
        action = {"action":"update_tender_outcome","t247_id":tid,"outcome":outcome,"loi_date":loi_date,"contract_value":val}
        result = execute_action(action)
        suffix = " Remember: renew POA before signing." if outcome == "Won" else ""
        response = f"Recorded. T247-{tid} marked as {outcome}.{suffix}\n{result}"
        return _offline_return(response, history, user_message)

    # ── 14. ACTIONS: MARK BID DECISION ───────────────────────────
    m_dec = re.search(
        r"(?:mark|set|make)\s+t247[-\s]?(\d{4,8})\s+as\s+(bid|no.?bid|conditional)",
        msg, re.IGNORECASE
    )
    if m_dec:
        tid     = m_dec.group(1)
        dec_raw = m_dec.group(2).upper().replace(" ","")
        decision = "BID" if dec_raw == "BID" else ("NO-BID" if "NOBID" in dec_raw else "CONDITIONAL")
        action = {"action":"mark_bid_decision","t247_id":tid,"decision":decision,"reason":"Set via offline chat"}
        result = execute_action(action)
        response = f"T247-{tid} verdict set to {decision}.\n{result}"
        return _offline_return(response, history, user_message)

    # ── 15. ACTIONS: ADD NOTE ─────────────────────────────────────
    m_note = re.search(
        r"(?:add\s+note\s+(?:to\s+)?|note\s+for\s+)t247[-\s]?(\d{4,8})[:\s]+(.+)",
        msg, re.IGNORECASE
    )
    if m_note:
        tid  = m_note.group(1)
        note = m_note.group(2).strip()
        action = {"action":"update_tender_note","t247_id":tid,"note":note}
        result = execute_action(action)
        response = f"Note added to T247-{tid}.\n{result}"
        return _offline_return(response, history, user_message)

    # ── 16. SHOW BID / NO-BID LIST ────────────────────────────────
    if any(x in msg for x in ["show bid", "list bid", "all bid", "bid tender", "show no-bid", "list no-bid"]):
        filter_v = "BID" if "no-bid" not in msg and "no bid" not in msg else "NO-BID"
        if "conditional" in msg: filter_v = "CONDITIONAL"
        subset = [t for t in tenders if t.get("verdict","") == filter_v]
        if subset:
            lines = [f"{filter_v} Tenders ({len(subset)}):"]
            for t in sorted(subset, key=lambda x: x.get("deadline",""), reverse=False)[:15]:
                dl   = (t.get("deadline","") or t.get("bid_submission_date","") or "")[:10]
                cost = t.get("estimated_cost_cr","")
                lines.append(
                    f"  T247-{t.get('t247_id','?')} | {t.get('brief','')[:45]} | Due:{dl}" +
                    (f" | Rs.{cost}Cr" if cost else "")
                )
            response = "\n".join(lines)
        else:
            response = f"No {filter_v} tenders found."
        return _offline_return(response, history, user_message)

    # ── 17. COMPANY INFO ──────────────────────────────────────────
    if any(x in msg for x in ["company info", "our company", "nascent info", "who are we", "about us", "company profile"]):
        co = company
        response = (
            f"{co.get('name','Nascent Info Technologies Pvt. Ltd.')}\n"
            f"CIN: {co.get('cin','U72200GJ2006PTC048723')}\n"
            f"PAN: {co.get('pan','AACCN3670J')} | GSTIN: {co.get('gstin','24AACCN3670J1ZG')}\n"
            f"Udyam/MSME: {co.get('udyam','UDYAM-GJ-01-0007420')}\n"
            f"Type: {co.get('type','IT / GIS / Smart City Solutions Provider')}\n"
            f"Incorporated: {co.get('year_of_incorporation',2006)} | Years: {co.get('years_in_operation',19)}\n"
            f"Address: {co.get('address','A-805, Shapath IV, SG Highway, Ahmedabad 380015')}\n"
            f"MD: {co.get('md','Maulik Bhagat')} | CAO: Hitesh Patel\n"
            f"Email: {co.get('tender_email','nascent.tender@nascentinfo.com')}"
        )
        return _offline_return(response, history, user_message)

    # ── 18. SIMILAR WORK MATCHING ─────────────────────────────────
    if any(x in msg for x in ["similar work", "match", "relevant project", "qualifying project", "which project"]):
        # Extract domain from message
        domain_kws = {
            "gis": ["gis","geospatial","geographic","mapping","survey"],
            "smart city": ["smart city","ulb","municipal","nagar"],
            "egov": ["egovernance","e-governance","portal","citizen"],
            "mobile": ["mobile app","android","ios","app"],
            "erp": ["erp","enterprise resource","property tax"],
            "tourism": ["tourism"],
        }
        matched_domain = None
        for dom, kws in domain_kws.items():
            if any(kw in msg for kw in kws):
                matched_domain = dom
                break

        if matched_domain:
            relevant = [
                p for p in projects
                if any(kw in " ".join(p.get("tags",[])).lower() + p.get("name","").lower() + p.get("client","").lower()
                       for kw in domain_kws.get(matched_domain,[matched_domain]))
            ]
        else:
            relevant = projects  # show all if no specific domain

        if relevant:
            lines = [f"Relevant projects for '{matched_domain or 'all'}':"]
            for p in relevant:
                lines.append(
                    f"  {p.get('name','')} | {p.get('client','')} | Rs.{p.get('value_cr',0)}Cr | "
                    f"{p.get('status','')} | {p.get('role','Solo')}"
                )
            response = "\n".join(lines)
        else:
            response = "No matching projects found in profile. Update nascent_profile.json projects section."
        return _offline_return(response, history, user_message)

    # ── 19. FALLBACK ──────────────────────────────────────────────
    # Try to give a useful partial answer for anything left
    # Check if it's a question about a topic we know
    topic_hints = {
        "emd": "EMD/Earnest Money: Nascent is MSME (UDYAM-GJ-01-0007420) — eligible for EMD exemption under PPP-MSME Order 2012. Always raise pre-bid query if EMD > 0.",
        "msme": f"Nascent MSME: {company.get('udyam','UDYAM-GJ-01-0007420')} | Lifetime validity | Eligible for EMD exemption, purchase preference, 50% turnover relaxation.",
        "blacklist": "Nascent is not blacklisted by any Govt dept, PSU, or statutory body. Self-declaration affidavit available.",
        "solvency": f"Net worth: Rs. {finance.get('net_worth_cr',26.09)} Cr | Solvency certificate obtainable from banker (SBI, SG Highway Branch).",
        "consortium": "Consortium/JV: Nascent can be Lead or Member. Check if tender allows consortium. Use Consortium module to register JV partners.",
        "gem": "GeM portal: Nascent can participate on GeM. GePNIC/eProcure/CPPP also supported.",
        "arcgis": "ArcGIS: Nascent uses ArcGIS but is NOT an Esri authorized partner. For Esri-partner requirement, this is a disqualifier.",
        "net worth": f"Net worth: Rs. {finance.get('net_worth_cr',26.09)} Cr | CA: {finance.get('ca_name','Anuj J. Sharedalal')}",
    }
    for kw, hint in topic_hints.items():
        if kw in msg:
            return _offline_return(hint, history, user_message)

    response = (
        "Offline mode — limited responses without API key.\n"
        "I can answer: pipeline stats, deadlines, company profile, certifications, turnover, projects, rules.\n"
        "I can do: update tender stage, add bid rules, mark won/lost, add notes.\n"
        "Type 'help' for full command list, or add Gemini API key in Settings for full AI conversation."
    )
    return _offline_return(response, history, user_message)


def _offline_return(response: str, history: List[Dict], user_message: str) -> Dict:
    """Helper to build standard return dict for offline chat."""
    history.append({"role":"user","content":user_message})
    history.append({"role":"assistant","content":response})
    save_history(history)
    return {
        "response":      response,
        "action":        None,
        "action_result": None,
        "all_actions":   [],
        "history":       history,
        "offline_mode":  True,
    }


def process_message(user_message: str, history: List[Dict]) -> Dict:
    """Process user message. Return response + any action results."""
    config = load_config()
    api_key = config.get("gemini_api_key","")
    if not api_key:
        # Full offline rule-based chatbot — no API needed
        return _offline_chat(user_message, history)

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
