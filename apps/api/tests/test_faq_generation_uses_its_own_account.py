"""
FAQ generation must not spend the token allowance a live call answers from.

Groq's rate limits are per account: 8,000 tokens a minute and 200,000 a day
for this model. Generation and the realtime call loop were sharing them, and
processing a document costs about 5,700 tokens a minute - most of what a
call needs to reply at all. Callers heard "Sorry, I'm having trouble
responding right now" because somebody had uploaded a PDF, which CLAUDE.md
section 21 forbids outright: a caller must never experience a limit event.
"""

import pytest

from app.core.config import settings
from app.providers.factory import (
    MissingGroqApiKeyError,
    get_faq_generation_llm_provider,
)


def test_generation_uses_the_separate_account_key_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "groq_api_key", "the-live-call-account")
    monkeypatch.setattr(settings, "groq_api_key_secret", "the-background-account")

    provider = get_faq_generation_llm_provider("groq")

    assert provider._api_key == "the-background-account"


def test_one_account_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The fallback matters: a deployment with a single key must keep working
    exactly as it did, sharing the limits as it always has.
    """

    monkeypatch.setattr(settings, "groq_api_key", "the-only-account")
    monkeypatch.setattr(settings, "groq_api_key_secret", "")

    provider = get_faq_generation_llm_provider("groq")

    assert provider._api_key == "the-only-account"


def test_neither_key_fails_at_construction_not_mid_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A misconfigured deploy must say so before it has half-written a
    document's FAQs, not after.
    """

    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key_secret", "")

    with pytest.raises(MissingGroqApiKeyError) as caught:
        get_faq_generation_llm_provider("groq")

    # Names both, so whoever reads it knows there is a choice.
    assert "GROQ_API_KEY_SECRET" in str(caught.value)
    assert "GROQ_API_KEY" in str(caught.value)


def test_the_live_call_path_is_untouched_by_the_background_key() -> None:
    """
    apps/voice reads GROQ_API_KEY from its own environment and knows nothing
    about the second account - which is the whole point. This asserts the
    separation is structural rather than a convention someone has to
    remember.
    """

    from pathlib import Path

    voice_config = Path(__file__).resolve().parents[3] / "apps/voice/app/config.py"
    source = voice_config.read_text(encoding="utf-8")

    assert "GROQ_API_KEY" in source
    assert "GROQ_API_KEY_SECRET" not in source
