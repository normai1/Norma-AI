import { authorizedJson, authorizedSend } from "./auth";

export type KnowledgeSourceType = "file" | "website" | "manual_faq";
export type KnowledgeSourceStatus =
  | "pending"
  | "processing"
  | "completed"
  | "failed";

export interface KnowledgeSourceDocument {
  id: string;
  filename: string;
  content_type: string;
  processing_status: string;
  processing_error: string | null;
  created_at: string;
}

export interface KnowledgeSourceCrawledPage {
  id: string;
  url: string;
  fetched_at: string;
  content_hash: string;
}

export interface KnowledgeSource {
  id: string;
  organization_id: string;
  workspace_id: string;
  assistant_id: string | null;
  type: KnowledgeSourceType;
  status: KnowledgeSourceStatus;
  error_message: string | null;
  owner_user_id: string | null;
  source_url: string | null;
  name: string | null;
  created_at: string;
  document: KnowledgeSourceDocument | null;
  crawled_pages: KnowledgeSourceCrawledPage[] | null;
}

export interface FaqEntry {
  id: string;
  knowledge_source_id: string;
  question: string;
  answer: string;
  created_at: string;
}

function knowledgeSourcesUrl(organizationId: string, workspaceId: string): string {
  return `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}/knowledge-sources`;
}

function knowledgeSourceUrl(
  organizationId: string,
  workspaceId: string,
  knowledgeSourceId: string,
): string {
  return `${knowledgeSourcesUrl(organizationId, workspaceId)}/${knowledgeSourceId}`;
}

function faqEntriesUrl(
  organizationId: string,
  workspaceId: string,
  knowledgeSourceId: string,
): string {
  return `${knowledgeSourceUrl(organizationId, workspaceId, knowledgeSourceId)}/faq-entries`;
}

export async function listKnowledgeSources(
  organizationId: string,
  workspaceId: string,
): Promise<KnowledgeSource[]> {
  return authorizedJson<KnowledgeSource[]>(
    knowledgeSourcesUrl(organizationId, workspaceId),
  );
}

export async function uploadKnowledgeSourceFile(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  file: File,
): Promise<KnowledgeSource> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("assistant_id", assistantId);

  const response = await authorizedSend(
    knowledgeSourcesUrl(organizationId, workspaceId),
    { method: "POST", body: formData },
  );

  return response.json() as Promise<KnowledgeSource>;
}

export async function createWebsiteKnowledgeSource(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  url: string,
): Promise<KnowledgeSource> {
  return authorizedJson<KnowledgeSource>(
    `${knowledgeSourcesUrl(organizationId, workspaceId)}/website`,
    { method: "POST", body: JSON.stringify({ url, assistant_id: assistantId }) },
  );
}

export async function createManualFaqKnowledgeSource(
  organizationId: string,
  workspaceId: string,
  assistantId: string,
  name: string,
): Promise<KnowledgeSource> {
  return authorizedJson<KnowledgeSource>(
    `${knowledgeSourcesUrl(organizationId, workspaceId)}/manual-faq`,
    { method: "POST", body: JSON.stringify({ name, assistant_id: assistantId }) },
  );
}

export async function processKnowledgeSource(
  organizationId: string,
  workspaceId: string,
  knowledgeSourceId: string,
): Promise<KnowledgeSource> {
  return authorizedJson<KnowledgeSource>(
    `${knowledgeSourceUrl(organizationId, workspaceId, knowledgeSourceId)}/process`,
    { method: "POST" },
  );
}

export async function recrawlKnowledgeSource(
  organizationId: string,
  workspaceId: string,
  knowledgeSourceId: string,
): Promise<KnowledgeSource> {
  return authorizedJson<KnowledgeSource>(
    `${knowledgeSourceUrl(organizationId, workspaceId, knowledgeSourceId)}/recrawl`,
    { method: "POST" },
  );
}

export async function listFaqEntries(
  organizationId: string,
  workspaceId: string,
  knowledgeSourceId: string,
): Promise<FaqEntry[]> {
  return authorizedJson<FaqEntry[]>(
    faqEntriesUrl(organizationId, workspaceId, knowledgeSourceId),
  );
}

export async function createFaqEntry(
  organizationId: string,
  workspaceId: string,
  knowledgeSourceId: string,
  input: { question: string; answer: string },
): Promise<FaqEntry> {
  return authorizedJson<FaqEntry>(
    faqEntriesUrl(organizationId, workspaceId, knowledgeSourceId),
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function updateFaqEntry(
  organizationId: string,
  workspaceId: string,
  knowledgeSourceId: string,
  faqEntryId: string,
  input: { question?: string; answer?: string },
): Promise<FaqEntry> {
  return authorizedJson<FaqEntry>(
    `${faqEntriesUrl(organizationId, workspaceId, knowledgeSourceId)}/${faqEntryId}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
}

export async function deleteFaqEntry(
  organizationId: string,
  workspaceId: string,
  knowledgeSourceId: string,
  faqEntryId: string,
): Promise<void> {
  await authorizedSend(
    `${faqEntriesUrl(organizationId, workspaceId, knowledgeSourceId)}/${faqEntryId}`,
    { method: "DELETE" },
  );
}

const SOURCE_TYPE_LABELS: Record<KnowledgeSourceType, string> = {
  file: "File",
  website: "Website",
  manual_faq: "Manual FAQ",
};

export function knowledgeSourceTypeLabel(type: KnowledgeSourceType): string {
  return SOURCE_TYPE_LABELS[type];
}

export function knowledgeSourceDisplayName(source: KnowledgeSource): string {
  return (
    source.name ??
    source.document?.filename ??
    source.source_url ??
    "Untitled source"
  );
}

/** A failed file source can be retried; nothing else can. */
export function canRetryKnowledgeSource(source: KnowledgeSource): boolean {
  return source.type === "file" && source.status === "failed";
}

/** Any website source can be recrawled, regardless of its current status. */
export function canRecrawlKnowledgeSource(source: KnowledgeSource): boolean {
  return source.type === "website";
}
