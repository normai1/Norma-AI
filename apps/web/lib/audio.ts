/**
 * Pure helpers for the browser test-call page (build-plan item 21b). Kept
 * free of any DOM/WebSocket/AudioContext dependency so they can be unit
 * tested directly - see audio.test.ts.
 */

/** Converts Float32 samples in [-1, 1] to 16-bit PCM, clamping out-of-range values. */
export function floatToPCM16(samples: Float32Array<ArrayBuffer>): ArrayBuffer {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);

  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    const int16 = clamped < 0 ? clamped * 32768 : clamped * 32767;

    view.setInt16(i * 2, Math.round(int16), true);
  }

  return buffer;
}

/** Converts a 16-bit PCM buffer back to Float32 samples in [-1, 1]. */
export function pcm16ToFloat32(buffer: ArrayBuffer): Float32Array<ArrayBuffer> {
  const view = new DataView(buffer);
  const sampleCount = buffer.byteLength / 2;
  const samples = new Float32Array(sampleCount);

  for (let i = 0; i < sampleCount; i++) {
    const int16 = view.getInt16(i * 2, true);

    samples[i] = int16 < 0 ? int16 / 32768 : int16 / 32767;
  }

  return samples;
}

/**
 * Linear resample, used only as a fallback for the rare browser that ignores
 * the AudioContext's requested sampleRate (see this feature's spec) - the
 * primary path never calls this.
 */
export function resampleLinear(
  samples: Float32Array<ArrayBuffer>,
  fromRate: number,
  toRate: number,
): Float32Array<ArrayBuffer> {
  if (fromRate === toRate || samples.length === 0) {
    return samples;
  }

  const ratio = fromRate / toRate;
  const outputLength = Math.round(samples.length / ratio);
  const output = new Float32Array(outputLength);

  for (let i = 0; i < outputLength; i++) {
    const sourceIndex = i * ratio;
    const lower = Math.floor(sourceIndex);
    const upper = Math.min(lower + 1, samples.length - 1);
    const fraction = sourceIndex - lower;

    output[i] = samples[lower] + (samples[upper] - samples[lower]) * fraction;
  }

  return output;
}

/** Root-mean-square level of a frame, the loudness measure the gate below compares. */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) {
    return 0;
  }

  let total = 0;

  for (let i = 0; i < samples.length; i++) {
    total += samples[i] * samples[i];
  }

  return Math.sqrt(total / samples.length);
}

/**
 * How much louder than the assistant's own echo a frame must be to count as
 * the caller genuinely speaking. Speech into the machine's own microphone is
 * far louder than the same machine's speakers heard back across a room.
 */
export const ECHO_GATE_SPEECH_FACTOR = 2.5;

/** Absolute floor, so room noise during playback can never open the gate. */
export const ECHO_GATE_MIN_SPEECH_RMS = 0.02;

/**
 * Once speech is detected the gate stays open this long, so a normal pause
 * between words does not chop the caller's sentence into fragments - and so
 * their own speech never trains the echo baseline below.
 */
export const ECHO_GATE_HOLD_MS = 600;

/**
 * Frames spent learning how loud this room's echo actually is before judging
 * anything against it. At ~20ms a frame this is a fraction of a second at the
 * start of each reply, during which nothing is forwarded.
 */
export const ECHO_GATE_LEARN_FRAMES = 10;

export interface EchoGateState {
  /** Running estimate of the echo's level while the assistant is playing. */
  echoLevel: number;
  /** performance.now() timestamp the gate stays open until. */
  openUntil: number;
  /** How many frames of this playback have gone into the estimate. */
  learnedFrames: number;
}

export const ECHO_GATE_IDLE: EchoGateState = {
  echoLevel: 0,
  openUntil: 0,
  learnedFrames: 0,
};

/**
 * Decides whether one microphone frame should be sent to the server or
 * replaced with silence, while the assistant's own reply is playing out of
 * the same machine's speakers.
 *
 * Without this the assistant hears itself: its playback returns through an
 * open microphone, gets transcribed, becomes a "turn", and it answers its own
 * echo - a feedback loop measured on a live session generating 27+ seconds of
 * speech from one short question, with the caller's real interruption
 * indistinguishable among dozens of self-triggered ones. Gating the input is
 * what breaks that loop; barge-in cannot, because it sits downstream of the
 * corrupted audio.
 *
 * Only ever gates while the assistant is actually playing. When it is silent
 * every frame passes untouched, so the ordinary path - the caller asking the
 * question in the first place - is never affected by any of this.
 */
