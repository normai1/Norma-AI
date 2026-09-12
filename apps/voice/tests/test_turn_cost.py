"""
Item 25b: what a turn cost, in tokens and in money.

CLAUDE.md section 21: "Capture provider cost per call from day one. Gross
margin per minute determines whether this business works." The failure worth
designing against is not a wrong number - it is a *plausible* wrong number.
Reporting an unpriced model, or a provider that reported no usage, as free
would understate cost invisibly and in the flattering direction, so the
distinction between zero and unknown is what most of this file is about.
"""

import uuid
from decimal import Decimal

import pytest
from norma_shared.token_cost import ModelPrice, TokenUsage, cost_micro_usd

from app import config
from app.groq_llm import GroqLLM
from app.llm_pricing import realtime_turn_cost_micro_usd
from app.turn_metrics import TurnMetricsRecorder

_GPT_OSS = "openai/gpt-oss-120b"
# Not a claim about what Groq charges - a pair of round numbers that make the
# arithmetic below checkable by hand.
_PRICES = {_GPT_OSS: ModelPrice.of("0.15", "0.75")}


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeXGroq:
    def __init__(self, usage: object | None) -> None:
        self.usage = usage


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.delta = _FakeDelta(content)


class _FakeChunk:
    """
    One streamed chunk in the shape the installed groq SDK actually
    produces: choices carrying text, and - on the final chunk - an x_groq
    field carrying the usage, with an empty choices list.
    """

    def __init__(
        self,
        content: str | None = None,
        *,
        x_groq: object | None = None,
        usage: object | None = None,
        has_choices: bool = True,
    ) -> None:
        self.choices = [_FakeChoice(content)] if has_choices else []
        self.x_groq = x_groq
        self.usage = usage


class _FakeStream:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk


class _FakeCompletions:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = chunks

    async def create(self, **_kwargs) -> _FakeStream:
        return _FakeStream(self._chunks)


class _FakeGroqClient:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(chunks)})()


async def _drain(provider: GroqLLM) -> list[str]:
    from app.conversation import Message

    return [
        delta
        async for delta in provider.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
        )
    ]


async def test_groq_reports_the_tokens_its_final_chunk_carried() -> None:
    client = _FakeGroqClient(
        [
            _FakeChunk("Hello"),
            _FakeChunk(
                x_groq=_FakeXGroq(_FakeUsage(prompt_tokens=412, completion_tokens=37)),
                has_choices=False,
            ),
        ]
    )
    provider = GroqLLM(api_key="fake", model=_GPT_OSS, client=client)

    assert await _drain(provider) == ["Hello"]
    assert provider.last_usage() == TokenUsage(prompt_tokens=412, completion_tokens=37)


async def test_the_usage_carrying_chunk_does_not_break_the_reply() -> None:
    """
    The final chunk of an OpenAI-compatible stream has an empty choices
    list. Reading choices[0] on it - which is what the code did before this
    item - is an IndexError in the middle of the audio path.
    """

    client = _FakeGroqClient(
        [
            _FakeChunk("Hello "),
            _FakeChunk("there."),
            _FakeChunk(
                x_groq=_FakeXGroq(_FakeUsage(prompt_tokens=1, completion_tokens=2)),
                has_choices=False,
            ),
        ]
    )
    provider = GroqLLM(api_key="fake", model=_GPT_OSS, client=client)

    assert "".join(await _drain(provider)) == "Hello there."


async def test_the_openai_compatible_usage_field_is_read_too() -> None:
    """
    x_groq.usage is where a real Groq stream puts them today. The
    standard-compatible top-level field is read as well, so a provider
    moving to it does not silently stop reporting cost.
    """

    client = _FakeGroqClient(
        [
            _FakeChunk("Hi"),
            _FakeChunk(
                usage=_FakeUsage(prompt_tokens=9, completion_tokens=4),
                has_choices=False,
            ),
        ]
    )
    provider = GroqLLM(api_key="fake", model=_GPT_OSS, client=client)
    await _drain(provider)

    assert provider.last_usage() == TokenUsage(prompt_tokens=9, completion_tokens=4)


async def test_a_stream_that_reports_nothing_reports_nothing() -> None:
    provider = GroqLLM(
        api_key="fake", model=_GPT_OSS, client=_FakeGroqClient([_FakeChunk("Hi")])
    )
    await _drain(provider)

    assert provider.last_usage() is None


async def test_a_later_turn_never_inherits_an_earlier_turns_tokens() -> None:
    """
    The single way this could produce a confidently wrong cost: last-call
    state left over from the previous turn being billed to this one.
    """

    reporting = _FakeGroqClient(
        [
            _FakeChunk("Hi"),
            _FakeChunk(
                x_groq=_FakeXGroq(_FakeUsage(prompt_tokens=5, completion_tokens=6)),
                has_choices=False,
            ),
        ]
    )
    provider = GroqLLM(api_key="fake", model=_GPT_OSS, client=reporting)
    await _drain(provider)

    assert provider.last_usage() is not None

    provider._client = _FakeGroqClient([_FakeChunk("Hi again")])
    await _drain(provider)

    assert provider.last_usage() is None


def test_cost_is_exact_at_the_scale_a_turn_actually_costs() -> None:
    """
    412 prompt tokens at $0.15/M is $0.0000618; 37 completion tokens at
    $0.75/M is $0.00002775. Together $0.00008955, which is 89.55 micro-
    dollars and rounds to 90.
    """

    cost = cost_micro_usd(
        TokenUsage(prompt_tokens=412, completion_tokens=37), _GPT_OSS, _PRICES
    )

    assert cost == 90


