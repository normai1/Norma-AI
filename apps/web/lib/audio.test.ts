import { describe, expect, it } from "vitest";

import {
  floatToPCM16,
  interpretCloseCode,
  pcm16ToFloat32,
  resampleLinear,
} from "./audio";

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
