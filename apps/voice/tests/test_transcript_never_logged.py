"""
Item 24d step 4: the rule that transcript text never reaches the logs, made
into something the suite enforces rather than something a reviewer remembers.

CLAUDE.md section 27 states it plainly - transcript text in application logs
is "a data-protection incident waiting to happen". Every turn-path log site
in media_session.py already follows the rule, recording word counts and
assistant IDs instead of utterances. This test is what stops the next one
being written wrong: it runs a real turn through the real pipeline and fails
if a single distinctive word of either side's speech shows up in the log.

Verified to actually catch a leak, not pass vacuously: adding
`logger.info("%s", text)` to LLMTurnProcessor's transcript handler makes
test_caller_speech_never_reaches_the_logs fail with that line quoted back.
"""

import logging
import re

import pytest
from norma_shared.mock_speech import MockSTT, MockTTS
from norma_shared.speech import TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState

from app.mock_llm import MockLLM
from tests.conversation_harness import (
    open_conversation_session,
    receive_until,
    send_audio_chunks,
)

# Deliberately distinctive, so a match in the log output cannot be a
# coincidence from some unrelated message.
_CALLER_SAYS = "My name is Wilhelmina Bracegirdle and my postcode is Quaxton."
_ASSISTANT_SAYS = "Certainly Wilhelmina, your Quaxton appointment is confirmed."

# Words common enough to appear in a log line for innocent reasons are not
# evidence of a leak; these are not.
_TELLTALE = ("Wilhelmina", "Bracegirdle", "Quaxton", "postcode")


def _run_one_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drives a full caller-speaks/assistant-replies turn through the pipeline."""

    with open_conversation_session(
        monkeypatch,
        mock_stt=MockSTT(
            script=[TranscriptEvent(text=_CALLER_SAYS, is_final=True)],
            chunks_before_event=[1],
        ),
        mock_llm=MockLLM(response=_ASSISTANT_SAYS),
        mock_tts=MockTTS(),
        vad_states=[VADState.SPEAKING, VADState.QUIET, VADState.QUIET],
    ) as ws:
        send_audio_chunks(ws, 3)
        receive_until(ws, stop_types={"llm_complete"})


def test_caller_speech_never_reaches_the_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    The caller's own words, and the assistant's reply, must be absent from
    everything the app logs during a turn - at DEBUG, which is stricter than
    anything production runs at.
    """

    with caplog.at_level(logging.DEBUG):
        _run_one_turn(monkeypatch)

    logged = "\n".join(record.getMessage() for record in caplog.records)

    for word in _TELLTALE:
        assert word not in logged, (
            f"{word!r} from the conversation reached the application log. "
            "Log the call id and a word count and look the transcript up "
            "through authorized access instead (CLAUDE.md section 27). "
            f"Offending output:\n{logged}"
        )


def test_the_turn_was_actually_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Guards the test above from passing for the wrong reason.

    A turn that logged nothing at all would satisfy "no transcript in the
    logs" while telling us nothing, and would also mean the per-turn
    diagnostics CLAUDE.md section 27 asks for had quietly stopped working.
    """

    with caplog.at_level(logging.DEBUG):
        _run_one_turn(monkeypatch)

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert logged.strip(), "the turn produced no log output at all"
    # The shape the turn path is supposed to log in: counts and identifiers.
    assert re.search(r"words=\d+|frames=\d+|turn|stt", logged, re.IGNORECASE)


def test_a_client_event_is_logged_by_its_known_fields_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    The browser telemetry channel accepts arbitrary JSON from the client.
    It must log the fields it knows and drop everything else, rather than
    echoing whatever arrived.
    """

    import json
    import time

    with (
        caplog.at_level(logging.DEBUG),
        open_conversation_session(monkeypatch, mock_tts=MockTTS()) as ws,
    ):
        ws.send_text(
            json.dumps(
                {
                    "source": "client",
                    "event": "flush",
                    "reason": "caller_speech_started",
                    "queued": 3,
                    "note": _CALLER_SAYS,
                }
            )
        )
        send_audio_chunks(ws, 1)

        # The serializer handles that frame on the pipeline's own task, so the
        # log line is not written by the time send_text returns. Asserting
        # straight away passes or fails depending on scheduling - which is
        # exactly how this test first went flaky.
        deadline = time.monotonic() + 5.0

        while time.monotonic() < deadline:
            if any("client event" in r.getMessage() for r in caplog.records):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the client event was never logged")

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert "event='flush'" in logged
    assert "reason='caller_speech_started'" in logged
    # The unrecognised field is dropped, and its arrival is still visible.
    assert "Wilhelmina" not in logged
    assert "unrecognised field" in logged
