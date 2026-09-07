"""
Deterministic text-generation provider mock. No network - exists so the
test suite never depends on a paid or live external API.
"""

from app.providers.llm import LLMProviderError


class MockLLMProvider:
    """
    Records every (system_prompt, user_prompt) pair passed to generate()
    and returns response - defaulting to an empty JSON array, since the
    primary caller (FAQ generation) treats "nothing worth generating" as a
    normal, common outcome. Both response and failure are public and
    freely reassignable after construction, so a test using the shared
    fixture instance can configure it per test. failure, if set, is raised
    instead of generating anything.
    """

    def __init__(
        self,
        *,
        response: str = "[]",
        failure: LLMProviderError | None = None,
    ) -> None:
        self.response = response
        self.failure = failure
        self.calls: list[tuple[str, str]] = []

    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))

        if self.failure is not None:
            raise self.failure

        return self.response
