"""
A window that contributes nothing must say why.

Three separate bugs this session were invisible in exactly this way: a
window refused by the rate limiter, a window whose response would not parse,
and a window whose response carried the right content in the wrong shape all
produced the same thing - zero entries and no log line - and the only
symptom anyone could see was a short FAQ list.
"""

import json
import logging
import uuid

import pytest

from app.services.faq_generation import _extract_pairs, _generate_for_window


def test_a_response_that_is_not_json_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        assert _extract_pairs("I'm sorry, I can't help with that.") == []

    assert any("could not parse" in r.getMessage() for r in caplog.records)


def test_a_response_in_the_wrong_shape_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    An object wrapping the list is the model's most common near-miss, and it
    reads as "no questions" without this.
    """

    wrapped = json.dumps({"faqs": [{"question": "Q?", "answer": "A."}]})

    with caplog.at_level(logging.WARNING):
        assert _extract_pairs(wrapped) == []

    assert any("where a list of pairs" in r.getMessage() for r in caplog.records)


def test_a_good_response_is_not_reported(caplog: pytest.LogCaptureFixture) -> None:
    """
    The cost of a warning is that it must mean something. A window that
    worked has nothing to report.
    """

    good = json.dumps([{"question": "Q?", "answer": "A."}])

    with caplog.at_level(logging.WARNING):
        assert _extract_pairs(good) == [("Q?", "A.")]

    assert caplog.records == []


def test_a_malformed_entry_is_dropped_without_losing_the_rest() -> None:
    """
    CLAUDE.md's malformed-model-output rule: one bad entry costs itself, not
    the batch.
    """

    mixed = json.dumps(
        [
            {"question": "Kept?", "answer": "Yes."},
            {"question": "", "answer": "No question."},
            {"question": "No answer?"},
            "not an object",
            {"question": "Also kept?", "answer": "Yes."},
        ]
    )

    assert _extract_pairs(mixed) == [("Kept?", "Yes."), ("Also kept?", "Yes.")]


async def test_a_window_lost_to_rate_limiting_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    The failure that started all of this: windows abandoned to 429s looked
    exactly like windows with nothing worth asking.
    """

    from app.providers.llm import LLMRateLimited

    class _AlwaysLimited:
        async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
            raise LLMRateLimited("429", retry_after_seconds=0.0)

    with caplog.at_level(logging.WARNING):
        pairs = await _generate_for_window(
            _AlwaysLimited(),
            "some document text",
            already_asked=[],
            knowledge_source_id=uuid.uuid4(),
        )

    assert pairs == []
    assert any("rate-limited attempts" in r.getMessage() for r in caplog.records)
