"""
In-process implementation of embedding.py's contract, running the configured
sentence-embedding model on the CPU with ONNX Runtime instead of calling a
hosted API.

This exists because the hosted alternative is the measured cause of the
product's most common wrong answer. huggingface_embedding.py's own comment
records it: 0.28-0.43s warm, 4-12s cold, roughly one call in three. The voice
plane allows a turn's retrieval about two seconds before it gives up and
tells the caller it could not look that up - so on a third of first questions
the knowledge was indexed, scored well, and never reached the model.
Benchmarked against the same machine's container, batch of one:

    hosted router     280-430ms typical, 4-12s cold
    this provider     106ms p50, 137ms p95

It wins on the median and it wins far more on the tail, which is the part
that was costing callers answers. Nothing here is close to the timeout.

That 106ms depends on pinning the thread count - see
settings.embedding_local_threads for the sweep. Left at onnxruntime's own
default of "every core" the same query measures 447ms p50 and 1021ms p95,
which would have made this barely worth doing.

CLAUDE.md section 6.4 records a previous in-process attempt that failed, and
the differences are the reason this one is worth making. That was a
multilingual model with no hosted provider at all, loaded through
sentence-transformers, executing its own `trust_remote_code` Python - which
crashed on CPU on first inference. This is a 109M-parameter English BERT
exported to ONNX by its own authors, with no custom code path: the runtime
reads a graph and runs it.

Two things make or break it, both of which that history warns about:

- **The model loads at startup, not on first use.** Loading costs seconds
  (1.5s on a normal filesystem, far worse on Docker Desktop), and a first
  caller who pays it times out - "the first question never works", which is
  precisely the earlier failure. app/main.py's detached
  warm_embedding_connection() call starts the load while the API is coming
  up; anything arriving mid-load waits on the same lock rather than starting
  a second one.

- **Inference never runs on the event loop.** It is 100-200ms of synchronous
  CPU, which would stall every other coroutine in the process for its
  duration. asyncio.to_thread moves it off; onnxruntime releases the GIL
  inside run(), so this genuinely parallelises rather than relocating the
  stall.

The session is process-global rather than per-provider-instance because
get_embedding_provider() constructs a fresh provider per request, and a
440MB model reloaded per request is not a provider, it is an outage.
"""

import asyncio
import logging
import os
import threading
from typing import Any

from norma_shared.provider_telemetry import provider_call

from app.providers.embedding import (
    EmbeddingDimensionMismatch,
    EmbeddingProviderUnavailable,
)

logger = logging.getLogger(__name__)

# Guards construction of the singleton below. A plain threading.Lock, not an
# asyncio one: every path that touches this already runs inside
# asyncio.to_thread, so the contention being serialised is between worker
# threads, not coroutines.
_load_lock = threading.Lock()
_runtime: "_Runtime | None" = None


class _Runtime:
    """
    The loaded tokenizer and ONNX session, plus the pooling that turns the
    model's per-token output into one vector per text.
    """

    def __init__(self, *, model_path: str, threads: int) -> None:
        import numpy as np
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self._np = np
        self._tokenizer = AutoTokenizer.from_pretrained(model_path)

        options = ort.SessionOptions()

        # Pinned rather than left to the runtime's default of "every core".
        # This process is a web server with its own worker pool, and an
        # embedding that grabs all eight cores starves the requests it is
        # meant to be serving alongside.
        if threads > 0:
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1

        self._session = ort.InferenceSession(
            _onnx_file(model_path),
            options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {i.name for i in self._session.get_inputs()}

    def embed(self, texts: list[str], *, max_length: int) -> list[list[float]]:
        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="np",
        )
        feed = {
            name: value.astype(self._np.int64)
            for name, value in encoded.items()
            if name in self._input_names
        }
        hidden = self._session.run(None, feed)[0]

        # CLS pooling then L2 normalisation, which is what BAAI's own
        # sentence-transformers configuration for this model does
        # (1_Pooling/config.json sets pooling_mode_cls_token, and the model
        # card normalises). Mean pooling here instead would produce vectors
        # that are internally consistent but incomparable with anything the
        # hosted provider already wrote into the chunks table.
        pooled = hidden[:, 0]
        norms = self._np.linalg.norm(pooled, axis=1, keepdims=True)

        # A zero-length vector cannot be normalised and would divide to NaN.
        # It should not happen for any real text, and silently emitting NaN
        # into pgvector would be far worse than saying so.
        if not self._np.all(norms > 0):
            raise EmbeddingProviderUnavailable(
                "Local embedding produced a zero-length vector",
            )

        return (pooled / norms).tolist()


