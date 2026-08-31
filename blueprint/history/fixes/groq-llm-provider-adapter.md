# Fix: Groq LLM provider adapter

**Type:** Fix

## The problem

`.env` already sets `LLM_PROVIDER=groq` with a real `GROQ_API_KEY`, and `blueprint/build-plan.md`'s
Planning decisions section now locks the realtime LLM choice to `llama-3.1-8b-instant` (served by
Groq). But `apps/voice/app/llm_provider_factory.py` only recognizes `"mock"` and `"anthropic"` -
any other `LLM_PROVIDER` value raises `UnknownLLMProviderError` at provider construction. The
realtime voice pipeline is currently non-functional against this `.env` for anything beyond the
mock provider.

Separately, `blueprint/build-plan.md`'s "LLM environment variables" line still lists only
`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`, no longer accurate now that `GROQ_API_KEY` is a real,
in-use credential.

## The fix

Add a `GroqLLM` adapter implementing `apps/voice/app/llm.py`'s `LLMProvider` protocol, mirroring
`AnthropicLLM`'s exact shape and error-handling precedent, then wire `"groq"` into
`llm_provider_factory.py`. Verified against the actually-installed `groq` SDK (v1.7.0, added to
`apps/voice/requirements.txt`) rather than assumed:

- `groq.AsyncGroq` exposes `client.chat.completions.create(model=..., messages=[...],
  temperature=..., stream=True)`, returning `AsyncStream[ChatCompletionChunk]` - standard
  OpenAI-compatible shape, unlike Anthropic's `messages.stream()` async-context-manager form.
- No separate `system` parameter exists (unlike Anthropic) - a system prompt is a `{"role":
  "system", "content": ...}` entry prepended to `messages`, the standard OpenAI-compatible
  convention.
- Each streamed `ChatCompletionChunk.choices[0].delta.content` is `str | None` (the first chunk's
  delta is often role-only with `content=None`) - must guard `if delta.content:` before yielding,
  or the stream yields spurious `None`/empty deltas.
- `groq.APITimeoutError` is a subclass of `groq.APIConnectionError`, itself a subclass of
  `groq.APIError`, itself a subclass of the `groq.GroqError` base every other SDK failure derives
  from - the identical hierarchy shape `anthropic_llm.py`'s own docstring already documents for
  the Anthropic SDK, so the same "catch the specific timeout first, then the broad base" pattern
  applies unchanged.
- `"llama-3.1-8b-instant"` is a real, currently-valid model literal in the installed SDK's own
  type stubs - confirms the `.env` value is a real Groq model id, not a typo.

Must not break: the existing `"mock"`/`"anthropic"` paths, or any of the many existing
`test_media_session.py`/`test_latency_regression.py` tests that monkeypatch
`get_llm_provider` directly (unaffected, since they replace the factory function itself).

## Build steps

- [x] **Step 1 - GroqLLM adapter and factory wiring**
  - `apps/voice/requirements.txt`: add `groq`.
  - `apps/voice/app/groq_llm.py` (new): `GroqLLM` class mirroring `AnthropicLLM`'s constructor
    shape (`api_key`, `model`, injectable `client` for testing, `max_tokens` default), with
    `stream()` prepending a system message, guarding `delta.content`, and mapping
    `groq.APITimeoutError` → `LLMProviderTimeout`, `groq.GroqError` → `LLMProviderUnavailable`.
  - `apps/voice/app/config.py`: add `GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")`.
  - `apps/voice/app/llm_provider_factory.py`: add a `"groq"` branch (mirroring the `"anthropic"`
    branch's shape exactly, including a `MissingGroqApiKeyError` for a missing key), update
    `_VALID_PROVIDER_NAMES`.
  - New tests: `apps/voice/tests/test_groq_llm.py` (mirroring `test_anthropic_llm.py`'s exact
    four cases: yields scripted deltas, passes model/messages-with-system/temperature through,
    maps a timeout, maps another Groq error) and a new `apps/voice/tests/test_llm_provider_factory.py`
    (mirroring `test_provider_factory.py`'s pattern: default/mock/unknown/groq-without-key cases,
    plus a groq-with-key case constructing a real `GroqLLM`).
  - `blueprint/build-plan.md`: update the "LLM environment variables" line to include
    `GROQ_API_KEY`.
  *Done when:* `cd apps/voice && .venv/Scripts/python -m pytest` passes; `ruff check apps/voice`
  clean; constructing `get_llm_provider()` against the real `.env` (`LLM_PROVIDER=groq`) succeeds
  without raising.

## Verify

Run the voice app's test suite and confirm the new Groq tests pass. Then confirm
`get_llm_provider()` resolves without error when `LLM_PROVIDER=groq` and `GROQ_API_KEY` is set
(matching the real `.env`) - a quick Python one-liner import check, not a full live call (no
budget/need to spend real Groq API credits verifying this fix).

## Result

Full `apps/voice` suite: 118/118 passed (11 new). `ruff check apps/voice`: clean.
`get_llm_provider()` verified against the real `.env` values (loaded directly, matching how
Docker Compose's `env_file` feeds the container) - resolved to a real `GroqLLM` instance with no
error, confirming the realtime voice pipeline is no longer broken against `LLM_PROVIDER=groq`.

## Follow-up: `llama-3.1-8b-instant` was not actually available

After merging the adapter, a real test call still produced no LLM response. Rebuilding the
`norma-voice` image (needed anyway - `requirements.txt` had changed but the image was never
rebuilt, so the container was crash-looping on `ModuleNotFoundError: No module named 'groq'`)
fixed the crash, but a direct real-API call still failed with `groq.NotFoundError: 404 -
model_not_found`. Calling `client.models.list()` against the real API key confirmed
`llama-3.1-8b-instant` is genuinely not in this account's available model catalog - not a bug in
this fix, an external fact about the Groq account/catalog. The user chose `groq/compound-mini`
from the models actually available on the key; `.env`'s `LLM_REALTIME_MODEL`,
`blueprint/build-plan.md`'s Realtime LLM planning-decision line, and `CLAUDE.md` section 8.1 were
all updated to match, and re-verified with a real (non-mocked) `GroqLLM.stream()` call returning
an actual response.
