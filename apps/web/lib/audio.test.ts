import { describe, expect, it } from "vitest";

import {
  ECHO_GATE_HOLD_MS,
  ECHO_GATE_IDLE,
  ECHO_GATE_LEARN_FRAMES,
  describeInputLevel,
  floatToPCM16,
  interpretCloseCode,
  nextEchoGate,
  pcm16ToFloat32,
  peakOf,
  resampleLinear,
  rms,
  type EchoGateState,
} from "./audio";

/** Runs `frames` frames of the given level through the gate, in order. */
function feed(
  state: EchoGateState,
  level: number,
  frames: number,
  { assistantPlaying = true, now = 0 } = {},
): { state: EchoGateState; sent: number } {
  let sent = 0;
  let current = state;

  for (let i = 0; i < frames; i++) {
    const decision = nextEchoGate(current, { level, assistantPlaying, now: now + i * 20 });

    current = decision.state;

    if (decision.passThrough) {
      sent += 1;
    }
  }

  return { state: current, sent };
}

/** The gate having learned this room's echo level, ready to judge against it. */
function trained(echoLevel: number): EchoGateState {
  return feed(ECHO_GATE_IDLE, echoLevel, ECHO_GATE_LEARN_FRAMES).state;
}

describe("floatToPCM16", () => {
  it("maps 0.0 to 0", () => {
    const view = new DataView(floatToPCM16(new Float32Array([0])));

    expect(view.getInt16(0, true)).toBe(0);
  });

  it("maps 1.0 to the maximum positive Int16", () => {
    const view = new DataView(floatToPCM16(new Float32Array([1])));

    expect(view.getInt16(0, true)).toBe(32767);
  });

  it("maps -1.0 to the minimum Int16", () => {
    const view = new DataView(floatToPCM16(new Float32Array([-1])));

    expect(view.getInt16(0, true)).toBe(-32768);
  });

  it("clamps out-of-range values instead of wrapping", () => {
    const view = new DataView(floatToPCM16(new Float32Array([5, -5])));

    expect(view.getInt16(0, true)).toBe(32767);
    expect(view.getInt16(2, true)).toBe(-32768);
  });

  it("produces one Int16 per input sample", () => {
    const buffer = floatToPCM16(new Float32Array([0, 0.5, -0.5]));

    expect(buffer.byteLength).toBe(6);
  });
});

describe("pcm16ToFloat32", () => {
  it("round-trips floatToPCM16 within Int16 rounding tolerance", () => {
    const original = new Float32Array([0, 0.5, -0.5, 1, -1]);
    const roundTripped = pcm16ToFloat32(floatToPCM16(original));

    for (let i = 0; i < original.length; i++) {
      expect(roundTripped[i]).toBeCloseTo(original[i], 3);
    }
  });

  it("returns an empty array for an empty buffer", () => {
    expect(pcm16ToFloat32(new ArrayBuffer(0))).toHaveLength(0);
  });
});

describe("resampleLinear", () => {
  it("is the identity when the rates already match", () => {
    const samples = new Float32Array([0, 0.25, 0.5]);

    expect(resampleLinear(samples, 16000, 16000)).toBe(samples);
  });

  it("returns an empty array unchanged", () => {
    expect(resampleLinear(new Float32Array([]), 48000, 16000)).toHaveLength(0);
  });

  it("downsamples by an integer ratio", () => {
    const samples = new Float32Array([0, 1, 0, 1, 0, 1]);
    const result = resampleLinear(samples, 48000, 16000);

    expect(result).toHaveLength(2);
  });

  it("upsamples by an integer ratio", () => {
    const samples = new Float32Array([0, 1]);
    const result = resampleLinear(samples, 16000, 48000);

    expect(result).toHaveLength(6);
  });
});

describe("rms", () => {
  it("is zero for silence and for an empty frame", () => {
    expect(rms(new Float32Array(64))).toBe(0);
    expect(rms(new Float32Array(0))).toBe(0);
  });

  it("grows with loudness", () => {
    expect(rms(new Float32Array([0.5, -0.5]))).toBeGreaterThan(
      rms(new Float32Array([0.05, -0.05])),
    );
  });
});

