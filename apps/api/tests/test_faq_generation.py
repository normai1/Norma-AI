import json

from app.services.faq_generation import (
    MAX_ENTRIES_PER_WINDOW,
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


def test_extract_pairs_caps_one_window_at_the_per_window_count() -> None:
    """
    Per window, not per document: the document is covered window by
    window, so the whole-source ceiling is MAX_GENERATED_ENTRIES.
    """

    pairs = [{"question": f"Q{i}?", "answer": f"A{i}."} for i in range(50)]
    raw = json.dumps(pairs)

    result = _extract_pairs(raw)

    assert len(result) == MAX_ENTRIES_PER_WINDOW
    assert result[0] == ("Q0?", "A0.")


def test_strip_code_fence_removes_a_json_fence() -> None:
    raw = "```json\n[]\n```"

    assert _strip_code_fence(raw) == "[]"


def test_strip_code_fence_removes_a_plain_fence() -> None:
    raw = "```\n[]\n```"

    assert _strip_code_fence(raw) == "[]"


def test_strip_code_fence_leaves_unfenced_text_unchanged() -> None:
    assert _strip_code_fence("[]") == "[]"


def test_a_long_document_is_covered_in_windows_not_just_its_opening() -> None:
    """
    The reported bug: a 50-page PDF of ~143,000 characters produced 8 FAQ
    entries, because generation only ever saw text[:12_000] - its first 8%.
    """

    from app.services.faq_generation import MAX_SOURCE_TEXT_CHARS, _windows

    text = "\n\n".join(f"Paragraph {i}. " + "word " * 200 for i in range(150))

    windows = _windows(text)

    assert len(windows) > 1
    assert all(len(w) <= MAX_SOURCE_TEXT_CHARS for w in windows)
    # The end of the document is reached, not just the beginning.
    assert "Paragraph 149" in windows[-1]


def test_windowing_is_bounded_so_a_huge_document_cannot_run_away() -> None:
    from app.services.faq_generation import MAX_SOURCE_WINDOWS, _windows

    text = "\n\n".join(f"Paragraph {i}. " + "word " * 200 for i in range(5000))

    assert len(_windows(text)) == MAX_SOURCE_WINDOWS


def test_a_short_document_is_still_a_single_window() -> None:
    from app.services.faq_generation import _windows

    assert _windows("We open at nine and close at five.") == [
        "We open at nine and close at five."
    ]


def test_empty_text_produces_no_windows() -> None:
    from app.services.faq_generation import _windows

    assert _windows("   \n\n  ") == []


def test_the_same_question_from_two_windows_is_only_kept_once() -> None:
    """
    A document that mentions opening hours in three places gets asked about
    them three times; a FAQ list repeating itself is worse than a shorter one.
    """

    from app.services.faq_generation import _deduplicate

    merged = _deduplicate(
        [
            [("What are your hours?", "9-5"), ("Where are you?", "Main St")],
            [("what are your HOURS??", "9 to 5"), ("Do you validate parking?", "Yes")],
        ]
    )

    assert [q for q, _ in merged] == [
        "What are your hours?",
        "Where are you?",
        "Do you validate parking?",
    ]


def test_the_overall_entry_ceiling_is_respected() -> None:
    from app.services.faq_generation import _deduplicate

    groups = [
        [(f"Question {g}-{i}?", "Answer") for i in range(50)] for g in range(10)
    ]

    assert len(_deduplicate(groups)) == MAX_GENERATED_ENTRIES