def _onnx_file(model_path: str) -> str:
    """
    The exported graph inside a model directory.

    Checked explicitly so a directory that has the tokenizer but no ONNX
    export fails with something a reader can act on, rather than whatever
    onnxruntime says about a missing file.
    """

    candidate = os.path.join(model_path, "onnx", "model.onnx")

    if os.path.isfile(candidate):
        return candidate

    candidate = os.path.join(model_path, "model.onnx")

    if os.path.isfile(candidate):
        return candidate

    raise EmbeddingProviderUnavailable(
        f"No ONNX export found under {model_path!r}. Expected "
        "onnx/model.onnx or model.onnx.",
    )


def _resolve_model_path(model: str, configured_path: str) -> str:
    """
    Where the model files are.

    An explicit path wins, which is what a container image that bakes the
    model in uses. Otherwise the model name is resolved through the
    HuggingFace cache - downloading it if this machine has never seen it,
    which is convenient in development and is exactly what must not happen
    in production, hence the log line.
    """

    if configured_path:
        return configured_path

    from huggingface_hub import snapshot_download

    logger.info("resolving embedding model %s from the HuggingFace cache", model)

    return snapshot_download(
        model,
        allow_patterns=["onnx/model.onnx", "*.json", "vocab.txt", "*.txt"],
    )


def _get_runtime(*, model: str, model_path: str, threads: int) -> _Runtime:
    global _runtime

    if _runtime is not None:
        return _runtime

    with _load_lock:
        # Re-checked inside the lock: several threads can pass the check
        # above before any of them takes it, and loading twice would double
        # the memory for no benefit.
        if _runtime is None:
            resolved = _resolve_model_path(model, model_path)

            logger.info("loading embedding model from %s", resolved)
            _runtime = _Runtime(model_path=resolved, threads=threads)
            logger.info("embedding model loaded")

        return _runtime


def reset_local_embedding_runtime() -> None:
    """
    Drop the loaded model. For tests, which must not inherit a session built
    from another test's configuration.
    """

    global _runtime

    with _load_lock:
        _runtime = None


class LocalEmbeddingProvider:
    """
    Embeds text with an ONNX model running in this process.

    Holds no state itself - the loaded model is process-global - so
    constructing one per request costs nothing.
    """

    def __init__(
        self,
        *,
        model: str,
        dimension: int,
        model_path: str = "",
        threads: int = 0,
        max_length: int = 512,
    ) -> None:
        self._model = model
        self._dimension = dimension
        self._model_path = model_path
        self._threads = threads
        self._max_length = max_length

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        with provider_call("local", "embedding.embed"):
            try:
                vectors = await asyncio.to_thread(self._embed_sync, texts)
            except (EmbeddingDimensionMismatch, EmbeddingProviderUnavailable):
                raise
            except Exception as exc:
                raise EmbeddingProviderUnavailable(
                    "Local embedding model failed",
                ) from exc

        if len(vectors) != len(texts):
            raise EmbeddingDimensionMismatch(
                f"Local model returned {len(vectors)} embeddings for "
                f"{len(texts)} input texts",
            )

        for vector in vectors:
            if len(vector) != self._dimension:
                raise EmbeddingDimensionMismatch(
                    f"Local model returned a {len(vector)}-dimension "
                    f"embedding, expected {self._dimension}",
                )

        return vectors

    def _embed_sync(self, texts: list[str]) -> list[list[Any]]:
        runtime = _get_runtime(
            model=self._model,
            model_path=self._model_path,
            threads=self._threads,
        )

        return runtime.embed(texts, max_length=self._max_length)
