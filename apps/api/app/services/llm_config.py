"""
Resolves the two pieces of assistant configuration item 20d's realtime turn
loop needs at session setup: the system prompt (rendered from the
assistant's assigned prompt template, item 12b, falling back to its persona)
and creativity (bounded temperature, item 11b). A live call is never the
place for a template-authoring bug (PromptRenderError) to drop the call -
CLAUDE.md's "silence is the worst possible failure" - so rendering failures
fail open to persona, then to a fixed default, rather than raising.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound, PromptRenderError
from app.models.assistant import Assistant
from app.models.assistant_version import AssistantVersion
from app.repositories import assistant as assistant_repo
from app.repositories import assistant_version as assistant_version_repo
from app.repositories import prompt_version as prompt_version_repo
from app.repositories import workspace as workspace_repo
from app.services.prompt_rendering import render_prompt

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful AI phone assistant. Answer briefly and clearly, "
    "and only state information you actually know."
)

# AssistantVersionCreate's own schema default (app/schemas/assistant_version.py)
# - what an assistant would have if it had never been published.
DEFAULT_CREATIVITY = 0.3


@dataclass(frozen=True)
class LLMConfig:
    system_prompt: str
    creativity: float


async def resolve_llm_config(db: AsyncSession, assistant_id: uuid.UUID) -> LLMConfig:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise AssistantNotFound

    if assistant.current_version_id is None:
        return LLMConfig(
            system_prompt=DEFAULT_SYSTEM_PROMPT, creativity=DEFAULT_CREATIVITY
        )

    version = await assistant_version_repo.get_by_id(db, assistant.current_version_id)

    system_prompt = await _resolve_system_prompt(db, assistant, version)

    return LLMConfig(system_prompt=system_prompt, creativity=version.creativity)


async def _resolve_system_prompt(
    db: AsyncSession, assistant: Assistant, version: AssistantVersion
) -> str:
    if version.prompt_template_id is not None and version.prompt_version is not None:
        prompt_version = await prompt_version_repo.get_by_version(
            db, version.prompt_template_id, version.prompt_version
        )

        if prompt_version is not None:
            workspace = await workspace_repo.get_by_id(db, assistant.workspace_id)

            try:
                return render_prompt(
                    prompt_version.content,
                    {
                        "workspace": {"name": workspace.name},
                        "assistant": {"name": assistant.name},
                        "caller": {"name": None},
                    },
                )
            except PromptRenderError:
                pass

    return version.persona or DEFAULT_SYSTEM_PROMPT
