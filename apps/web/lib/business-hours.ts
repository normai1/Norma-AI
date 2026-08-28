import { BUSINESS_HOURS_DAYS, type BusinessHoursDay, type WorkspaceSettings } from "./workspaces";

export interface DayRowState {
  open: boolean;
  start: string;
  end: string;
}

export type BusinessHoursForm = Record<BusinessHoursDay, DayRowState>;

export function emptyBusinessHoursForm(): BusinessHoursForm {
  const form = {} as BusinessHoursForm;

  for (const day of BUSINESS_HOURS_DAYS) {
    form[day] = { open: false, start: "09:00", end: "17:00" };
  }

  return form;
}

export function businessHoursFromApi(
  apiHours: WorkspaceSettings["business_hours"],
): BusinessHoursForm {
  const form = emptyBusinessHoursForm();

  if (!apiHours) {
    return form;
  }

  for (const day of BUSINESS_HOURS_DAYS) {
    const window = apiHours[day];

    if (window) {
      form[day] = { open: true, start: window.open, end: window.close };
    }
  }

  return form;
}

export function businessHoursToApi(
  form: BusinessHoursForm,
): WorkspaceSettings["business_hours"] {
  const result: WorkspaceSettings["business_hours"] = {};

  for (const day of BUSINESS_HOURS_DAYS) {
    const row = form[day];

    result[day] = row.open ? { open: row.start, close: row.end } : null;
  }

  return result;
}
