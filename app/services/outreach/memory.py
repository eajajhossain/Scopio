"""Agent memory — what the AI remembers about each lead, across turns and conversations.

Four kinds of memory, mapped to how cognitive agents are built:

- WORKING memory    — the live conversation: the transcript window the LLM sees, plus a
                      structured scratchpad (`conversation.memory`) of facts learned in THIS
                      conversation. Because the transcript sent to the LLM is capped (last 10
                      turns), the scratchpad is how facts from early in a long thread survive.
- EPISODIC memory   — past conversations with the same business (when, on what channel, how
                      it ended, what we learned) — recalled whenever we talk to them again,
                      so the agent never re-introduces itself cold to a lead it already knows.
- SEMANTIC memory   — durable facts about the lead, promoted to `business.details["known_facts"]`
                      (owner's name, objections, preferences, current setup), alongside the
                      deep-research profile enrichment already stored in `details`.
- PROCEDURAL memory — how to sell: the playbook system prompt (see playbook.py).

`recall()` builds one text brief from all of these for the agent's system prompt;
`remember()` persists the `new_facts` the LLM reports after each reply.
"""
import logging

from sqlalchemy import select

from app.models.business import Business
from app.models.conversation import Conversation

logger = logging.getLogger(__name__)

# Caps keep the brief small (prompt tokens) and stop unbounded JSONB growth.
MAX_FACTS = 30          # per store (conversation scratchpad / business known_facts)
MAX_EPISODES = 3        # past conversations summarized
MAX_EPISODE_FACTS = 5   # facts quoted per past conversation
MAX_NEW_FACTS = 10      # facts accepted from a single LLM turn


def _conv_facts(conv: Conversation) -> list[str]:
    return [f for f in (conv.memory or {}).get("facts", []) if isinstance(f, str)]


def _dedup_append(existing: list[str], new: list[str], cap: int) -> list[str]:
    """Append new facts, skipping (case-insensitive) duplicates; keep the newest `cap`."""
    seen = {f.strip().lower() for f in existing}
    out = list(existing)
    for f in new:
        key = f.strip().lower()
        if key and key not in seen:
            out.append(f.strip())
            seen.add(key)
    return out[-cap:]


async def recall(session, biz: Business, conv: Conversation | None = None) -> str:
    """Build the memory brief injected into the agent's system prompt.

    Combines semantic facts about the lead, episodic summaries of previous
    conversations, and the current conversation's working-memory scratchpad.
    Returns "" when there is nothing remembered yet (first cold contact).
    """
    lines: list[str] = []

    # SEMANTIC — durable facts about this lead, learned in any past interaction.
    known = [f for f in ((biz.details or {}).get("known_facts") or []) if isinstance(f, str)]
    if known:
        lines.append("Facts you already know about this lead (from earlier interactions):")
        lines += [f"- {f}" for f in known[-MAX_FACTS:]]

    # EPISODIC — previous conversations with this business (most recent first).
    q = select(Conversation).where(Conversation.business_id == biz.id)
    if conv is not None:
        q = q.where(Conversation.id != conv.id)
    past = (
        (await session.execute(q.order_by(Conversation.created_at.desc()).limit(MAX_EPISODES)))
        .scalars().all()
    )
    if past:
        lines.append("Previous conversations with this business:")
        for p in past:
            when = p.created_at.date().isoformat() if p.created_at else "earlier"
            turns = len(p.transcript or [])
            lines.append(f"- {when} via {p.channel}: {turns} messages, ended '{p.status}'")
            lines += [f"  • {f}" for f in _conv_facts(p)[-MAX_EPISODE_FACTS:]]

    # WORKING — this conversation's scratchpad (facts that may have scrolled out of
    # the capped transcript window).
    if conv is not None:
        facts = _conv_facts(conv)
        if facts:
            lines.append("Notes from this conversation so far:")
            lines += [f"- {f}" for f in facts[-MAX_FACTS:]]

    return "\n".join(lines)


def remember(conv: Conversation | None, biz: Business, new_facts: list | None) -> None:
    """Persist facts the agent learned this turn.

    Writes to the conversation's working-memory scratchpad AND promotes them to the
    lead's semantic memory (`business.details.known_facts`) so future conversations
    start already knowing them. Mutates the ORM objects; does NOT commit.
    """
    facts = [f.strip() for f in (new_facts or [])
             if isinstance(f, str) and f.strip()][:MAX_NEW_FACTS]
    if not facts:
        return

    if conv is not None:
        mem = dict(conv.memory or {})
        mem["facts"] = _dedup_append(_conv_facts(conv), facts, MAX_FACTS)
        conv.memory = mem  # reassign so SQLAlchemy tracks the JSONB change

    details = dict(biz.details or {})
    existing = [f for f in (details.get("known_facts") or []) if isinstance(f, str)]
    details["known_facts"] = _dedup_append(existing, facts, MAX_FACTS)
    biz.details = details
