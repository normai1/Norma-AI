"""
What the transcriber is allowed to hear.

Written against the two halves of the requirement, which pull in opposite
directions: catch every word the caller says, and send the room nothing.
A gate that satisfies only the second is the deafness this project has
already shipped twice.
"""

from app.speech_gate import SpeechGate

FRAME = b"\x01\x02" * 160  # 20ms at 16kHz, 16-bit mono
SILENCE = bytes(len(FRAME))


def _gate(*, pre_roll=0.2, hangover=0.4) -> SpeechGate:
    return SpeechGate(
        pre_roll_seconds=pre_roll, hangover_seconds=hangover, sample_rate=16000
    )


def test_the_room_reaches_the_transcriber_as_silence() -> None:
    """
    The cause of "it generates random words by himself": a transcriber given
    room noise finds words in it. Given silence it finds nothing.
    """

    gate = _gate()

    for _ in range(50):
        assert gate.feed(FRAME, speaking=False) == [SILENCE]

    assert not gate.is_open


def test_silence_is_sent_rather_than_nothing() -> None:
    """
    The provider commits on its own silence detection and takes its sense of
    time from the stream. Dropping frames would compress a two-second pause
    into an instant and change when it decides an utterance ended.
    """

    gate = _gate()
    sent = gate.feed(FRAME, speaking=False)

    assert len(sent) == 1
    assert len(sent[0]) == len(FRAME)
    assert set(sent[0]) == {0}


def test_the_callers_first_syllable_is_not_clipped() -> None:
    """
    The other half of the requirement. The detector confirms speech slightly
    after it starts, so gating strictly on its verdict loses the beginning of
    every sentence. The frames just before the verdict are held and released
    with it.
    """

    gate = _gate(pre_roll=0.2)  # ten frames

    for _ in range(10):
        gate.feed(FRAME, speaking=False)

    released = gate.feed(FRAME, speaking=True)

    # The ten held frames, then the frame that confirmed speech.
    assert len(released) == 11
    assert all(frame == FRAME for frame in released), "the pre-roll was not real audio"


def test_the_pre_roll_does_not_grow_without_bound() -> None:
    """
    A caller silent for five minutes must not hand the provider five minutes
    of backlog the moment they speak.
    """

    gate = _gate(pre_roll=0.2)

    for _ in range(1000):
        gate.feed(FRAME, speaking=False)

    assert len(gate.feed(FRAME, speaking=True)) == 11


def test_a_trailing_word_on_a_falling_voice_still_gets_through() -> None:
    """
    Speech keeps flowing for a moment after the detector goes quiet, so the
    end of a sentence is not cut off mid-word.
    """

    gate = _gate(hangover=0.4)  # twenty frames
    gate.feed(FRAME, speaking=True)

    for _ in range(19):
        assert gate.feed(FRAME, speaking=False) == [FRAME]

    # Past the hangover, it is the room again.
    assert gate.feed(FRAME, speaking=False) == [SILENCE]
    assert not gate.is_open


def test_a_pause_inside_a_sentence_does_not_close_the_gate() -> None:
    """
    People pause mid-sentence. A gate that shut on every one of them would
    chop the caller's speech into fragments, which is how a transcriber comes
    to emit one-word transcripts.
    """

    gate = _gate(hangover=0.4)
    gate.feed(FRAME, speaking=True)

    for _ in range(10):
        gate.feed(FRAME, speaking=False)

    assert gate.is_open
    assert gate.feed(FRAME, speaking=True) == [FRAME]


def test_the_gate_reopens_for_the_next_utterance() -> None:
    gate = _gate(pre_roll=0.2, hangover=0.04)

    gate.feed(FRAME, speaking=True)

    for _ in range(5):
        gate.feed(FRAME, speaking=False)

    assert not gate.is_open

    for _ in range(10):
        gate.feed(FRAME, speaking=False)

    assert len(gate.feed(FRAME, speaking=True)) == 11


async def test_the_processor_sends_the_room_as_silence_and_the_caller_as_audio(
    monkeypatch,
) -> None:
    """
    The gate wired into the real processor, which is what the pipeline tests
    deliberately opt out of - they count frames, and the gate changes the
    count.

    Two things have to hold together, and only one of them is obvious. The
    room must reach the provider as silence, or it invents words from it.
    And the caller must reach it intact, or this is the deafness that has
    already been shipped twice under a different name.
    """

    from app import config
    from app.media_session import SpeechToTextProcessor

    monkeypatch.setattr(config, "STT_GATE_ON_SPEECH", True)

    class _Detector:
        """Stands in for the shared TurnDetector's verdict."""

        def __init__(self) -> None:
            self.is_speaking = False

    detector = _Detector()
    processor = SpeechToTextProcessor(
        provider=object(), language="en", turn_detector=detector
    )

    room = b"\x40\x00" * 160
    voice = b"\x00\x40" * 160

    # The room, at length.
    for _ in range(30):
        for frame in processor._audio_for_provider(room):
            assert set(frame) == {0}, "room audio reached the transcriber"

    # The caller starts. The pre-roll comes with them, so the opening
    # syllable is not lost.
    detector.is_speaking = True
    released = processor._audio_for_provider(voice)

    assert len(released) > 1, "the pre-roll was not released with the onset"
    assert any(frame == room for frame in released), (
        "the frames just before the detector caught up were dropped, which "
        "is the caller's first syllable"
    )

    # And while they speak, their audio goes through untouched.
    assert processor._audio_for_provider(voice) == [voice]


async def test_the_deafness_watchdog_only_waits_on_audio_actually_sent(
    monkeypatch,
) -> None:
    """
    The interaction that made this gate wait twice.

    The watchdog restarts the stream when speech-level audio went in and no
    transcript came back. Once the room is being replaced with silence, it
    has to measure what was *sent* - otherwise it sees the caller's room,
    sees the provider correctly transcribing nothing from the silence it was
    handed, and restarts a healthy stream every few seconds forever.
    """

    from app import config
    from app.media_session import SpeechToTextProcessor

    monkeypatch.setattr(config, "STT_GATE_ON_SPEECH", True)

    class _Detector:
        def __init__(self) -> None:
            self.is_speaking = False

    detector = _Detector()
    processor = SpeechToTextProcessor(
        provider=object(), language="en", turn_detector=detector
    )
    loud_room = b"\x00\x40" * 160  # speech-level, but not the caller

    for _ in range(30):
        processor._audio_for_provider(loud_room)

    assert processor._last_speech_at == 0.0, (
        "the watchdog is waiting on a transcript for audio that was never "
        "sent, and will restart a healthy stream"
    )

    detector.is_speaking = True
    processor._audio_for_provider(loud_room)

    assert processor._last_speech_at > 0.0
