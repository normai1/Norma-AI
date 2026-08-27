import { authorizedEmpty, authorizedJson } from "./auth";
import type { MemberUser } from "./organizations";

export interface Workspace {
  id: string;
  organization_id: string;
  name: string;
  settings: Record<string, unknown>;
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