describe("nextEchoGate", () => {
  it("never gates while the assistant is silent, however loud the room is", () => {
    // The caller asking the original question must always get through -
    // gating that would leave the assistant deaf rather than merely
    // over-talkative.
    const quiet = feed(ECHO_GATE_IDLE, 0.001, 5, { assistantPlaying: false });
    const loud = feed(ECHO_GATE_IDLE, 0.9, 5, { assistantPlaying: false });

    expect(quiet.sent).toBe(5);
    expect(loud.sent).toBe(5);
  });

  it("swallows the assistant's own playback coming back through the mic", () => {
    // The feedback loop this exists to break: steady echo at the level the
    // gate just learned must not reach the server, or it is transcribed and
    // answered as if the caller had said it.
    const { sent } = feed(trained(0.08), 0.08, 40);

    expect(sent).toBe(0);
  });

  it("lets the caller through when they genuinely talk over the reply", () => {
    // Speech into the machine's own mic is far louder than its speakers
    // heard back across the room.
    const decision = nextEchoGate(trained(0.05), {
      level: 0.4,
      assistantPlaying: true,
      now: 1_000,
    });

    expect(decision.passThrough).toBe(true);
  });

  it("holds the gate open through the pauses between a caller's words", () => {
    const opened = nextEchoGate(trained(0.05), {
      level: 0.4,
      assistantPlaying: true,
      now: 1_000,
    });

    // A brief dip mid-sentence, well inside the hold window.
    const gap = nextEchoGate(opened.state, {
      level: 0.05,
      assistantPlaying: true,
      now: 1_000 + ECHO_GATE_HOLD_MS / 2,
    });

    expect(gap.passThrough).toBe(true);
  });

  it("closes again once the caller has actually stopped", () => {
    const opened = nextEchoGate(trained(0.05), {
      level: 0.4,
      assistantPlaying: true,
      now: 1_000,
    });

    const afterHold = nextEchoGate(opened.state, {
      level: 0.05,
      assistantPlaying: true,
      now: 1_000 + ECHO_GATE_HOLD_MS + 1,
    });

    expect(afterHold.passThrough).toBe(false);
  });

  it("does not let the caller's own speech raise the echo baseline", () => {
    // Otherwise a caller who keeps talking trains the gate to ignore them.
    const before = trained(0.05);
    const after = nextEchoGate(before, { level: 0.9, assistantPlaying: true, now: 1_000 });

    expect(after.state.echoLevel).toBe(before.echoLevel);
  });

  it("resets once the reply finishes, so each reply is judged afresh", () => {
    const during = trained(0.3);
    const after = nextEchoGate(during, { level: 0.01, assistantPlaying: false, now: 5_000 });

    expect(after.state).toEqual(ECHO_GATE_IDLE);
  });

  it("still gates a room quiet enough that the learned level is negligible", () => {
    // With headphones the learned echo is ~0, so the absolute floor is what
    // stops faint room noise being forwarded as if it were speech.
    const { sent } = feed(trained(0.0001), 0.005, 20);

    expect(sent).toBe(0);
  });
});

describe("interpretCloseCode", () => {
  it("treats 4401 (invalid ticket) as an auth failure", () => {
    expect(interpretCloseCode(4401).kind).toBe("auth");
  });

  it("treats 1008 (missing ticket, FastAPI's own validation) as an auth failure", () => {
    expect(interpretCloseCode(1008).kind).toBe("auth");
  });

  it("treats a normal close code as a normal end of call", () => {
    expect(interpretCloseCode(1000).kind).toBe("normal");
    expect(interpretCloseCode(1001).kind).toBe("normal");
  });
});

describe("describeInputLevel", () => {
  // The levels below are the ones real calls actually produced, out of
  // 32768, so the bands are pinned to the failures they exist to catch.
  const asPeak = (int16Peak: number) => int16Peak / 32768;

  it("calls a dead microphone silent rather than quiet", () => {
    // A muted or wrong device needs a different instruction from one that
    // is merely turned down, so these are separate bands.
    expect(describeInputLevel(asPeak(0)).band).toBe("silent");
    expect(describeInputLevel(asPeak(80)).band).toBe("silent");
  });

  it("flags the level that made a whole call silent", () => {
    // p90 of 422 on a real call: the voice-activity detector never reported
    // speech once and the transcriber returned no words at all.
    expect(describeInputLevel(asPeak(422)).band).toBe("quiet");
    expect(describeInputLevel(asPeak(977)).band).toBe("quiet");
  });

  it("flags the clipping that causes the silence on the next call", () => {
    // p90 of 32,555 out of 32,768. The browser's gain control clamps down
    // hard afterwards, and the following session starts near-silent - which
    // is why the loud end has to be called out too, not just the quiet end.
    expect(describeInputLevel(asPeak(32555)).band).toBe("clipping");
    expect(describeInputLevel(asPeak(32767)).band).toBe("clipping");
  });

  it("accepts ordinary speech", () => {
    // -20 to -8 dBFS, which is where a correctly set microphone sits.
    expect(describeInputLevel(asPeak(3300)).band).toBe("good");
    expect(describeInputLevel(asPeak(6500)).band).toBe("good");
    expect(describeInputLevel(asPeak(13000)).band).toBe("good");
  });

  it("reports dBFS, so the screen and the server logs agree", () => {
    // The logs report "peak=N (x.xxx of full scale)" in dBFS terms; an
    // operator reading one and an engineer reading the other have to be
    // talking about the same number.
    expect(describeInputLevel(0.5).dbfs).toBeCloseTo(-6.02, 1);
    expect(describeInputLevel(1).dbfs).toBeCloseTo(0, 5);
  });

  it("never says a helpful-sounding nothing", () => {
    for (const peak of [0, 0.001, 0.01, 0.1, 0.5, 1]) {
      expect(describeInputLevel(peak).label.length).toBeGreaterThan(3);
    }
  });
});

describe("peakOf", () => {
  it("finds the loudest sample regardless of sign", () => {
    expect(peakOf(new Float32Array([0.1, -0.8, 0.3]))).toBeCloseTo(0.8);
  });

  it("is zero for silence and for nothing", () => {
    expect(peakOf(new Float32Array([0, 0, 0]))).toBe(0);
    expect(peakOf(new Float32Array())).toBe(0);
  });
});
