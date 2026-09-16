"""
AI Chat Agent Service (§5) — Gemini-backed conversational agent.

Handles:
- Blood group parsing (typos, Bangla, informal forms)
- Relative date parsing
- Multi-turn slot filling via conversation_history
- MCP tool dispatch
"""

import json
import logging
import re
from datetime import datetime, date, timedelta
from typing import Optional, List, Tuple

from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import business_today
from app.db.models import ConversationHistory, ConversationRole, User
from app.mcp_tools.tools import dispatch_tool, TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

# ── Blood group normalisation ────────────────────────────

BLOOD_GROUP_MAP = {
    # English variants
    "a positive": "A+", "a pos": "A+", "a+": "A+", "a +": "A+",
    "a negative": "A-", "a neg": "A-", "a-": "A-", "a -": "A-",
    "b positive": "B+", "b pos": "B+", "b+": "B+", "b +": "B+",
    "b negative": "B-", "b neg": "B-", "b-": "B-", "b -": "B-",
    "ab positive": "AB+", "ab pos": "AB+", "ab+": "AB+", "ab +": "AB+",
    "ab negative": "AB-", "ab neg": "AB-", "ab-": "AB-", "ab -": "AB-",
    "o positive": "O+", "o pos": "O+", "o+": "O+", "o +": "O+",
    "o negative": "O-", "o neg": "O-", "o-": "O-", "o -": "O-",
    # Common typos
    "ove positive": "O+", "ove pos": "O+",
    "ove negative": "O-", "ove neg": "O-",
    # Bangla variants
    "এ পজিটিভ": "A+", "এ নেগেটিভ": "A-",
    "বি পজিটিভ": "B+", "বি নেগেটিভ": "B-",
    "এবি পজিটিভ": "AB+", "এবি নেগেটিভ": "AB-",
    "ও পজিটিভ": "O+", "ও নেগেটিভ": "O-",
    "এ পসিটিভ": "A+", "বি পসিটিভ": "B+",
    "এবি পসিটিভ": "AB+", "ও পসিটিভ": "O+",
}


def normalize_blood_group(text: str) -> Optional[str]:
    """Parse a blood group from text, handling typos, Bangla, and informal forms."""
    text_lower = text.strip().lower()
    # Direct match
    if text_lower in BLOOD_GROUP_MAP:
        return BLOOD_GROUP_MAP[text_lower]
    # Search within text
    for key, value in sorted(BLOOD_GROUP_MAP.items(), key=lambda x: -len(x[0])):
        if key in text_lower:
            return value
    # Regex for patterns like "O+", "AB-"
    match = re.search(r'\b(A|B|AB|O)\s*([+-])\b', text, re.IGNORECASE)
    if match:
        return f"{match.group(1).upper()}{match.group(2)}"
    return None


# ── Date parsing ─────────────────────────────────────────

def parse_relative_date(text: str) -> Optional[str]:
    """Parse relative dates like 'today', 'tomorrow', 'next Monday'."""
    text_lower = text.strip().lower()
    today = business_today()

    if "today" in text_lower or "আজ" in text_lower:
        return today.isoformat()
    # Most specific first: "day after tomorrow" contains "tomorrow", so checking
    # the shorter phrase earlier would match it and answer one day early — a
    # request dated wrong is one donors judge the urgency of wrong.
    if "day after tomorrow" in text_lower or "পরশু" in text_lower:
        return (today + timedelta(days=2)).isoformat()
    if "tomorrow" in text_lower or "আগামীকাল" in text_lower:
        return (today + timedelta(days=1)).isoformat()

    # "next Monday", "next Tuesday", etc.
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(days):
        if day in text_lower:
            current_day = today.weekday()
            days_ahead = i - current_day
            if days_ahead <= 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).isoformat()

    # "this evening", "tonight" → today
    if any(w in text_lower for w in ["evening", "tonight", "this morning", "this afternoon"]):
        return today.isoformat()

    # Try ISO format
    match = re.search(r'\d{4}-\d{2}-\d{2}', text)
    if match:
        return match.group()

    return None


# ── System prompt ────────────────────────────────────────

SYSTEM_PROMPT = """You are a helpful Blood Donation Assistant for Bangladesh. You help users:
1. Find blood donors nearby
2. Create blood requests
3. Check donation eligibility
4. View donation history
5. Manage blood requests

You have access to these tools:
{tools}

RULES:
- NEVER make up donor data. Every donor must come from tool results.
- If no donors are found, say so plainly.
- NEVER invent names, phone numbers, hospitals, or locations.
- If information is missing, ask a follow-up question.
- Parse blood groups from various formats (O+, O positive, ও পজিটিভ, etc.)
- Parse relative dates (today, tomorrow, next Monday, etc.)
- Support both English and Bangla.
- Be empathetic and helpful — blood donation saves lives.

When you need to call a tool, respond with a JSON block:
```tool_call
{{"tool": "ToolName", "args": {{...}}}}
```

After receiving tool results, summarize them naturally for the user.
"""


