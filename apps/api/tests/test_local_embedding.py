"""
The in-process embedding provider.

Most of these run against a fake runtime, so they are fast and hermetic and
still cover what the provider itself is responsible for: the contract with
embedding.py, the dimension checks, the singleton, and keeping inference off
the event loop.

The one test that loads the real model says so and skips when onnxruntime or
the model files are unavailable. It is the only test that can catch the
failure that matters most - a pooling or normalisation mistake produces
perfectly well-formed vectors of the right width that are simply incomparable
with every vector already in the chunks table, and no amount of faking finds
that.
"""

import asyncio
import time

import pytest

from app.providers.embedding import (
    EmbeddingDimensionMismatch,
    EmbeddingProviderUnavailable,
)
from app.providers.local_embedding import (
    LocalEmbeddingProvider,
    reset_local_embedding_runtime,
)


class _FakeRuntime:
    """
    Stands in for the loaded model. Records the texts it was asked for, so
    the tests can prove what reached it.
    """

    def __init__(self, *, dimension: int = 768, delay: float = 0.0) -> None:
        self.dimension = dimension
        self.delay = delay
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str], *, max_length: int) -> list[list[float]]:
        self.calls.append(list(texts))

        if self.delay:
            time.sleep(self.delay)

        return [[0.1] * self.dimension for _ in texts]


@pytest.fixture(autouse=True)
def _clean_runtime():
    reset_local_embedding_runtime()
    yield
    reset_local_embedding_runtime()


