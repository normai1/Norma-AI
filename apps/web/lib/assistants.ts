import { authorizedJson } from "./auth";

export interface Assistant {
  id: string;
  organization_id: string;
  workspace_id: string;
  name: string;
  status: string;
  current_version_id: string | null;
  created_at: string;
}

export interface AssistantVersion {
  id: string;
  assistant_id: string;
  version: number;
  voice_id: string;
  language: string;
  greeting: string;
  persona: string | null;
  speech_rate: number;
  turn_sensitivity: number;
  creativity: number;
  ambient_sound: string | null;
  created_at: string;
}

export interface AssistantVersionInput {
  voice_id: string;
  language: string;
  greeting: string;
  persona: string | null;
  speech_rate: number;
  turn_sensitivity: number;
  creativity: number;
  ambient_sound: string | null;
}

export interface AssistantVersionFieldDiff {
  previous: unknown;
  current: unknown;
}

export interface AssistantVersionDiff {
  from_version: number;
  to_version: number;
  changes: Record<string, AssistantVersionFieldDiff>;
}

export interface TestCallTicket {
  ticket: string;
  expires_in: number;
}

function assistantsUrl(organizationId: string, workspaceId: string): string {
  return `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}/assistants`;
}

function assistantUrl(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): string {
  return `${assistantsUrl(organizationId, workspaceId)}/${assistantId}`;
}

export async function listAssistants(
  organizationId: string,
  workspaceId: string,
): Promise<Assistant[]> {
  return authorizedJson<Assistant[]>(assistantsUrl(organizationId, workspaceId));
}

export async function getAssistant(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): Promise<Assistant> {
  return authorizedJson<Assistant>(
    assistantUrl(organizationId, workspaceId, assistantId),
  );
}

export async function createAssistant(
  organizationId: string,
  workspaceId: string,
  name: string,
): Promise<Assistant> {
  return authorizedJson<Assistant>(assistantsUrl(organizationId, workspaceId), {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function renameAssistant(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  name: string,
): Promise<Assistant> {
  return authorizedJson<Assistant>(
    assistantUrl(organizationId, workspaceId, assistantId),
    { method: "PATCH", body: JSON.stringify({ name }) },
  );
}

export async function archiveAssistant(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): Promise<Assistant> {
  return authorizedJson<Assistant>(
    `${assistantUrl(organizationId, workspaceId, assistantId)}/archive`,
    { method: "POST" },
  );
}

export async function publishAssistant(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  version: number,
): Promise<Assistant> {
  return authorizedJson<Assistant>(
    `${assistantUrl(organizationId, workspaceId, assistantId)}/publish`,
    { method: "POST", body: JSON.stringify({ version }) },
  );
}

export async function listAssistantVersions(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): Promise<AssistantVersion[]> {
  return authorizedJson<AssistantVersion[]>(
    `${assistantUrl(organizationId, workspaceId, assistantId)}/versions`,
  );
}

export async function createAssistantVersion(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  input: AssistantVersionInput,
): Promise<AssistantVersion> {
  return authorizedJson<AssistantVersion>(
    `${assistantUrl(organizationId, workspaceId, assistantId)}/versions`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function fetchTestCallTicket(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): Promise<TestCallTicket> {
  return authorizedJson<TestCallTicket>(
    `${assistantUrl(organizationId, workspaceId, assistantId)}/test-call-token`,
    { method: "POST" },
  );
}

export async function diffAssistantVersions(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  fromVersion: number,
  toVersion: number,
): Promise<AssistantVersionDiff> {
  return authorizedJson<AssistantVersionDiff>(
    `${assistantUrl(organizationId, workspaceId, assistantId)}/versions/` +
      `${fromVersion}/diff/${toVersion}`,
  );
}