# ── Chat agent ───────────────────────────────────────────


async def process_chat_message(
    session: Session,
    user: User,
    message: str,
) -> str:
    """
    Process a chat message, maintaining conversation history.

    Uses Gemini for NL understanding if available; otherwise falls back
    to rule-based parsing.
    """
    # Save user message to conversation history
    user_msg = ConversationHistory(
        user_id=user.id,
        role=ConversationRole.USER.value,
        content=message,
    )
    session.add(user_msg)
    session.commit()

    # Try Gemini first
    if settings.gemini_api_key:
        response = await _gemini_chat(session, user, message)
    else:
        response = _rule_based_chat(session, user, message)

    # Save assistant response
    assistant_msg = ConversationHistory(
        user_id=user.id,
        role=ConversationRole.ASSISTANT.value,
        content=response,
    )
    session.add(assistant_msg)
    session.commit()

    return response


async def _gemini_chat(session: Session, user: User, message: str) -> str:
    """Use Gemini API for natural language understanding."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        model_name = "gemini-3.6-flash"

        # Build conversation context
        history = _get_recent_history(session, user.id, limit=10)
        tools_json = json.dumps(TOOL_DEFINITIONS, indent=2)
        system_instruction = SYSTEM_PROMPT.format(tools=tools_json)

        # Build messages as Content objects for the new SDK
        contents = []
        for msg in history:
            role = "user" if msg.role == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=msg.content)]))
        contents.append(types.Content(role="user", parts=[types.Part(text=message)]))

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
            ),
        )
        response_text = response.text

        # Check if response contains a tool call
        tool_call = _extract_tool_call(response_text)
        if tool_call:
            tool_name, tool_args = tool_call
            # Pre-process args
            if "blood_group" in tool_args:
                normalized = normalize_blood_group(tool_args["blood_group"])
                if normalized:
                    tool_args["blood_group"] = normalized
            if "needed_date" in tool_args:
                parsed = parse_relative_date(tool_args["needed_date"])
                if parsed:
                    tool_args["needed_date"] = parsed

            result = dispatch_tool(session, user, tool_name, tool_args)

            # Save tool result
            tool_msg = ConversationHistory(
                user_id=user.id,
                role=ConversationRole.TOOL.value,
                content=json.dumps(result, default=str),
                tool_name=tool_name,
                tool_payload=json.dumps(tool_args),
            )
            session.add(tool_msg)
            session.commit()

            # Ask Gemini to summarize the result
            summary_prompt = (
                f"Tool {tool_name} returned: {json.dumps(result, default=str)}\n\n"
                "Summarize this result naturally for the user. "
                "If it's donor data, present names, blood groups, and distances clearly. "
                "Never add information that isn't in the tool result."
            )
            contents.append(types.Content(role="model", parts=[types.Part(text=response_text)]))
            contents.append(types.Content(role="user", parts=[types.Part(text=summary_prompt)]))
            summary = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                ),
            )
            return summary.text

        return response_text

    except Exception as exc:
        logger.error("Gemini API error: %s", exc)
        return _rule_based_chat(session, user, message)


def _rule_based_chat(session: Session, user: User, message: str) -> str:
    """Fallback rule-based chat when Gemini is unavailable."""
    msg_lower = message.lower()

    # Blood group detection
    blood_group = normalize_blood_group(message)

    # Find donors intent
    if any(w in msg_lower for w in ["find donor", "need blood", "donor", "রক্ত দরকার", "ডোনার"]):
        if blood_group and user.latitude and user.longitude:
            result = dispatch_tool(session, user, "FindDonors", {
                "blood_group": blood_group,
                "latitude": user.latitude,
                "longitude": user.longitude,
            })
            if isinstance(result, list) and len(result) > 0:
                lines = [f"I found {len(result)} eligible donor(s) near you:\n"]
                for d in result[:5]:
                    lines.append(
                        f"- **{d['name']}** ({d['blood_group']}) — "
                        f"{d['distance_km']} km away"
                    )
                return "\n".join(lines)
            else:
                return f"Sorry, I couldn't find any {blood_group} donors near your location right now. Try expanding your search radius."
        elif blood_group:
            return "I can search for donors, but I need your location. Could you share your current location or tell me your district?"
        else:
            return "What blood group do you need? (e.g., O+, A-, B positive)"

    # Create request intent
    if any(w in msg_lower for w in ["create request", "emergency", "urgent", "request blood", "রিকুয়েস্ট"]):
        return (
            "I can help you create a blood request. Please provide:\n"
            "1. **Patient name**\n"
            "2. **Blood group needed**\n"
            "3. **Hospital name**\n"
            "4. **When do you need it?** (e.g., today, tomorrow)\n"
            "5. **Contact number**\n"
            "6. **How many units?**"
        )

    # Eligibility check
    if any(w in msg_lower for w in ["eligible", "can i donate", "eligibility", "যোগ্য"]):
        # No user_id argument: dispatch_tool injects the authenticated user's id
        # and logs a warning for any identity argument it has to strip. Passing
        # one here — even the correct one — would fire that warning on every
        # eligibility question and bury the real prompt-injection signal.
        result = dispatch_tool(session, user, "CheckEligibility", {})
        if isinstance(result, dict) and "is_eligible" in result:
            if result["is_eligible"]:
                return "You are **eligible** to donate blood! Thank you for being willing to help. 🩸"
            else:
                days = result.get("days_until_eligible", 0)
                return f"You are not yet eligible to donate. You'll be eligible in **{days} days**. The minimum gap between donations is 90 days."
        return "I couldn't check your eligibility. Please make sure your profile is complete."

    # History
    if any(w in msg_lower for w in ["history", "past donation", "ইতিহাস"]):
        result = dispatch_tool(session, user, "GetDonationHistory", {})
        if isinstance(result, list) and len(result) > 0:
            lines = [f"Your donation history ({len(result)} records):\n"]
            for d in result:
                lines.append(f"- {d['date']} — {d['blood_group']} at {d.get('hospital', 'N/A')}")
            return "\n".join(lines)
        return "You don't have any donation history yet."

    # Nearby requests
    if any(w in msg_lower for w in ["nearby request", "requests near", "কাছের রিকুয়েস্ট"]):
        if user.latitude and user.longitude:
            result = dispatch_tool(session, user, "GetNearbyRequests", {
                "latitude": user.latitude,
                "longitude": user.longitude,
            })
            if isinstance(result, list) and len(result) > 0:
                lines = [f"There are {len(result)} active request(s) near you:\n"]
                for r in result[:5]:
                    lines.append(
                        f"- **{r['blood_group']}** blood needed "
                        f"at {r['hospital_name']} ({r['distance_km']} km away)"
                    )
                return "\n".join(lines)
            return "There are no active blood requests near your location right now."
        return "I need your location to find nearby requests. Please share your location."

    # Profile
    if any(w in msg_lower for w in ["my profile", "profile", "প্রোফাইল"]):
        result = dispatch_tool(session, user, "GetUserProfile", {})
        if isinstance(result, dict) and "name" in result:
            return (
                f"**Your Profile:**\n"
                f"- Name: {result['name']}\n"
                f"- Blood Group: {result.get('blood_group', 'Not set')}\n"
                f"- Phone: {result.get('phone', 'Not set')}\n"
                f"- Location: {result.get('district', 'N/A')}, {result.get('division', 'N/A')}\n"
                f"- Available: {'Yes' if result.get('is_available') else 'No'}\n"
                f"- Last Donation: {result.get('last_donation_date', 'Never')}"
            )
        return "I couldn't retrieve your profile."

    # Default
    return (
        "I'm your Blood Donation Assistant! I can help you with:\n\n"
        "- **Find donors** — \"I need O+ blood near me\"\n"
        "- **Create a request** — \"Create an emergency blood request\"\n"
        "- **Check eligibility** — \"Am I eligible to donate?\"\n"
        "- **View history** — \"Show my donation history\"\n"
        "- **Nearby requests** — \"Are there any requests near me?\"\n\n"
        "How can I help you today?"
    )


def _get_recent_history(
    session: Session, user_id: int, limit: int = 10
) -> List[ConversationHistory]:
    stmt = (
        select(ConversationHistory)
        .where(ConversationHistory.user_id == user_id)
        .order_by(ConversationHistory.created_at.desc())
        .limit(limit)
    )
    results = session.exec(stmt).all()
    return list(reversed(results))


def _extract_tool_call(text: str) -> Optional[Tuple[str, dict]]:
    """Extract a tool call JSON from the response text."""
    # Look for ```tool_call ... ``` blocks
    match = re.search(r'```tool_call\s*\n?\s*(\{.*?\})\s*\n?\s*```', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return data.get("tool"), data.get("args", {})
        except json.JSONDecodeError:
            pass
    # Look for inline JSON
    match = re.search(r'\{"tool":\s*"(\w+)",\s*"args":\s*(\{.*?\})\}', text, re.DOTALL)
    if match:
        try:
            args = json.loads(match.group(2))
            return match.group(1), args
        except json.JSONDecodeError:
            pass
    return None


def get_chat_history(session: Session, user_id: int, limit: int = 50) -> List[dict]:
    """Get formatted conversation history."""
    history = _get_recent_history(session, user_id, limit=limit)
    return [
        {
            "role": msg.role,
            "content": msg.content,
            "tool_name": msg.tool_name,
            "created_at": str(msg.created_at),
        }
        for msg in history
    ]
