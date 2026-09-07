"""
The assistant must be heard, not read.

The regression these cover was reported from a real test call: asked about
pricing, the assistant answered with a markdown table and the caller heard the
pipes, the separator row of hyphens, and the bold asterisks read out loud.

The negative cases matter as much as the positive ones. This runs on every
sentence before the caller hears it, so a rule that eats a date, a price, or a
hyphenated word costs the caller the answer itself - a worse outcome than the
stray symbol it was meant to remove.
"""

import pytest

from app.spoken_text import to_spoken_text

# The reply from the reported call, trimmed to the part that broke.
_REPORTED_REPLY = """Sure! Renate offers four subscription tiers:

| Plan | Included interviews per month | Typical seat count* |
|------|------------------------------|----------------------|
| **Lite** | 50 | 1-2 seats |
| **Standard** | 300 | 3-5 seats |

All paid plans renew automatically each billing cycle unless you cancel."""


def test_the_reported_pricing_table_is_spoken_as_words() -> None:
    spoken = to_spoken_text(_REPORTED_REPLY)

    # Nothing a caller would hear as a symbol.
    for symbol in ("|", "*", "#", "`", "---"):
        assert symbol not in spoken, f"{symbol!r} survived: {spoken!r}"

    # Every word the caller actually needs is still there.
    for word in ("Renate", "Plan", "Included interviews per month", "Lite", "50",
                 "Standard", "300", "renew automatically"):
        assert word in spoken, f"{word!r} was lost: {spoken!r}"

    # Rows read as rows, not as one run-on breath.
    assert "Lite, 50" in spoken


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("**Lite** costs less.", "Lite costs less."),
        ("We offer *three* plans.", "We offer three plans."),
        ("Our __best__ value.", "Our best value."),
        ("The _Pro_ plan.", "The Pro plan."),
        ("## Pricing", "Pricing"),
        ("- Lite\n- Standard", "Lite. Standard"),
        ("* Lite\n* Standard", "Lite. Standard"),
        ("Use `book_appointment` for that.", "Use book_appointment for that."),
        ("See [our pricing page](https://example.com/pricing).", "See our pricing page."),
        ("> We are closed Sundays.", "We are closed Sundays."),
        ("| Lite | 50 |", "Lite, 50"),
    ],
)
def test_markdown_syntax_becomes_speech(written: str, expected: str) -> None:
    assert to_spoken_text(written) == expected


@pytest.mark.parametrize(
    "written",
    [
        # Anything an operator's caller actually needs to hear, unchanged.
        "That comes to $45.00 including tax.",
        "We open at 9:30 and close at 5.",
        "Your appointment is on 2026-09-07.",
        "Ask for the follow-up appointment.",
        "It is a state-of-the-art clinic.",
        "The reference is ABC-123.",
        "We charge 2.5% on card payments.",
        "Call us on 555-123-4567.",
    ],
)
def test_ordinary_speech_is_left_exactly_alone(written: str) -> None:
    assert to_spoken_text(written) == written


def test_a_typed_range_dash_becomes_the_word_to() -> None:
    """
    "1-2 seats" read aloud as "one dash two" is the complaint in miniature.
    Only the typed en/em dash converts - see the date case above for why the
    ASCII hyphen must not.
    """

    assert to_spoken_text("Typical seat count 1–2.") == "Typical seat count 1 to 2."
    assert to_spoken_text("Open 9–5 on weekdays.") == "Open 9 to 5 on weekdays."


def test_a_spaced_dash_becomes_a_pause() -> None:
    assert to_spoken_text("Lite - 50 interviews.") == "Lite, 50 interviews."


def test_it_is_pure_and_total() -> None:
    """
    This sits in front of the TTS provider on every sentence. A failure here
    would cost the caller the reply, so it must never raise on any input.
    """

    for value in ("", "   ", "|||", "***", "---", "#", "`", "[]()", "**", "> "):
        result = to_spoken_text(value)
        assert isinstance(result, str)

    assert to_spoken_text("") == ""
    # Running it twice changes nothing further.
    once = to_spoken_text(_REPORTED_REPLY)
    assert to_spoken_text(once) == once


def test_a_markdown_reply_reaches_the_caller_as_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    End to end through the real pipeline: the model answers with a markdown
    table, and what the caller is sent contains no markup at all.

    The unit tests above prove the rewriting; this proves it is actually
    wired into the turn path, on the same frame the TTS provider consumes.
    """

    from norma_shared.mock_speech import MockSTT, MockTTS
    from norma_shared.speech import TranscriptEvent
    from pipecat.audio.vad.vad_analyzer import VADState

    from app.mock_llm import MockLLM
    from tests.conversation_harness import (
        open_conversation_session,
        receive_until,
        send_audio_chunks,
    )

    with open_conversation_session(
        monkeypatch,
        mock_stt=MockSTT(
            script=[TranscriptEvent(text="What plans do you offer?", is_final=True)],
            chunks_before_event=[1],
        ),
        mock_llm=MockLLM(response=_REPORTED_REPLY),
        mock_tts=MockTTS(),
        vad_states=[VADState.SPEAKING, VADState.QUIET, VADState.QUIET],
    ) as ws:
        send_audio_chunks(ws, 3)
        trace = receive_until(ws, stop_types={"llm_complete"})

    spoken = [
        payload["text"]
        for kind, payload in trace
        if kind == "text" and payload.get("type") in ("llm_delta", "llm_complete")
    ]

    assert spoken, f"the turn produced no reply: {trace}"

    for text in spoken:
        for symbol in ("|", "*", "#", "`"):
            assert symbol not in text, f"{symbol!r} would have been spoken: {text!r}"

    # The answer itself survived the cleanup.
    assert any("Lite" in text and "50" in text for text in spoken)