export function nextEchoGate(
  state: EchoGateState,
  { level, assistantPlaying, now }: { level: number; assistantPlaying: boolean; now: number },
): { passThrough: boolean; state: EchoGateState } {
  if (!assistantPlaying) {
    return { passThrough: true, state: ECHO_GATE_IDLE };
  }

  if (state.learnedFrames < ECHO_GATE_LEARN_FRAMES) {
    // Peak-tracking rather than averaging: the estimate has to cover the
    // echo's loud moments, or those alone would later read as speech.
    return {
      passThrough: false,
      state: {
        echoLevel: Math.max(state.echoLevel, level),
        openUntil: 0,
        learnedFrames: state.learnedFrames + 1,
      },
    };
  }

  if (now < state.openUntil) {
    return { passThrough: true, state };
  }

  const threshold = Math.max(
    state.echoLevel * ECHO_GATE_SPEECH_FACTOR,
    ECHO_GATE_MIN_SPEECH_RMS,
  );

  if (level > threshold) {
    return { passThrough: true, state: { ...state, openUntil: now + ECHO_GATE_HOLD_MS } };
  }

  // Echo, so it may keep shaping the baseline - slowly, and never from
  // anything already judged to be speech.
  return {
    passThrough: false,
    state: { ...state, echoLevel: state.echoLevel * 0.98 + level * 0.02 },
  };
}

export type CloseReasonKind = "auth" | "normal";

export interface CloseReason {
  kind: CloseReasonKind;
  message: string;
}

// 4401: this feature's own /media/session rejection for an invalid ticket
// (item 21a). 1008 ("Policy Violation"): FastAPI's own close code when the
// required `ticket` query parameter is missing entirely, before app code
// ever runs. Both mean the same thing from the browser's point of view.
const _AUTH_CLOSE_CODES = new Set([4401, 1008]);

/** Distinguishes "this test call couldn't be authorized" from a normal end of call. */
export function interpretCloseCode(code: number): CloseReason {
  if (_AUTH_CLOSE_CODES.has(code)) {
    return {
      kind: "auth",
      message: "This test call couldn't be authorized. Try starting it again.",
    };
  }

  return { kind: "normal", message: "The test call ended." };
}

/**
 * How a microphone's input level should be described to the operator.
 *
 * "silent" and "quiet" are separate on purpose: a muted or wrong device
 * reads as nothing at all, which needs a different instruction from a mic
 * that is working but turned down.
 */
export type InputLevelBand = "silent" | "quiet" | "good" | "loud" | "clipping";

export interface InputLevel {
  /** Peak of the most recent frames, 0..1 of full scale. */
  peak: number;
  /** The same as dBFS, which is what the server logs report. */
  dbfs: number;
  band: InputLevelBand;
  label: string;
}

/**
 * Bands taken from real calls on this deployment rather than from a
 * reference level.
 *
 * Four sessions in one morning alternated between a p90 of 32,555 out of
 * 32,768 - clipping at full scale - and 422, which is near-silence. At the
 * quiet end neither the voice-activity detector nor the transcriber could
 * find speech at all, and the call was silent with nothing on screen to say
 * why. The loud end is what causes it: the browser's automatic gain control
 * clamps down hard after clipping, and the next session starts from the
 * clamped gain.
 *
 * So the meter has to call out both ends, not just silence.
 */
export function describeInputLevel(peak: number): InputLevel {
  const dbfs = peak > 0 ? 20 * Math.log10(peak) : -Infinity;

  if (peak < 0.004) {
    return {
      peak,
      dbfs,
      band: "silent",
      label: "No sound from the microphone",
    };
  }

  if (dbfs < -30) {
    return {
      peak,
      dbfs,
      band: "quiet",
      label: "Too quiet - turn your microphone level up",
    };
  }

  if (dbfs > -1.5) {
    return {
      peak,
      dbfs,
      band: "clipping",
      label: "Clipping - turn your microphone level down",
    };
  }

  if (dbfs > -6) {
    return { peak, dbfs, band: "loud", label: "A little hot" };
  }

  return { peak, dbfs, band: "good", label: "Good" };
}

/** Peak magnitude of a frame, 0..1 of full scale. */
export function peakOf(samples: Float32Array): number {
  let peak = 0;

  for (let i = 0; i < samples.length; i++) {
    const magnitude = samples[i] < 0 ? -samples[i] : samples[i];

    if (magnitude > peak) {
      peak = magnitude;
    }
  }

  return peak;
}
