import { authorizedEmpty, authorizedJson } from "./auth";

export interface GlossaryEntry {
  id: string;
  organization_id: string;
  workspace_id: string;
  assistant_id: string;
  term: string;
  meaning: string | null;
  phonetic_spelling: string | null;
  stt_boost_weight: number;
  created_at: string;
}

export interface GlossaryEntryInput {
  term: string;
  meaning: string | null;
  phonetic_spelling: string | null;
  stt_boost_weight: number;
}

function glossaryUrl(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): string {
  const base = `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}`;

  return `${base}/assistants/${assistantId}/glossary`;
}

export async function listGlossaryEntries(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
): Promise<GlossaryEntry[]> {
  return authorizedJson<GlossaryEntry[]>(
    glossaryUrl(organizationId, workspaceId, assistantId),
  );
}

export async function createGlossaryEntry(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  input: GlossaryEntryInput,
): Promise<GlossaryEntry> {
  return authorizedJson<GlossaryEntry>(
    glossaryUrl(organizationId, workspaceId, assistantId),
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function updateGlossaryEntry(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  glossaryEntryId: string,
  input: Partial<GlossaryEntryInput>,
): Promise<GlossaryEntry> {
  return authorizedJson<GlossaryEntry>(
    `${glossaryUrl(organizationId, workspaceId, assistantId)}/${glossaryEntryId}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
}

export async function deleteGlossaryEntry(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  glossaryEntryId: string,
): Promise<void> {
  await authorizedEmpty(
    `${glossaryUrl(organizationId, workspaceId, assistantId)}/${glossaryEntryId}`,
    { method: "DELETE" },
  );
}
