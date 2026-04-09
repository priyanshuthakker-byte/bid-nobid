"""
chatbot.py — Bid Assistant Chatbot
Answers questions about tenders using AI + tender database context.
THIS FILE WAS MISSING — /chat endpoint failed silently without it.
"""
import json, os
import logging
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

RUNTIME_DIR = Path(os.environ.get("BIDNOBID_RUNTIME_DIR", "/tmp/bid-nobid"))
OUTPUT_DIR = RUNTIME_DIR / "data"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
HISTORY_FILE = OUTPUT_DIR / "chat_history.json"
DB_FILE = OUTPUT_DIR / "tenders_db.json"


def load_history() -> List[Dict]:
    """Load chat history from disk."""
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_history(history: List[Dict]):
    """Save chat history to disk."""
    try:
        # Keep last 100 messages
        HISTORY_FILE.write_text(
            json.dumps(history[-100:], indent=2, default=str),
            encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"Could not save chat history: {e}")


def load_db() -> dict:
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tenders": {}}


def build_context(message: str) -> str:
    """
    Build context string from tender database relevant to the question.
    Injects tender summaries and company profile into the AI prompt.
    """
    db = load_db()
    tenders = list(db.get("tenders", {}).values())

    # Basic stats
    total = len(tenders)
    bid = sum(1 for t in tenders if t.get("verdict") == "BID")
    no_bid = sum(1 for t in tenders if t.get("verdict") == "NO-BID")
    conditional = sum(1 for t in tenders if t.get("verdict") == "CONDITIONAL")
    analysed = sum(1 for t in tenders if t.get("bid_no_bid_done"))

    # Find relevant tenders (keyword match)
    msg_lower = message.lower()
    relevant = []
    for t in tenders:
        brief = (t.get("brief") or "").lower()
        org = (t.get("org_name") or "").lower()
        tid = str(t.get("t247_id") or "")
        if any(word in brief or word in org or word in tid
               for word in msg_lower.split() if len(word) > 3):
            relevant.append(t)

    # Build tender summary
    tender_summary = f"TENDER DATABASE SUMMARY: Total={total}, BID={bid}, NO-BID={no_bid}, CONDITIONAL={conditional}, Analysed={analysed}\n"

    if relevant:
        tender_summary += "\nRELEVANT TENDERS:\n"
        for t in relevant[:5]:
            tender_summary += (
                f"- T247 ID: {t.get('t247_id')} | {t.get('brief','')[:60]} | "
                f"Org: {t.get('org_name','')[:40]} | "
                f"Value: Rs.{t.get('estimated_cost_cr','?')}Cr | "
                f"Deadline: {t.get('deadline','?')} | "
                f"Verdict: {t.get('verdict','?')} | "
                f"Status: {t.get('status','Identified')}\n"
            )
    elif tenders:
        # Show recent tenders
        recent = sorted(tenders, key=lambda x: x.get("imported_at", ""), reverse=True)[:5]
        tender_summary += "\nRECENT TENDERS:\n"
        for t in recent:
            tender_summary += (
                f"- T247 ID: {t.get('t247_id')} | {t.get('brief','')[:60]} | "
                f"Verdict: {t.get('verdict','?')} | "
                f"Deadline: {t.get('deadline','?')}\n"
            )

    return tender_summary


def process_message(message: str, history: List[Dict]) -> Dict[str, Any]:
    """
    Process a chat message and return AI response.
    Uses Gemini if configured, otherwise rule-based responses.
    """
    if not message or not message.strip():
        return {"response": "Please type a question.", "history": history}

    # Build context
    context = build_context(message)

    # Try AI response first
    response_text = None
    try:
        from ai_analyzer import get_all_api_keys, call_gemini, load_config, call_groq
        keys = get_all_api_keys()
        if keys:
            prompt = _build_chat_prompt(message, context, history)
            response_text = call_gemini(prompt, keys[0])
        else:
            # Try Groq
            config = load_config()
            groq_key = config.get("groq_api_key", "")
            if groq_key:
                prompt = _build_chat_prompt(message, context, history)
                response_text = call_groq(prompt, groq_key)
    except Exception as e:
        logger.warning(f"AI chat failed: {e}")
        response_text = None

    # Fallback to rule-based
    if not response_text:
        response_text = _rule_based_response(message, context)

    # Update history
    new_history = list(history)
    new_history.append({"role": "user", "content": message, "ts": datetime.now().isoformat()})
    new_history.append({"role": "assistant", "content": response_text, "ts": datetime.now().isoformat()})
    save_history(new_history)

    return {
        "response": response_text,
        "history": new_history[-20:],  # Return last 20 messages
    }


