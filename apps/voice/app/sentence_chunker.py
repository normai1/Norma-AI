"""
Pure sentence-chunking logic (item 20e) - lets streamed LLM text be spoken
sentence by sentence, before the full reply has finished (CLAUDE.md: "start
speaking before the LLM finishes. Chunk on sentence boundaries."). No
Pipecat, no I/O - mirrors app/turn_detection.py's and app/conversation.py's
own pure-module-plus-thin-adapter split.

A deliberate placeholder, not real sentence-boundary detection: splitting on
terminal punctuation alone will mis-split on abbreviations ("Dr. Smith").
Real sentence-boundary modeling is out of scope for this spike, the same
honestly-documented limitation app/turn_detection.py's
is_semantically_complete already carries.
"""

_TERMINAL_PUNCTUATION = (".", "!", "?")


class SentenceChunker:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        """
        Accumulate delta and return every complete sentence it newly
        completes, in order. Text with no sentence boundary yet stays
        buffered for the next feed() or flush() call.
        """

        self._buffer += delta
        sentences: list[str] = []

        while True:
            boundary = self._find_boundary(self._buffer)

            if boundary is None:
                break

            sentence = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:]

            if sentence:
                sentences.append(sentence)

        return sentences

    def flush(self) -> str | None:
        """
        The trailing fragment left over once the stream has ended (no
        terminal punctuation was ever seen for it), or None if nothing is
        buffered. Clears the buffer either way.
        """

        remaining = self._buffer.strip()
        self._buffer = ""

        return remaining or None

    def reset(self) -> None:
        """
        Discard buffered text without returning it - used on barge-in,
        where the abandoned reply's unspoken tail must never be spoken.
        """

        self._buffer = ""

    @staticmethod
    def _find_boundary(text: str) -> int | None:
        for index, character in enumerate(text):
            if character in _TERMINAL_PUNCTUATION:
                return index + 1

        return None
