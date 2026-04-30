# Project briefing assembler.
#
# A "context restore" endpoint that gives the user a quick status snapshot
# when they return to a project after a gap.  Combines:
#   - Open action items from transcript extraction
#   - Recent decisions from transcripts
#   - Active risks from transcripts
#   - Top-3 RAG chunks for "project current status" query
#
# The LLM then generates a 2-4 sentence summary that threads these together.
# Designed to complete in ≤10 seconds on local hardware.

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("uvicorn.error")

MAX_ACTIONS = 10
MAX_DECISIONS = 5
MAX_RISKS = 10
RAG_K = 3


@dataclass
class BriefAction:
    id: str
    text: str
    owner: str | None
    due_date: str | None
    status: str
    source: str


@dataclass
class BriefDecision:
    id: str
    text: str
    source: str
    created_at: str


@dataclass
class BriefRisk:
    id: str
    text: str
    source: str
    created_at: str


@dataclass
class Briefing:
    summary: str
    open_actions: list[BriefAction]
    recent_decisions: list[BriefDecision]
    active_risks: list[BriefRisk]
    generated_at: str


BRIEFING_SYSTEM_PROMPT = (
    "You are a project assistant summarising the current status of a project. "
    "Given structured data about open action items, recent decisions, active risks, "
    "and relevant document chunks, produce a 2-4 sentence plain-English summary. "
    "Do not list items — weave them into a coherent narrative. "
    "If there is no data, say 'No content yet for this project.'.\n\n"
    "Output ONLY the summary text, no markdown, no preamble."
)


async def assemble_briefing(
    project_id: str,
    transcript_store: Any,
    vector_store: Any,
    chat_fn: Any,
) -> Briefing:
    """Assemble the four-part briefing for a project.

    Runs four queries in parallel where possible, then calls the LLM
    to generate the summary text.
    """
    now = datetime.now(timezone.utc).isoformat()

    open_actions = await transcript_store.list_action_items(project_id, status="open")
    open_actions = open_actions[:MAX_ACTIONS]

    decisions = await transcript_store.list_decisions(project_id)
    recent_decisions = decisions[:MAX_DECISIONS]

    risks = await transcript_store.list_risks(project_id)
    active_risks = risks[:MAX_RISKS]

    rag_hits = []
    try:
        vec = await vector_store.embed("project current status")
        hits = await vector_store.search(
            collection="documents",
            query_vector=vec,
            k=RAG_K,
            project_id=project_id,
        )
        rag_hits = [
            {"source": h.payload.get("source"), "text": h.payload.get("text")}
            for h in hits
            if h.payload
        ]
    except Exception as e:
        logger.warning(f"RAG search failed during briefing: {e}")

    summary = await _generate_summary(
        open_actions, recent_decisions, active_risks, rag_hits, chat_fn
    )

    return Briefing(
        summary=summary,
        open_actions=[
            BriefAction(
                id=a.id,
                text=a.text,
                owner=a.owner,
                due_date=a.due_date,
                status=a.status,
                source=a.source,
            )
            for a in open_actions
        ],
        recent_decisions=[
            BriefDecision(
                id=d.id, text=d.text, source=d.source, created_at=d.created_at
            )
            for d in recent_decisions
        ],
        active_risks=[
            BriefRisk(id=r.id, text=r.text, source=r.source, created_at=r.created_at)
            for r in active_risks
        ],
        generated_at=now,
    )


async def _generate_summary(
    actions: list[Any],
    decisions: list[Any],
    risks: list[Any],
    rag_hits: list[dict],
    chat_fn: Any,
) -> str:
    """Call the LLM to generate a summary from the collected data."""
    has_content = actions or decisions or risks or rag_hits

    if not has_content:
        return "No content yet for this project."

    parts = []

    if actions:
        action_list = "; ".join(
            f"{a.text}" + (f" (due {a.due_date})" if a.due_date else "")
            for a in actions[:5]
        )
        parts.append(f"Open actions: {action_list}.")

    if decisions:
        decision_list = "; ".join(d.text for d in decisions[:3])
        parts.append(f"Recent decisions: {decision_list}.")

    if risks:
        risk_list = "; ".join(r.text for r in risks[:3])
        parts.append(f"Active risks: {risk_list}.")

    if rag_hits:
        doc_context = " ".join(h["text"][:150] for h in rag_hits[:2])
        parts.append(f"Recent documents mention: {doc_context}")

    user_prompt = (
        f"Briefing data:\n---\n" + "\n".join(parts) + "\n---\n"
        "Provide a 2-4 sentence summary of this project's current status."
    )

    messages = [
        {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        summary = await chat_fn(messages)
        return summary.strip()
    except Exception as e:
        logger.warning(f"LLM summary generation failed: {e}")
        return f"Briefing generated from {len(actions)} actions, {len(decisions)} decisions, {len(risks)} risks."
