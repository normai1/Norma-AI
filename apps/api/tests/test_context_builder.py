import uuid

from app.services.context_builder import build_context
from app.services.retrieval import RetrievedChunk


def _chunk(text: str, *, score: float = 1.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        knowledge_source_id=uuid.uuid4(),
        source_type="file",
        text=text,
        metadata={},
        score=score,
    )


def test_empty_input_returns_empty_string() -> None:
    assert build_context([]) == ""


def test_single_chunk_returns_its_text() -> None:
    assert build_context([_chunk("Our hours are 9am to 5pm.")]) == (
        "Our hours are 9am to 5pm."
    )


def test_multiple_chunks_under_budget_are_joined_in_order() -> None:
    chunks = [_chunk("First fact."), _chunk("Second fact."), _chunk("Third fact.")]

    assert build_context(chunks) == "First fact.\n\nSecond fact.\n\nThird fact."


def test_a_chunk_that_would_exceed_the_budget_is_dropped_whole() -> None:
    chunks = [_chunk("A" * 30), _chunk("B" * 30)]

    result = build_context(chunks, max_chars=40)

    # The first chunk fits complete; the second would push the total over
    # budget and is dropped entirely, never truncated.
    assert result == "A" * 30
    assert "B" not in result


def test_later_chunks_are_never_considered_once_one_does_not_fit() -> None:
    chunks = [_chunk("A" * 30), _chunk("B" * 30), _chunk("C")]

    result = build_context(chunks, max_chars=40)

    assert result == "A" * 30
    assert "C" not in result
