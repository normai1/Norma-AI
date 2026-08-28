import { authorizedEmpty, authorizedJson } from "./auth";

export type OrganizationRole = "owner" | "admin" | "member" | "viewer";

// Mirrors SUPPORTED_CURRENCIES in apps/api/app/schemas/settings.py.
export const SUPPORTED_CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD"] as const;

export interface OrganizationSettings {
  currency: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  settings: OrganizationSettings;
  status: string;
  created_at: string;
  role: OrganizationRole;
}

export interface MemberUser {
  id: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
}

export interface Member {
  id: string;
  role: OrganizationRole;
  created_at: string;
  user: MemberUser;
}

export interface Invitation {
  id: string;
  organization_id: string;
  email: string;
  role: OrganizationRole;
  status: string;
  expires_at: string;
  created_at: string;
}

export interface CreatedInvitation extends Invitation {
  /** Returned once, at creation. No email provider is configured yet. */
  token: string;
}

/** Roles that may manage members and invitations. Mirrors the API's OrgAdmin. */
export const MANAGER_ROLES: OrganizationRole[] = ["owner", "admin"];

export function canManage(role: OrganizationRole): boolean {
  return MANAGER_ROLES.includes(role);
}

export async function listOrganizations(): Promise<Organization[]> {
  return authorizedJson<Organization[]>("/api/v1/organizations");
}

export async function getOrganization(id: string): Promise<Organization> {
  return authorizedJson<Organization>(`/api/v1/organizations/${id}`);
}

export async function createOrganization(
  name: string,
): Promise<Organization> {
  return authorizedJson<Organization>("/api/v1/organizations", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function updateOrganization(
  id: string,
  input: { name?: string; settings?: OrganizationSettings },
): Promise<Organization> {
  return authorizedJson<Organization>(`/api/v1/organizations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function listMembers(organizationId: string): Promise<Member[]> {
  return authorizedJson<Member[]>(
    `/api/v1/organizations/${organizationId}/members`,
  );
}

export async function changeMemberRole(
  organizationId: string,
  memberId: string,
  role: OrganizationRole,
): Promise<Member> {
  return authorizedJson<Member>(
    `/api/v1/organizations/${organizationId}/members/${memberId}`,
    { method: "PATCH", body: JSON.stringify({ role }) },
  );
}

export async function removeMember(
  organizationId: string,
  memberId: string,
): Promise<void> {
  await authorizedEmpty(
    `/api/v1/organizations/${organizationId}/members/${memberId}`,
    { method: "DELETE" },
  );
}

export async function listInvitations(
  organizationId: string,
): Promise<Invitation[]> {
  return authorizedJson<Invitation[]>(
    `/api/v1/organizations/${organizationId}/invitations`,
  );
}

export async function inviteMember(
  organizationId: string,
  email: string,
  role: OrganizationRole,
): Promise<CreatedInvitation> {
  return authorizedJson<CreatedInvitation>(
    `/api/v1/organizations/${organizationId}/invitations`,
    { method: "POST", body: JSON.stringify({ email, role }) },
  );
}

export async function revokeInvitation(
  organizationId: string,
  invitationId: string,
): Promise<void> {
  await authorizedEmpty(
    `/api/v1/organizations/${organizationId}/invitations/${invitationId}`,
    { method: "DELETE" },
  );
}