@pytest.fixture
def fake_runtime(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setattr(
        "app.providers.local_embedding._get_runtime",
        lambda **_kwargs: runtime,
    )

    return runtime


def _provider(**overrides) -> LocalEmbeddingProvider:
    kwargs = {"model": "BAAI/bge-base-en-v1.5", "dimension": 768}
    kwargs.update(overrides)

    return LocalEmbeddingProvider(**kwargs)


async def test_embeds_one_vector_per_text(fake_runtime) -> None:
    vectors = await _provider().embed(["first", "second"])

    assert len(vectors) == 2
    assert all(len(vector) == 768 for vector in vectors)
    assert fake_runtime.calls == [["first", "second"]]


async def test_empty_input_never_touches_the_model(fake_runtime) -> None:
    assert await _provider().embed([]) == []
    assert fake_runtime.calls == []


async def test_a_wrong_width_vector_is_a_hard_failure(monkeypatch) -> None:
    """
    CLAUDE.md section 6.4 forbids padding or truncating a mismatched
    embedding. A model that does not produce the configured width is a
    misconfiguration, and writing its output into a 768-wide column would
    corrupt retrieval silently.
    """

    monkeypatch.setattr(
        "app.providers.local_embedding._get_runtime",
        lambda **_kwargs: _FakeRuntime(dimension=384),
    )

    with pytest.raises(EmbeddingDimensionMismatch):
        await _provider().embed(["anything"])


async def test_a_model_failure_becomes_a_provider_error(monkeypatch) -> None:
    """
    Whatever onnxruntime raises must arrive as this module's own error type,
    so callers handle it the same way they handle a hosted provider being
    down.
    """

    def _explode(**_kwargs):
        raise RuntimeError("onnx graph is broken")

    monkeypatch.setattr("app.providers.local_embedding._get_runtime", _explode)

    with pytest.raises(EmbeddingProviderUnavailable):
        await _provider().embed(["anything"])


async def test_inference_does_not_block_the_event_loop(monkeypatch) -> None:
    """
    The reason embed() goes through asyncio.to_thread.

    Inference is 100-200ms of synchronous CPU. Run inline it stalls every
    other coroutine in the process - on the API that is every concurrent
    request, and this provider exists to protect a latency budget, so
    trading a hosted call for a process-wide stall would be no improvement
    at all.
    """

    monkeypatch.setattr(
        "app.providers.local_embedding._get_runtime",
        lambda **_kwargs: _FakeRuntime(delay=0.3),
    )

    ticks = 0

    async def _tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(_tick())
    try:
        await _provider().embed(["slow"])
    finally:
        ticker.cancel()

    # Inline, the loop would be frozen for the whole 300ms and tick zero
    # times. A generous floor: this asserts "the loop kept running", not a
    # precise scheduling rate, so it does not turn into a flaky test on a
    # loaded CI machine.
    assert ticks > 5, f"event loop only advanced {ticks} times during inference"


async def test_the_model_is_loaded_once_across_providers(monkeypatch) -> None:
    """
    get_embedding_provider() builds a fresh provider per request. A 440MB
    model loaded per provider would be an outage, not a provider.
    """

    loads = 0

    class _CountingRuntime(_FakeRuntime):
        pass

    def _build(*, model_path: str, threads: int):
        nonlocal loads
        loads += 1

        return _CountingRuntime()

    monkeypatch.setattr("app.providers.local_embedding._Runtime", _build)
    monkeypatch.setattr(
        "app.providers.local_embedding._resolve_model_path",
        lambda model, configured_path: "/models/bge",
    )

    await _provider().embed(["one"])
    await _provider().embed(["two"])
    await _provider().embed(["three"])

    assert loads == 1


async def test_concurrent_first_calls_still_load_once(monkeypatch) -> None:
    """
    Startup fires the warm-up detached, so a real request can arrive while
    the model is still loading. Both must end up behind one load rather than
    starting a second.
    """

    loads = 0

    def _build(*, model_path: str, threads: int):
        nonlocal loads
        loads += 1
        time.sleep(0.2)

        return _FakeRuntime()

    monkeypatch.setattr("app.providers.local_embedding._Runtime", _build)
    monkeypatch.setattr(
        "app.providers.local_embedding._resolve_model_path",
        lambda model, configured_path: "/models/bge",
    )

    await asyncio.gather(*[_provider().embed([f"text {i}"]) for i in range(4)])

    assert loads == 1


def _model_is_on_disk() -> bool:
    """
    Whether the real model can be loaded without downloading it.

    The suite must not pull 436MB on a fresh checkout or in CI, and
    CLAUDE.md section 28 requires it to run with zero paid external calls -
    a Hub download is not a paid call but it is exactly the kind of network
    dependency that makes a test suite unreliable.
    """

    from app.core.config import settings

    if settings.embedding_local_path:
        return True

    try:
        from huggingface_hub import snapshot_download

        snapshot_download(settings.embedding_model, local_files_only=True)

        return True
    except Exception:
        return False


async def test_real_model_matches_the_stored_vector_space() -> None:
    """
    The test faking cannot replace.

    CLS pooling and L2 normalisation are what BAAI's own configuration for
    this model specifies, and the chunks already in the database were
    embedded that way by the hosted provider. Get the pooling wrong - mean
    instead of CLS, or no normalisation - and every vector is still 768
    floats and still looks fine, but sits in a different space from
    everything already stored, so retrieval quietly returns nonsense.

    Checked by behaviour rather than by exact values, which differ across
    runtimes: unit length, and a paraphrase scoring clearly above unrelated
    text.
    """

    pytest.importorskip("onnxruntime")

    if not _model_is_on_disk():
        pytest.skip("embedding model not cached locally; skipping to avoid a download")

    provider = _provider()

    try:
        vectors = await provider.embed(
            [
                "what are your opening hours",
                "when are you open",
                "how do I replace a clutch cable",
            ]
        )
    except EmbeddingProviderUnavailable as exc:
        pytest.skip(f"local model unavailable: {exc}")

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert all(abs(dot(v, v) - 1.0) < 1e-3 for v in vectors), "not unit length"

    paraphrase = dot(vectors[0], vectors[1])
    unrelated = dot(vectors[0], vectors[2])

    assert paraphrase > unrelated + 0.15, (
        f"paraphrase {paraphrase:.3f} not clearly above unrelated "
        f"{unrelated:.3f} - pooling or normalisation is wrong"
    )
