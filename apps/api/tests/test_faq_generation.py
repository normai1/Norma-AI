import json

from app.services.faq_generation import (
    MAX_GENERATED_ENTRIES,
    _extract_pairs,
    _strip_code_fence,
)


def test_extract_pairs_parses_a_valid_json_array() -> None:
    raw = '[{"question": "When are you open?", "answer": "9am to 5pm."}]'

    assert _extract_pairs(raw) == [("When are you open?", "9am to 5pm.")]


def test_extract_pairs_returns_empty_list_for_empty_array() -> None:
    assert _extract_pairs("[]") == []


def test_extract_pairs_returns_empty_list_for_invalid_json() -> None:
    assert _extract_pairs("not json at all") == []


def test_extract_pairs_returns_empty_list_when_top_level_is_not_a_list() -> None:
    assert _extract_pairs('{"question": "x", "answer": "y"}') == []


def test_extract_pairs_drops_entries_missing_a_question_or_answer() -> None:
    raw = (
        '[{"question": "Valid?", "answer": "Yes."}, '
        '{"question": "Missing answer"}, '
        '{"answer": "Missing question"}]'
    )

    assert _extract_pairs(raw) == [("Valid?", "Yes.")]


def test_extract_pairs_drops_entries_with_non_string_or_blank_values() -> None:
    raw = (
        '[{"question": "Valid?", "answer": "Yes."}, '
        '{"question": 123, "answer": "Not a string question"}, '
        '{"question": "  ", "answer": "Blank question"}]'
    )

    assert _extract_pairs(raw) == [("Valid?", "Yes.")]


def test_extract_pairs_caps_at_the_max_entry_count() -> None:
    pairs = [{"question": f"Q{i}?", "answer": f"A{i}."} for i in range(50)]
    raw = json.dumps(pairs)

    result = _extract_pairs(raw)

    assert len(result) == MAX_GENERATED_ENTRIES
    assert result[0] == ("Q0?", "A0.")


def test_strip_code_fence_removes_a_json_fence() -> None:
    raw = "```json\n[]\n```"

    assert _strip_code_fence(raw) == "[]"


def test_strip_code_fence_removes_a_plain_fence() -> None:
    raw = "```\n[]\n```"

    assert _strip_code_fence(raw) == "[]"


def test_strip_code_fence_leaves_unfenced_text_unchanged() -> None:
    assert _strip_code_fence("[]") == "[]"