def _build_chat_prompt(message: str, context: str, history: List[Dict]) -> str:
    """Build the prompt for the AI chatbot."""
    # Build conversation history string
    history_str = ""
    for h in history[-6:]:  # Last 3 exchanges
        role = "User" if h.get("role") == "user" else "Assistant"
        history_str += f"{role}: {h.get('content','')[:200]}\n"

    return f"""You are a helpful bid management assistant for Nascent Info Technologies Pvt. Ltd., Ahmedabad.
You help the bid team with questions about tenders, eligibility, pre-bid queries, and bid strategy.

COMPANY: Nascent Info Technologies Pvt. Ltd.
- MSME | CMMI L3 V2.0 | ISO 9001, 27001, 20000
- Specializes in GIS, Smart City, eGov, IT solutions
- Average turnover ~Rs. 17 Cr | Net worth Rs. 26.09 Cr
- 67 employees (21 IT/Dev, 11 GIS)

{context}

CONVERSATION HISTORY:
{history_str}

User question: {message}

Answer concisely and helpfully. If asked about a specific tender, use the database context above.
If asked about eligibility, use the company profile. Keep response under 300 words."""


def _rule_based_response(message: str, context: str) -> str:
    """Rule-based fallback responses when AI is not available."""
    msg = message.lower().strip()

    if any(w in msg for w in ["how many", "count", "total", "tenders"]):
        return f"Here's your tender summary:\n\n{context}\n\nConfigure your Gemini API key in Settings for detailed AI-powered answers."

    if any(w in msg for w in ["bid", "no-bid", "conditional", "verdict"]):
        return (
            "Bid decisions are made in 3 layers:\n"
            "1. Excel Import: keyword matching against bid rules in your profile\n"
            "2. AI Analysis: Gemini reads the full RFP and gives a verdict\n"
            "3. Nascent Checker: fallback keyword check when AI is unavailable\n\n"
            "Configure your Gemini API key in Settings to enable AI analysis."
        )

    if any(w in msg for w in ["pre-bid", "prebid", "query", "queries"]):
        return (
            "Pre-bid queries are auto-generated after AI analysis of a tender.\n"
            "Go to Analyse Tender → upload the tender PDF → click Analyse.\n"
            "After analysis, pre-bid queries appear in the Pre-Bid Queries page."
        )

    if any(w in msg for w in ["profile", "company", "nascent", "turnover"]):
        return (
            "Nascent Info Technologies Pvt. Ltd.:\n"
            "- CMMI Level 3 V2.0 (valid Dec-2026)\n"
            "- ISO 9001, 27001, 20000 (valid Sep-2028)\n"
            "- Average turnover: Rs. 17.18 Cr (last 3 FY)\n"
            "- Net Worth: Rs. 26.09 Cr\n"
            "- 67 employees (21 IT/Dev, 11 GIS)\n"
            "- MSME: UDYAM-GJ-01-0007420"
        )

    if any(w in msg for w in ["help", "what can", "features", "how"]):
        return (
            "I can help you with:\n"
            "• Tender status and deadlines\n"
            "• Bid/No-Bid decisions and reasoning\n"
            "• Pre-bid query suggestions\n"
            "• Company eligibility checks\n"
            "• Document checklist guidance\n\n"
            "Configure Gemini API key in Settings for full AI assistance."
        )

    # Default
    return (
        f"I received your question: '{message}'\n\n"
        f"Current database snapshot:\n{context}\n\n"
        "For detailed AI-powered answers, please configure your Gemini API key in Settings → Gemini AI Keys."
    )
