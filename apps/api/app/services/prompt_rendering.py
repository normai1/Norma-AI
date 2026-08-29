"""
Pure prompt-template rendering: substitutes `{{namespace.field}}`
placeholders against a context dict. No database, no I/O, no async - usable
in a fast unit test and, later, directly inside the realtime turn loop
without an I/O detour.
"""

import re
from typing import Any

from app.core.exceptions import PromptRenderError

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\.(\w+)\s*\}\}")


def render_prompt(content: str, context: dict[str, dict[str, Any]]) -> str:
    """
    Replace every `{{namespace.field}}` placeholder in `content` with
    `context[namespace][field]`.

    A `None` value is a legitimate, not-yet-known value (for example a
    caller's name before it is known) and renders as an empty string. A
    namespace or field the context does not define at all raises
    `PromptRenderError` - that is an authoring bug in the template, not
    something to silently blank out.
    """

    def substitute(match: re.Match[str]) -> str:
        namespace, field = match.group(1), match.group(2)

        if namespace not in context:
            raise PromptRenderError(
                f"Unknown namespace '{namespace}' in prompt template"
            )

        fields = context[namespace]

        if field not in fields:
            raise PromptRenderError(
                f"Unknown field '{field}' in namespace '{namespace}'"
                " in prompt template",
            )

        value = fields[field]

        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(substitute, content)
