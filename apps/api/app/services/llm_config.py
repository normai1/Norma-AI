"""
Resolves the two pieces of assistant configuration item 20d's realtime turn
loop needs at session setup: the system prompt (rendered from the
assistant's own custom_prompt, falling back to its persona) and creativity
(bounded temperature, item 11b) - both read directly off the one mutable
Assistant row. A live call is never the place for a prompt-authoring bug
(PromptRenderError) to drop the call - CLAUDE.md's "silence is the worst
possible failure" - so rendering failures fail open to persona, then to a
fixed default, rather than raising.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound, PromptRenderError
from app.models.assistant import Assistant
from app.repositories import assistant as assistant_repo
from app.repositories import workspace as workspace_repo
from app.services.prompt_rendering import render_prompt

DEFAULT_SYSTEM_PROMPT = (
    "You are Norma, an AI phone assistant answering calls for this business. "
    "Be warm, professional, and efficient - sound like an experienced "
    "front-desk person having a real conversation, not a chatbot reading a "
    "script.\n\n"
    "Keep every response to 1-2 sentences. Never use bullet points or "
    "numbered lists, even for multi-part answers - if something needs more "
    "detail than that, offer to have someone follow up instead of listing "
    "it all out loud.\n\n"
    "If you do not have specific information about this business - its "
    "services, hours, pricing, policies, or anything else - do not attempt "
    "to describe or summarize it in any way, generic or specific, and never "
    "write a placeholder in brackets. Simply say you don't have those "
    "details in front of you right now, and offer to take a message or "
    "connect the caller with someone who does. Only state something as fact "
    "if it was actually given to you in this conversation or in this "
    "business's own knowledge base.\n\n"
    "Never claim an action has happened (booked, sent, confirmed, "
    "cancelled) unless you have explicit confirmation it actually "
    "succeeded.\n\n"
    "Ask one question at a time, and listen for what the caller actually "
    "needs rather than assuming. If the caller interrupts you, stop and "
    "address what they just said.\n\n"
    "Stay calm and respectful if the caller is frustrated. If you can't "
    "resolve something, offer to connect them with a person.\n\n"
    "When the caller's request is handled, ask if there's anything else you "
    "can help with, and end the call naturally once they say no."
)


@dataclass(frozen=True)
class LLMConfig:
    system_prompt: str
    creativity: float


async def resolve_llm_config(db: AsyncSession, assistant_id: uuid.UUID) -> LLMConfig:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise AssistantNotFound

    system_prompt = await _resolve_system_prompt(db, assistant)

    return LLMConfig(system_prompt=system_prompt, creativity=assistant.creativity)


async def _resolve_system_prompt(db: AsyncSession, assistant: Assistant) -> str:
    if assistant.custom_prompt:
        workspace = await workspace_repo.get_by_id(db, assistant.workspace_id)

        try:
            return render_prompt(
                assistant.custom_prompt,
                {
                    "workspace": {"name": workspace.name},
                    "assistant": {"name": assistant.name},
                    "caller": {"name": None},
                },
            )
        except PromptRenderError:
            pass

    return assistant.persona or DEFAULT_SYSTEM_PROMPT
