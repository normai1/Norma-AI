import { authorizedJson } from "./auth";

export interface PromptTemplate {
  id: string;
  organization_id: string;
  workspace_id: string;
  name: string;
  use_case: string;
  status: string;
  current_version_id: string | null;
  created_at: string;
}

export interface PromptTemplateVersion {
  id: string;
  prompt_template_id: string;
  version: number;
  content: string;
  created_at: string;
}

export interface PromptTemplateVersionFieldDiff {
  previous: unknown;
  current: unknown;
}

export interface PromptTemplateVersionDiff {
  from_version: number;
  to_version: number;
  changes: Record<string, PromptTemplateVersionFieldDiff>;
}

function promptTemplatesUrl(organizationId: string, workspaceId: string): string {
  return `/api/v1/organizations/${organizationId}/workspaces/${workspaceId}/prompt-templates`;
}

function promptTemplateUrl(
  organizationId: string,
  workspaceId: string,
  promptTemplateId: string,
): string {
  return `${promptTemplatesUrl(organizationId, workspaceId)}/${promptTemplateId}`;
}

export async function listPromptTemplates(
  organizationId: string,
  workspaceId: string,
): Promise<PromptTemplate[]> {
  return authorizedJson<PromptTemplate[]>(
    promptTemplatesUrl(organizationId, workspaceId),
  );
}

export async function getPromptTemplate(
  organizationId: string,
  workspaceId: string,
  promptTemplateId: string,
): Promise<PromptTemplate> {
  return authorizedJson<PromptTemplate>(
    promptTemplateUrl(organizationId, workspaceId, promptTemplateId),
  );
}

export async function createPromptTemplate(
  organizationId: string,
  workspaceId: string,
  name: string,
  useCase: string,
): Promise<PromptTemplate> {
  return authorizedJson<PromptTemplate>(
    promptTemplatesUrl(organizationId, workspaceId),
    {
      method: "POST",
      body: JSON.stringify({ name, use_case: useCase }),
    },
  );
}

export async function renamePromptTemplate(
  organizationId: string,
  workspaceId: string,
  promptTemplateId: string,
  name: string,
): Promise<PromptTemplate> {
  return authorizedJson<PromptTemplate>(
    promptTemplateUrl(organizationId, workspaceId, promptTemplateId),
    { method: "PATCH", body: JSON.stringify({ name }) },
  );
}

export async function archivePromptTemplate(
  organizationId: string,
  workspaceId: string,
  promptTemplateId: string,
): Promise<PromptTemplate> {
  return authorizedJson<PromptTemplate>(
    `${promptTemplateUrl(organizationId, workspaceId, promptTemplateId)}/archive`,
    { method: "POST" },
  );
}

export async function publishPromptTemplate(
  organizationId: string,
  workspaceId: string,
  promptTemplateId: string,
  version: number,
): Promise<PromptTemplate> {
  return authorizedJson<PromptTemplate>(
    `${promptTemplateUrl(organizationId, workspaceId, promptTemplateId)}/publish`,
    { method: "POST", body: JSON.stringify({ version }) },
  );
}

export async function listPromptTemplateVersions(
  organizationId: string,
  workspaceId: string,
  promptTemplateId: string,
): Promise<PromptTemplateVersion[]> {
  return authorizedJson<PromptTemplateVersion[]>(
    `${promptTemplateUrl(organizationId, workspaceId, promptTemplateId)}/versions`,
  );
}

export async function createPromptTemplateVersion(
  organizationId: string,
  workspaceId: string,
  promptTemplateId: string,
  content: string,
): Promise<PromptTemplateVersion> {
  return authorizedJson<PromptTemplateVersion>(
    `${promptTemplateUrl(organizationId, workspaceId, promptTemplateId)}/versions`,
    { method: "POST", body: JSON.stringify({ content }) },
  );
}

export async function diffPromptTemplateVersions(
  organizationId: string,
  workspaceId: string,
  promptTemplateId: string,
  fromVersion: number,
  toVersion: number,
): Promise<PromptTemplateVersionDiff> {
  const base = promptTemplateUrl(organizationId, workspaceId, promptTemplateId);

  return authorizedJson<PromptTemplateVersionDiff>(
    `${base}/versions/${fromVersion}/diff/${toVersion}`,
  );
}
