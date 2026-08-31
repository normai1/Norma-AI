"""
Pure per-session failure counting (item 20g) - tracks consecutive fully-
failed LLM turns and reports when a caller-facing session failover should
trigger. Deliberately no Pipecat dependency here, mirroring turn_detection.py's
and turn_metrics.py's own pure-module-plus-thin-adapter precedent -
app/media_session.py is the thin adapter that wires this into the live
pipeline.

Only LLM turns feed this counter, not TTS sentence failures - see this
feature's spec for why a single bad sentence out of several is a minor
blemish, not a session-ending signal, and why a persistent TTS-only outage
(rare, given the documented shared failure domain with STT) is deferred
rather than tracked separately here.
"""


class SessionResilienceTracker:
    def __init__(self, *, max_consecutive_failures: int) -> None:
        self._max_consecutive_failures = max_consecutive_failures
        self._consecutive_failures = 0

    def record_turn_failed(self) -> bool:
        """
        Call once a turn has exhausted its retries and given up. Returns
        True if the consecutive-failure threshold is now crossed - the
        caller's cue to trigger session failover.
        """

        self._consecutive_failures += 1

        return self._consecutive_failures >= self._max_consecutive_failures

    def record_turn_succeeded(self) -> None:
        """
        Call once a turn completes successfully - resets the counter, so a
        single isolated blip between two successes never accumulates
        toward the threshold.
        """

        self._consecutive_failures = 0
