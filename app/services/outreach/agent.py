#The conversational sales agent.
import json
import logging

import httpx

from app.core import llm, telemetry
from app.services.outreach.playbook import SenderContext, fallback_opening, system_prompt

logger = logging.getLogger(__name__)

# Explicit, unambiguous agreement to a CALL — ONLY these grant permission to book.
# (A soft "ok"/"sure"/"interested" is NOT permission — we ask before scheduling.)
_CALL_AGREEMENT = (
    "call me", "give me a call", "you can call", "call would be", "sounds good",
    "sounds great", "let's do it", "lets do it", "let's talk", "lets talk",
    "let's schedule", "lets schedule", "let's set", "lets set", "book it",
    "book a call", "schedule a call", "schedule the call", "set up a call",
    "set up the call", "go ahead", "works for me", "that works", "yes please",
    "yes, let's", "yes let's", "yes, lets", "yes lets", "happy to chat",
    "happy to talk", "happy to hop on", "let's book",
)
# Soft interest / acknowledgement — keep the conversation going, but do NOT book
# a call off these alone; the reply asks the owner to confirm scheduling first.
_SOFT_POSITIVE = ("ok", "okay", "sure", "interested", "yes", "yeah", "yep", "alright", "fine")
_NEGATIVE = ("no thanks", "not interested", "stop", "don't", "do not", "remove", "unsubscribe", "busy")


def llm_available() -> bool:
    return llm.llm_available()


async def _chat(messages: list[dict], json_mode: bool, max_tokens: int = 400) -> str:
    """Chat via the shared cloud-LLM brain, on the outreach (fast) endpoint/model."""
    return await llm.chat(messages, json_mode=json_mode, max_tokens=max_tokens)


def _target_brief(business_info: dict) -> str:
    parts = [f'Target business: "{business_info.get("name", "")}"']
    if business_info.get("category"):
        parts.append(f'(type: {business_info["category"]})')
    if business_info.get("description"):
        parts.append(f'— about them: {business_info["description"]}')
    return " ".join(parts)


_TAILOR = (
    "FIRST think about what this kind of business actually does and what their customers "
    "need day to day. THEN write the message so it leads with the SPECIFIC, concrete "
    "benefits THIS business would get from our services (e.g. fewer missed enquiries, "
    "24/7 bookings, time saved at the front desk, more repeat customers). Make it clearly "
    "relevant to their line of work — never a generic pitch."
)


def _optout_line(channel: str) -> str:
    """A clear opt-out on the FIRST message — required for compliant cold outreach
    (CAN-SPAM in the US; stricter consent rules under GDPR/EU and in India) and it
    also protects sender reputation / deliverability."""
    if channel == "email":
        return "Not interested? Just reply STOP and I won't email you again."
    return "Reply STOP to opt out."


def with_optout(message: str, channel: str) -> str:
    """Append the opt-out line to a cold opening, unless the model already added one."""
    if "reply stop" in (message or "").lower():
        return message
    return f"{message.rstrip()}\n\n{_optout_line(channel)}"


async def generate_opening(
    business_info: dict, channel: str, ctx: SenderContext, memory_brief: str = ""
) -> str:
    if not llm_available():
        return with_optout(fallback_opening(business_info, channel, ctx), channel)
    hint = {
        "email": "This is an email — warm, 3–5 short sentences, no subject line in the body.",
        "whatsapp": "This is a WhatsApp message — short, friendly, an emoji or two is fine.",
        "sms": "This is an SMS — very short, under 320 characters.",
    }.get(channel, "Keep it short and friendly.")
    with telemetry.span(
        "outreach:opening", input={"business": business_info.get("name"), "channel": channel}
    ) as s:
        sys = system_prompt(ctx)
        if memory_brief:
            # EPISODIC/SEMANTIC recall: we've interacted with this lead before —
            # the opening should acknowledge history, not read like a cold intro.
            sys += (
                f"\n\nWhat you remember about this lead:\n{memory_brief}\n"
                "Use this — write a follow-up that reflects the history, not a cold opening."
            )
        try:
            content = await _chat(
                [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": (
                        f"{_target_brief(business_info)}\n\n"
                        f"Write the FIRST cold outreach message to them. {hint} "
                        f"Introduce yourself as {ctx.sender_name} from {ctx.company_name}. "
                        f"{_TAILOR} End by proposing a quick 15-minute call. Return ONLY the message text."
                    )},
                ],
                json_mode=False,
                max_tokens=350,
            )
            message = with_optout(content.strip(), channel)
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            logger.warning("opening generation failed: %s", exc)
            message = with_optout(fallback_opening(business_info, channel, ctx), channel)
        if s:
            s.update(output={"message": message})
        return message


_JSON_INSTR = (
    "\n\nReply with ONLY a JSON object: {\"reply\": <your next message to the owner, a string>, "
    "\"intent\": one of \"interested\" | \"question\" | \"not_interested\" | \"callback\", "
    "\"set_reminder\": boolean — set true ONLY when the owner has EXPLICITLY agreed to a call / "
    "given clear permission to be called (e.g. \"yes, let's do a call\", \"call me tomorrow\", "
    "\"sure, book it in\"). Do NOT set it true for general interest, curiosity, a question, or a "
    "soft \"ok\"/\"sure\" — in those cases keep intent \"interested\" or \"question\" and, in your "
    "reply, ASK the owner for permission to set up the call before booking. Never schedule a call "
    "the owner has not clearly agreed to, "
    "\"callback_days\": integer or null — how many days from now they want the call "
    "(\"tomorrow\"=1, \"in a couple days\"=2, \"next week\"=7); null if they didn't say, "
    "\"new_facts\": array of short strings — NEW concrete facts you learned about this business "
    "or owner from their latest message (their name, current setup, pain points, objections, "
    "timing/budget preferences). Facts only, no guesses; [] if none.}"
)


