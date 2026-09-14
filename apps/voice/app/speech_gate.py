"""
What the transcriber is allowed to hear.

Until now every frame of a call went to the speech-to-text provider, the
whole session, whether the caller was speaking or not. A transcriber hears
everything it is given and is far more willing to find words in a sound than
a voice-activity detector is to call that sound speech, so a television, a
passing conversation or a door became text. That text ended turns, cancelled
replies, and appeared in the transcript panel as things the caller never
said - reported repeatedly as the assistant "taking background noises and
generating random words by himself".

Every previous fix worked downstream of it: a transcript the VAD never heard
cannot end a turn, and cannot interrupt a reply. Both were right and neither
addresses the cause, because the invented words are still produced, still
shown, and still cost provider time. This is the cause: do not send the room.

Three details that are the whole difficulty:

- **Silence is sent, not nothing.** The provider commits transcripts on its
  own silence detection and its sense of time comes from the stream; dropping
  frames would compress that and make a two-second pause look instant. The
  browser's echo gate already takes this approach for the same reason.

- **A pre-roll is kept.** The detector confirms speech a little after it
  actually starts, so gating strictly on its verdict clips the first syllable
  of every sentence - and "listen to every single word" is the other half of
  what was asked for. The frames immediately before the verdict are held back
  and released the moment it arrives.

- **The end is not clipped either.** Speech is forwarded for a short while
  after the detector goes quiet, so a trailing word on a falling voice is
  still transcribed.
"""

from collections import deque


class SpeechGate:
    """
    Decides, frame by frame, what reaches the transcriber.

    Pure and synchronous: it takes a frame and a verdict and returns frames.
    No audio analysis of its own - the detector that turn-taking already
    trusts is the one source of truth about whether the caller is speaking,
    and a second opinion here would be a second thing to disagree.
    """

    def __init__(
        self,
        *,
        pre_roll_seconds: float,
        hangover_seconds: float,
        sample_rate: int,
        frame_seconds: float = 0.02,
    ) -> None:
        self._sample_rate = sample_rate
        self._hangover_seconds = hangover_seconds
        # Frames, not seconds, so the arithmetic happens once.
        self._pre_roll = deque(maxlen=max(1, round(pre_roll_seconds / frame_seconds)))
        self._frame_seconds = frame_seconds
        self._quiet_for = 0.0
        self._open = False

    @property
    def is_open(self) -> bool:
        """Whether the caller's own audio is currently being forwarded."""

        return self._open

    def feed(self, chunk: bytes, *, speaking: bool) -> list[bytes]:
        """
        The frames to send for this one incoming frame.

        Usually one - the chunk itself, or an equal length of silence. At the
        moment speech is confirmed it is the held-back pre-roll followed by
        the chunk, which is how the caller's opening syllable survives the
        detector's own lag.
        """

        if speaking:
            self._quiet_for = 0.0

            if self._open:
                return [chunk]

            # Speech has just been confirmed. Everything held back is what
            # the caller was already saying before the detector caught up.
            self._open = True
            released = list(self._pre_roll)
            self._pre_roll.clear()

            return [*released, chunk]

        if self._open:
            self._quiet_for += self._frame_seconds

            if self._quiet_for < self._hangover_seconds:
                # Still inside the tail of an utterance: a trailing word on a
                # falling voice is not the room.
                return [chunk]

            self._open = False
            self._quiet_for = 0.0

        # Not the caller. Hold the frame in case it turns out to be the start
        # of something, and send an equal length of silence so the provider's
        # sense of time is unchanged.
        self._pre_roll.append(chunk)

        return [bytes(len(chunk))]
