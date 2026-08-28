import { describe, expect, it } from "vitest";

import {
  businessHoursFromApi,
  businessHoursToApi,
  emptyBusinessHoursForm,
} from "./business-hours";

describe("emptyBusinessHoursForm", () => {
  it("returns all seven days closed with sensible default times", () => {
    const form = emptyBusinessHoursForm();

    expect(Object.keys(form)).toHaveLength(7);
    expect(form.monday).toEqual({ open: false, start: "09:00", end: "17:00" });
    expect(form.sunday).toEqual({ open: false, start: "09:00", end: "17:00" });
  });
});

describe("businessHoursFromApi", () => {
  it("returns an all-closed form when the API value is null", () => {
    const form = businessHoursFromApi(null);

    expect(Object.values(form).every((row) => row.open === false)).toBe(true);
  });

  it("marks a day open with its stored times when present", () => {
    const form = businessHoursFromApi({
      monday: { open: "09:00", close: "17:00" },
    });

    expect(form.monday).toEqual({ open: true, start: "09:00", end: "17:00" });
  });

  it("leaves a day closed when its value is explicitly null", () => {
    const form = businessHoursFromApi({ sunday: null });

    expect(form.sunday.open).toBe(false);
  });

  it("leaves a day closed when it is simply absent from the API value", () => {
    const form = businessHoursFromApi({
      monday: { open: "09:00", close: "17:00" },
    });

    expect(form.tuesday.open).toBe(false);
  });
});

describe("businessHoursToApi", () => {
  it("always emits all seven day keys", () => {
    const api = businessHoursToApi(emptyBusinessHoursForm());

    expect(Object.keys(api ?? {}).sort()).toEqual(
      [
        "friday",
        "monday",
        "saturday",
        "sunday",
        "thursday",
        "tuesday",
        "wednesday",
      ].sort(),
    );
  });

  it("emits null for a closed day and the window for an open day", () => {
    const form = emptyBusinessHoursForm();
    form.monday = { open: true, start: "08:00", end: "16:00" };

    const api = businessHoursToApi(form);

    expect(api?.monday).toEqual({ open: "08:00", close: "16:00" });
    expect(api?.tuesday).toBeNull();
  });
});

describe("businessHoursFromApi / businessHoursToApi round-trip", () => {
  it("preserves an open day and a closed day through both directions", () => {
    const form = emptyBusinessHoursForm();
    form.monday = { open: true, start: "09:00", end: "17:00" };

    const roundTripped = businessHoursFromApi(businessHoursToApi(form));

    expect(roundTripped.monday).toEqual(form.monday);
    expect(roundTripped.sunday.open).toBe(false);
  });
});
