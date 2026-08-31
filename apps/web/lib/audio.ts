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