_PRICE_WORDS = ("price", "cost", "pricing", "charge", "fee", "how much", "rate", "budget", "expensive")
_PROOF_WORDS = ("experience", "worked with", "clients", "example", "case study", "portfolio",
                "references", "who else", "results", "proof", "trust")
_WHAT_WORDS = ("what do you", "what is", "what does", "how does", "how do you", "tell me", "explain",
               "details", "more info", "what can")


def _callback_days_from_text(text: str) -> int | None:
    """Best-effort timeframe from the owner's words (fallback, no LLM)."""
    low = text.lower()
    if "tomorrow" in low:
        return 1
    if "next week" in low:
        return 7
    if "today" in low or "now" in low:
        return 0
    if "couple" in low or "few days" in low:
        return 2
    return None


def _fallback_respond(transcript: list[dict]) -> dict:
    last = next((t["text"] for t in reversed(transcript) if t["role"] == "business"), "")
    low = last.lower()
    # Check negatives first ("not interested" contains "interested").
    if any(w in low for w in _NEGATIVE):
        return {"reply": "No problem at all — thank you for your time, and all the best!",
                "intent": "not_interested", "set_reminder": False, "callback_days": None,
                "new_facts": []}
    # Only an EXPLICIT agreement to a call gives permission to book the reminder.
    if any(w in low for w in _CALL_AGREEMENT):
        return {"reply": "Wonderful! I'll note that down and follow up with you then. Talk soon! 🎉",
                "intent": "callback", "set_reminder": True,
                "callback_days": _callback_days_from_text(last), "new_facts": []}
    # Soft interest / acknowledgement: stay warm, but ask permission before scheduling —
    # do NOT auto-book a call the owner hasn't clearly agreed to.
    if any(w in low for w in _SOFT_POSITIVE):
        return {"reply": ("Great to hear! Would it be okay if I set up a quick 15-minute call so I "
                          "can show you how it'd work? If so, would later today or tomorrow suit you?"),
                "intent": "interested", "set_reminder": False, "callback_days": None,
                "new_facts": []}
    if any(w in low for w in _PRICE_WORDS):
        reply = ("Totally fair to ask! Pricing depends on what you actually need, so we keep it "
                 "simple and tailored — I'll walk you through the options on a quick 15-min call, "
                 "no obligation. Would later today or tomorrow suit you?")
    elif any(w in low for w in _PROOF_WORDS):
        reply = ("Great question — yes, we've set this up for businesses in your space and I'd be "
                 "happy to share a couple of relevant examples and what changed for them. It lands "
                 "best on a short call — would a quick 15 minutes this week work?")
    elif any(w in low for w in _WHAT_WORDS):
        reply = ("Happy to explain! In short, we set up a 24/7 AI assistant that answers your "
                 "customers and books appointments automatically — no tech work on your side. The "
                 "easiest way to see if it fits is a quick 15-minute call. Would tomorrow work?")
    else:
        reply = ("Good point! I'd love to understand your setup a little and show you exactly how "
                 "this would help — could we grab a quick 15 minutes this week?")
    return {"reply": reply, "intent": "question", "set_reminder": False, "callback_days": None,
            "new_facts": []}


async def respond(
    business_info: dict, transcript: list[dict], channel: str, ctx: SenderContext,
    memory_brief: str = "",
) -> dict:
    if not llm_available():
        return _fallback_respond(transcript)
    sys = system_prompt(ctx) + f"\n\nYou are talking to: {_target_brief(business_info)}"
    if memory_brief:
        # Memory recall (working + episodic + semantic) — see outreach/memory.py.
        sys += f"\n\nWhat you remember about this lead:\n{memory_brief}"
    sys += _JSON_INSTR
    messages = [{"role": "system", "content": sys}]
    for turn in transcript[-10:]:   # cap history to keep token usage (and rate-limit risk) low
        role = "assistant" if turn["role"] == "assistant" else "user"
        messages.append({"role": role, "content": turn["text"]})
    with telemetry.span(
        "outreach:respond",
        input={"business": business_info.get("name"), "channel": channel,
               "turns": len(transcript)},
    ) as s:
        try:
            content = await _chat(messages, json_mode=True, max_tokens=400)
            data = json.loads(content)
            days = data.get("callback_days")
            raw_facts = data.get("new_facts")
            result = {
                "reply": (data.get("reply") or "").strip() or "Could you tell me a bit more?",
                "intent": data.get("intent") or "question",
                "set_reminder": bool(data.get("set_reminder")),
                "callback_days": days if isinstance(days, int) else None,
                "new_facts": [f for f in raw_facts if isinstance(f, str)]
                if isinstance(raw_facts, list) else [],
            }
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.warning("agent respond failed: %s", exc)
            result = _fallback_respond(transcript)
        if s:
            s.update(output=result)
        return result
