import { describe, expect, it } from "vitest";

import { canManage } from "./organizations";

describe("canManage", () => {
  it.each(["owner", "admin"] as const)("returns true for %s", (role) => {
    expect(canManage(role)).toBe(true);
  });

  it.each(["member", "viewer"] as const)("returns false for %s", (role) => {
    expect(canManage(role)).toBe(false);
  });
});
