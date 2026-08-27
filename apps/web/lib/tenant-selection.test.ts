import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getStoredActiveOrganizationId,
  resolveActiveId,
  setStoredActiveOrganizationId,
} from "./tenant-selection";

describe("resolveActiveId", () => {
  it("returns null when the available list is empty", () => {
    expect(resolveActiveId(null, [])).toBeNull();
    expect(resolveActiveId("stale", [])).toBeNull();
  });

  it("falls back to the first available id when nothing is persisted", () => {
    expect(resolveActiveId(null, ["a", "b"])).toBe("a");
  });

  it("falls back to the first available id when the persisted id is stale", () => {
    expect(resolveActiveId("gone", ["a", "b"])).toBe("a");
  });

  it("keeps the persisted id when it is still available", () => {
    expect(resolveActiveId("b", ["a", "b"])).toBe("b");
  });

  it("returns the only id in a single-item list regardless of what was persisted", () => {
    expect(resolveActiveId(null, ["only"])).toBe("only");
    expect(resolveActiveId("stale", ["only"])).toBe("only");
    expect(resolveActiveId("only", ["only"])).toBe("only");
  });
});

describe("storage helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("round-trips a value through localStorage", () => {
    setStoredActiveOrganizationId("org-1");

    expect(getStoredActiveOrganizationId()).toBe("org-1");
  });

  it("removes the key when set to null", () => {
    setStoredActiveOrganizationId("org-1");
    setStoredActiveOrganizationId(null);

    expect(getStoredActiveOrganizationId()).toBeNull();
  });

  it("is a no-op on both read and write when window is undefined", () => {
    vi.stubGlobal("window", undefined);

    expect(() => setStoredActiveOrganizationId("org-1")).not.toThrow();
    expect(getStoredActiveOrganizationId()).toBeNull();
  });
});