def test_input_and_output_are_priced_separately() -> None:
    """
    Every provider charges more for output than input, so a single blended
    rate would misreport a long answer to a short question.
    """

    same_tokens_as_input = cost_micro_usd(
        TokenUsage(prompt_tokens=1_000_000, completion_tokens=0), _GPT_OSS, _PRICES
    )
    same_tokens_as_output = cost_micro_usd(
        TokenUsage(prompt_tokens=0, completion_tokens=1_000_000), _GPT_OSS, _PRICES
    )

    assert same_tokens_as_input == 150_000
    assert same_tokens_as_output == 750_000


def test_no_usage_means_unknown_not_free() -> None:
    assert cost_micro_usd(None, _GPT_OSS, _PRICES) is None


def test_an_unpriced_model_means_unknown_not_free() -> None:
    """
    The flattering failure: a model nobody has priced reported as costing
    nothing would make the margin look better than it is, with nothing in
    the data to show it.
    """

    assert cost_micro_usd(
        TokenUsage(prompt_tokens=100, completion_tokens=100), "some-new-model", _PRICES
    ) is None


def test_an_empty_reply_really_does_cost_only_its_prompt() -> None:
    """
    The one genuine zero this has to stay distinguishable from: a priced
    model that produced no output tokens still costs its input.
    """

    cost = cost_micro_usd(
        TokenUsage(prompt_tokens=1_000_000, completion_tokens=0), _GPT_OSS, _PRICES
    )

    assert cost == 150_000


def test_money_never_passes_through_a_float() -> None:
    """
    A tenth of a cent has no exact binary representation, and these numbers
    are summed into invoices. Prices are Decimals and the result is an
    integer count of micro-dollars; nothing in between is a float.
    """

    price = ModelPrice.of("0.15", "0.75")

    assert isinstance(price.input_usd_per_million, Decimal)
    assert isinstance(
        cost_micro_usd(
            TokenUsage(prompt_tokens=3, completion_tokens=7), _GPT_OSS, _PRICES
        ),
        int,
    )


def test_an_unconfigured_price_records_tokens_but_no_cost(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    The out-of-the-box state. Cost is unknown, and says so once in the log
    rather than silently.
    """

    monkeypatch.setattr(config, "LLM_REALTIME_INPUT_USD_PER_MTOK", "")
    monkeypatch.setattr(config, "LLM_REALTIME_OUTPUT_USD_PER_MTOK", "")
    monkeypatch.setattr(config, "LLM_REALTIME_MODEL", "an-unpriced-model")
    monkeypatch.setattr("app.llm_pricing._warned_models", set())

    cost = realtime_turn_cost_micro_usd(
        TokenUsage(prompt_tokens=10, completion_tokens=20)
    )

    assert cost is None
    assert "no price configured" in caplog.text


def test_a_typo_in_a_price_does_not_stop_the_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    These are operator-entered strings. A malformed one must degrade to an
    unpriced model, not raise mid-turn - the same reasoning configure_logging
    applies to a misspelled LOG_LEVEL.
    """

    monkeypatch.setattr(config, "LLM_REALTIME_INPUT_USD_PER_MTOK", "fifteen cents")
    monkeypatch.setattr(config, "LLM_REALTIME_OUTPUT_USD_PER_MTOK", "0.75")
    monkeypatch.setattr("app.llm_pricing._warned_models", set())

    assert (
        realtime_turn_cost_micro_usd(TokenUsage(prompt_tokens=1, completion_tokens=1))
        is None
    )


def test_a_configured_price_reaches_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "LLM_REALTIME_MODEL", _GPT_OSS)
    monkeypatch.setattr(config, "LLM_REALTIME_INPUT_USD_PER_MTOK", "0.15")
    monkeypatch.setattr(config, "LLM_REALTIME_OUTPUT_USD_PER_MTOK", "0.75")

    assert (
        realtime_turn_cost_micro_usd(
            TokenUsage(prompt_tokens=412, completion_tokens=37)
        )
        == 90
    )


def test_the_recorder_files_the_cost_against_the_turn_that_incurred_it() -> None:
    recorder = TurnMetricsRecorder(call_id=uuid.uuid4())
    generation = recorder.current_generation()

    recorder.record_token_cost(
        generation, prompt_tokens=100, completion_tokens=50, cost_micro_usd=52
    )
    completed = recorder.finish_turn()

    assert (completed.prompt_tokens, completed.completion_tokens) == (100, 50)
    assert completed.cost_micro_usd == 52


def test_an_abandoned_replys_tokens_are_not_billed_to_the_turn_that_replaced_it() -> (
    None
):
    """
    A barge-in advances the turn while the abandoned reply's own task is
    still unwinding. That task's late accounting call must not land on the
    new turn - the same generation guard every timestamp mark uses, and for
    the same empirically-established reason (see TurnMetricsRecorder).
    """

    recorder = TurnMetricsRecorder(call_id=uuid.uuid4())
    abandoned_generation = recorder.current_generation()

    recorder.finish_turn()
    recorder.record_token_cost(
        abandoned_generation,
        prompt_tokens=999,
        completion_tokens=999,
        cost_micro_usd=999,
    )

    assert recorder.finish_turn().prompt_tokens is None
