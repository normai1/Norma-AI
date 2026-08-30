from app.sentence_chunker import SentenceChunker


def test_feed_returns_nothing_before_a_sentence_boundary() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("We are open") == []
    assert chunker.feed(" nine to five") == []


def test_feed_returns_a_sentence_exactly_when_it_completes() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("We are open") == []
    assert chunker.feed(" nine to five.") == ["We are open nine to five."]


def test_feed_returns_multiple_complete_sentences_from_one_call() -> None:
    chunker = SentenceChunker()

    result = chunker.feed("First one. Second one! Third pending")

    assert result == ["First one.", "Second one!"]


def test_feed_returns_sentences_across_separate_calls_in_order() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("One. Two.") == ["One.", "Two."]
    assert chunker.feed(" Three.") == ["Three."]


def test_flush_returns_the_trailing_fragment() -> None:
    chunker = SentenceChunker()
    chunker.feed("An incomplete thought")

    assert chunker.flush() == "An incomplete thought"


def test_flush_returns_none_when_nothing_is_buffered() -> None:
    chunker = SentenceChunker()
    chunker.feed("Complete.")

    assert chunker.flush() is None


def test_flush_clears_the_buffer() -> None:
    chunker = SentenceChunker()
    chunker.feed("First fragment")
    chunker.flush()

    assert chunker.feed(" more text.") == ["more text."]


def test_reset_discards_buffered_text_without_returning_it() -> None:
    chunker = SentenceChunker()
    chunker.feed("Abandoned fragment")
    chunker.reset()

    assert chunker.flush() is None
    assert chunker.feed(" fresh text.") == ["fresh text."]
