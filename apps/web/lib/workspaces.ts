import { authorizedEmpty, authorizedJson } from "./auth";
import type { MemberUser } from "./organizations";

export interface BusinessHoursWindow {
  open: string;
  close: string;
}

export type BusinessHoursDay =
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday"
  | "sunday";

export const BUSINESS_HOURS_DAYS: readonly BusinessHoursDay[] = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
];

// A curated subset - the backend validates against the full IANA/zoneinfo
// set, this list only bounds what the picker offers.
export const COMMON_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Toronto",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Africa/Johannesburg",
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
] as const;

export const COMMON_LOCALES = [
  "en-US",
  "en-GB",
  "fr-CA",
  "fr-FR",
  "es-ES",
  "es-MX",
  "de-DE",
  "pt-BR",
  "hi-IN",
] as const;

export interface WorkspaceSettings {
  timezone: string;
  locale: string;
  business_hours: Partial<Record<BusinessHoursDay, BusinessHoursWindow | null>> | null;
}

export interface Workspace {
  id: string;
  organization_id: string;
  name: string;
  settings: WorkspaceSettings;
  created_at: string;
}

export interface WorkspaceMember {
  id: string;
  workspace_id: string;
  created_at: string;
  user: MemberUser;
}

export async function listWorkspaces(
  organizationId: string,
): Promise<Workspace[]> {
  return authorizedJson<Workspace[]>(
    `/api/v1/organizations/${organizationId}/workspaces`,
  );
}

export async function createWorkspace(
  organizationId: string,
  name: string,
): Promise<Workspace> {
  return authorizedJson<Workspace>(
    `/api/v1/organizations/${organizationId}/workspaces`,
    { method: "POST", body: JSON.stringify({ name }) },
  );
}

export async function getWorkspace(
  organizationId: string,
  workspaceId: string,
): Promise<Workspace> {
  return authorizedJson<Workspace>(
    `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}`,
  );
}

export async function updateWorkspace(
  organizationId: string,
  workspaceId: string,
  input: { name?: string; settings?: WorkspaceSettings },
): Promise<Workspace> {
  return authorizedJson<Workspace>(
    `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
}

export async function listWorkspaceMembers(
  organizationId: string,
  workspaceId: string,
): Promise<WorkspaceMember[]> {
  return authorizedJson<WorkspaceMember[]>(
    `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}/members`,
  );
}

export async function addWorkspaceMember(
  organizationId: string,
  workspaceId: string,
  memberId: string,
): Promise<WorkspaceMember> {
  return authorizedJson<WorkspaceMember>(
    `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}/members`,
    { method: "POST", body: JSON.stringify({ member_id: memberId }) },
  );
}

export async function removeWorkspaceMember(
  organizationId: string,
  workspaceId: string,
  workspaceMemberId: string,
): Promise<void> {
  await authorizedEmpty(
    `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}/members/${workspaceMemberId}`,
    { method: "DELETE" },
  );
}
