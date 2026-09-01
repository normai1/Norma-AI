import { authorizedJson, authorizedSend } from "./auth";

export interface Assistant {
  id: string;
  organization_id: string;
  workspace_id: string;
  name: string;
  status: string;
  voice_id: string | null;
  language: string | null;
  greeting: string | null;
  persona: string | null;
  custom_prompt: string | null;
  speech_rate: number;
  turn_sensitivity: number;
  creativity: number;
  ambient_sound: string | null;
  ambient_sound_volume: number | null;
  max_call_duration_seconds: number | null;
  max_silence_timeout_seconds: number | null;
  record_calls: boolean;
  auto_delete_on_declined_consent: boolean;
  created_at: string;
}

/**
 * A partial update - every field is optional, and only the ones actually set
 * are sent (and changed). There is no separate version snapshot to create:
 * this edits the one mutable Assistant row directly.
 */
export interface AssistantUpdateInput {
  name?: string;
  voice_id?: string | null;
  language?: string | null;
  greeting?: string | null;
  persona?: string | null;
  custom_prompt?: string | null;
  speech_rate?: number;
  turn_sensitivity?: number;
  creativity?: number;
  ambient_sound?: string | null;
  ambient_sound_volume?: number | null;
  max_call_duration_seconds?: number | null;
  max_silence_timeout_seconds?: number | null;
  record_calls?: boolean;
  auto_delete_on_declined_consent?: boolean;
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

/** Update any subset of an assistant's configuration in place. */
export async function updateAssistant(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  input: AssistantUpdateInput,
): Promise<Assistant> {
  return authorizedJson<Assistant>(
    assistantUrl(organizationId, workspaceId, assistantId),
    { method: "PATCH", body: JSON.stringify(input) },
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

/** Permanently delete an assistant. Irreversible - unlike archiveAssistant. */
export async function deleteAssistant(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): Promise<void> {
  await authorizedSend(assistantUrl(organizationId, workspaceId, assistantId), {
    method: "DELETE",
  });
}

/** Mark an assistant's current configuration as live. */
export async function publishAssistant(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): Promise<Assistant> {
  return authorizedJson<Assistant>(
    `${assistantUrl(organizationId, workspaceId, assistantId)}/publish`,
    { method: "POST" },
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
