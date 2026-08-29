"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { useTenant } from "@/components/app/tenant-provider";
import {
  Button,
  Card,
  EmptyState,
  ErrorText,
  LoadingState,
  PageShell,
} from "@/components/organizations/ui";
import {
  archivePromptTemplate,
  createPromptTemplateVersion,
  diffPromptTemplateVersions,
  getPromptTemplate,
  listPromptTemplateVersions,
  publishPromptTemplate,
  renamePromptTemplate,
  type PromptTemplate,
  type PromptTemplateVersion,
  type PromptTemplateVersionDiff,
} from "@/lib/prompt-templates";

function formatDiffValue(value: unknown): string {
  return value === null || value === undefined ? "(none)" : String(value);
}

const STATUS_TONE: Record<string, string> = {
  draft: "border-slate-700 text-slate-400",
  published: "border-green-800 text-green-300",
  archived: "border-slate-800 text-slate-500",
};

function PromptTemplateStatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_TONE[status] ?? "border-slate-700 text-slate-400"}`}
    >
      {status}
    </span>
  );
}

export default function PromptTemplateEditorPage() {
  const params = useParams<{ promptTemplateId: string }>();
  const promptTemplateId = params.promptTemplateId;

  const {
    status: tenantStatus,
    error: tenantError,
    activeWorkspace,
  } = useTenant();

  const [promptTemplate, setPromptTemplate] = useState<PromptTemplate | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [content, setContent] = useState("");
  const [savingVersion, setSavingVersion] = useState(false);
  const [versionError, setVersionError] = useState<string | null>(null);
  const [versionSaved, setVersionSaved] = useState<PromptTemplateVersion | null>(
    null,
  );

  const [versions, setVersions] = useState<PromptTemplateVersion[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [publishingVersion, setPublishingVersion] = useState<number | null>(
    null,
  );
  const [publishError, setPublishError] = useState<string | null>(null);

  const [diffFrom, setDiffFrom] = useState("");
  const [diffTo, setDiffTo] = useState("");
  const [diffResult, setDiffResult] = useState<PromptTemplateVersionDiff | null>(
    null,
  );
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const fetchVersions = useCallback(async () => {
    if (!activeWorkspace) {
      return null;
    }

    return listPromptTemplateVersions(
      activeWorkspace.organization_id,
      activeWorkspace.id,
      promptTemplateId,
    );
  }, [activeWorkspace, promptTemplateId]);

  const applyVersions = useCallback(
    (loaded: PromptTemplateVersion[] | null) => {
      if (loaded) {
        setVersions(loaded);
      }
    },
    [],
  );

  const applyVersionsError = useCallback((err: unknown) => {
    setVersionsError(
      err instanceof Error ? err.message : "Could not load version history.",
    );
  }, []);

  const loadVersions = useCallback(async () => {
    try {
      applyVersions(await fetchVersions());
    } catch (err) {
      applyVersionsError(err);
    }
  }, [fetchVersions, applyVersions, applyVersionsError]);

  useEffect(() => {
    let cancelled = false;

    fetchVersions()
      .then((loaded) => {
        if (!cancelled) {
          applyVersions(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          applyVersionsError(err);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchVersions, applyVersions, applyVersionsError]);

  const fetchPromptTemplate = useCallback(async () => {
    if (!activeWorkspace) {
      return null;
    }

    return getPromptTemplate(
      activeWorkspace.organization_id,
      activeWorkspace.id,
      promptTemplateId,
    );
  }, [activeWorkspace, promptTemplateId]);

  const applyPromptTemplate = useCallback((loaded: PromptTemplate | null) => {
    if (loaded) {
      setPromptTemplate(loaded);
      setName(loaded.name);
    }
  }, []);

  const applyLoadError = useCallback((err: unknown) => {
    setError(
      err instanceof Error ? err.message : "Could not load this prompt template.",
    );
  }, []);

  const load = useCallback(async () => {
    try {
      applyPromptTemplate(await fetchPromptTemplate());
    } catch (err) {
      applyLoadError(err);
    }
  }, [fetchPromptTemplate, applyPromptTemplate, applyLoadError]);

  useEffect(() => {
    let cancelled = false;

    fetchPromptTemplate()
      .then((loaded) => {
        if (!cancelled) {
          applyPromptTemplate(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          applyLoadError(err);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchPromptTemplate, applyPromptTemplate, applyLoadError]);

  async function handleRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setActionError(null);
    setRenaming(true);

    try {
      await renamePromptTemplate(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        promptTemplateId,
        name,
      );
      await load();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Could not rename this prompt template.",
      );
    } finally {
      setRenaming(false);
    }
  }

  async function handleArchive() {
    if (!activeWorkspace) {
      return;
    }

    setActionError(null);
    setArchiving(true);

    try {
      await archivePromptTemplate(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        promptTemplateId,
      );
      await load();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Could not archive this prompt template.",
      );
    } finally {
      setArchiving(false);
    }
  }

  async function handleSaveVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setVersionError(null);
    setVersionSaved(null);
    setSavingVersion(true);

    try {
      const created = await createPromptTemplateVersion(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        promptTemplateId,
        content,
      );

      setVersionSaved(created);
      await loadVersions();
    } catch (err) {
      setVersionError(
        err instanceof Error ? err.message : "Could not save this version.",
      );
    } finally {
      setSavingVersion(false);
    }
  }

  async function handlePublish(version: number) {
    if (!activeWorkspace) {
      return;
    }

    setPublishError(null);
    setPublishingVersion(version);

    try {
      await publishPromptTemplate(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        promptTemplateId,
        version,
      );
      await load();
    } catch (err) {
      setPublishError(
        err instanceof Error ? err.message : "Could not publish this version.",
      );
    } finally {
      setPublishingVersion(null);
    }
  }

  async function handleDiff(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    const from = Number(diffFrom);
    const to = Number(diffTo);

    if (!Number.isFinite(from) || !Number.isFinite(to)) {
      setDiffError("Choose two versions to compare.");

      return;
    }

    setDiffError(null);
    setDiffResult(null);
    setDiffLoading(true);

    try {
      const result = await diffPromptTemplateVersions(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        promptTemplateId,
        from,
        to,
      );

      setDiffResult(result);
    } catch (err) {
      setDiffError(
        err instanceof Error ? err.message : "Could not compare these versions.",
      );
    } finally {
      setDiffLoading(false);
    }
  }

  if (tenantStatus === "error") {
    return (
      <PageShell title="Prompt Template">
        <ErrorText message={tenantError ?? "Could not load your workspace."} />
      </PageShell>
    );
  }

  if (tenantStatus === "loading" || (!error && !promptTemplate)) {
    return (
      <PageShell title="Prompt Template">
        <LoadingState />
      </PageShell>
    );
  }

  if (error) {
    return (
      <PageShell title="Prompt Template">
        <ErrorText message={error} />
      </PageShell>
    );
  }

  if (!promptTemplate) {
    return null;
  }

  const archived = promptTemplate.status === "archived";

  return (
    <PageShell
      title={promptTemplate.name}
      description="Prompt template configuration."
    >
      <div className="mb-6">
        <Link
          href="/prompt-templates"
          className="text-sm text-slate-400 hover:text-slate-300"
        >
          &larr; Back to prompt templates
        </Link>
      </div>

      {actionError && (
        <div className="mb-4">
          <ErrorText message={actionError} />
        </div>
      )}

      <Card>
        <div className="mb-4 flex items-center gap-3">
          <h2 className="text-lg font-semibold">Identity</h2>
          <PromptTemplateStatusBadge status={promptTemplate.status} />
          <span className="text-sm text-slate-500">
            {promptTemplate.use_case}
          </span>
        </div>

        <form onSubmit={handleRename} className="flex flex-wrap gap-3">
          <input
            aria-label="Prompt template name"
            className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
            required
            disabled={renaming || archived}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />

          <Button
            type="submit"
            disabled={renaming || archived || !name.trim()}
          >
            {renaming ? "Saving..." : "Save name"}
          </Button>
        </form>

        <div className="mt-6 border-t border-slate-800 pt-4">
          {archived ? (
            <p className="text-sm text-slate-500">
              This prompt template is archived.
            </p>
          ) : (
            <Button
              variant="secondary"
              disabled={archiving}
              onClick={handleArchive}
            >
              {archiving ? "Archiving..." : "Archive prompt template"}
            </Button>
          )}
        </div>
      </Card>

      <div className="mt-8">
        <Card>
          <h2 className="text-lg font-semibold">Content</h2>
          <p className="mt-1 text-sm text-slate-400">
            Saving posts a full new version snapshot - versions are immutable.
          </p>

          <form
            onSubmit={handleSaveVersion}
            className="mt-4 space-y-4"
            noValidate
          >
            {versionError && <ErrorText message={versionError} />}
            {versionSaved && (
              <p className="text-sm text-green-400">
                Saved as version {versionSaved.version}.
              </p>
            )}

            <div>
              <label
                htmlFor="content"
                className="block text-sm font-medium text-slate-200"
              >
                Content
              </label>

              <textarea
                id="content"
                rows={6}
                maxLength={20000}
                required
                disabled={savingVersion}
                className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                placeholder="Thanks for calling {{workspace.name}} - how can I help?"
                value={content}
                onChange={(event) => setContent(event.target.value)}
              />
            </div>

            <Button type="submit" disabled={savingVersion || !content.trim()}>
              {savingVersion ? "Saving..." : "Save as new version"}
            </Button>
          </form>
        </Card>
      </div>

      <div className="mt-8">
        <Card>
          <h2 className="text-lg font-semibold">Version history</h2>

          {publishError && (
            <div className="mt-4">
              <ErrorText message={publishError} />
            </div>
          )}

          {versionsError && (
            <div className="mt-4">
              <ErrorText message={versionsError} />
            </div>
          )}

          {versions === null && !versionsError && (
            <div className="mt-4">
              <LoadingState message="Loading versions..." />
            </div>
          )}

          {versions !== null && versions.length === 0 && (
            <div className="mt-4">
              <EmptyState message="No versions saved yet. Save one above to get started." />
            </div>
          )}

          {versions !== null && versions.length > 0 && (
            <ul className="mt-4 space-y-3">
              {versions.map((version) => {
                const isCurrent = version.id === promptTemplate.current_version_id;

                return (
                  <li
                    key={version.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 px-4 py-3"
                  >
                    <div>
                      <span className="font-medium">Version {version.version}</span>
                      <span className="ml-3 text-sm text-slate-500">
                        {new Date(version.created_at).toLocaleString()}
                      </span>
                    </div>

                    {isCurrent ? (
                      <span className="rounded-full border border-green-800 px-2.5 py-0.5 text-xs font-medium text-green-300">
                        Current
                      </span>
                    ) : (
                      <Button
                        variant="secondary"
                        disabled={archived || publishingVersion !== null}
                        onClick={() => handlePublish(version.version)}
                      >
                        {publishingVersion === version.version
                          ? "Publishing..."
                          : "Publish"}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {versions !== null && versions.length > 1 && (
            <div className="mt-6 border-t border-slate-800 pt-4">
              <h3 className="text-sm font-semibold text-slate-300">
                Compare versions
              </h3>

              <form
                onSubmit={handleDiff}
                className="mt-3 flex flex-wrap items-end gap-3"
                noValidate
              >
                <div>
                  <label
                    htmlFor="diff_from"
                    className="block text-sm font-medium text-slate-200"
                  >
                    From
                  </label>

                  <select
                    id="diff_from"
                    className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                    value={diffFrom}
                    onChange={(event) => setDiffFrom(event.target.value)}
                  >
                    <option value="" disabled>
                      Select a version
                    </option>
                    {versions.map((version) => (
                      <option key={version.id} value={version.version}>
                        Version {version.version}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="diff_to"
                    className="block text-sm font-medium text-slate-200"
                  >
                    To
                  </label>

                  <select
                    id="diff_to"
                    className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                    value={diffTo}
                    onChange={(event) => setDiffTo(event.target.value)}
                  >
                    <option value="" disabled>
                      Select a version
                    </option>
                    {versions.map((version) => (
                      <option key={version.id} value={version.version}>
                        Version {version.version}
                      </option>
                    ))}
                  </select>
                </div>

                <Button type="submit" disabled={diffLoading || !diffFrom || !diffTo}>
                  {diffLoading ? "Comparing..." : "Show diff"}
                </Button>
              </form>

              {diffError && (
                <div className="mt-4">
                  <ErrorText message={diffError} />
                </div>
              )}

              {diffResult && Object.keys(diffResult.changes).length === 0 && (
                <p className="mt-4 text-sm text-slate-400">
                  No differences between version {diffResult.from_version} and
                  version {diffResult.to_version}.
                </p>
              )}

              {diffResult && Object.keys(diffResult.changes).length > 0 && (
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="text-slate-400">
                        <th className="pb-2 pr-4 font-medium">Field</th>
                        <th className="pb-2 pr-4 font-medium">
                          Version {diffResult.from_version}
                        </th>
                        <th className="pb-2 font-medium">
                          Version {diffResult.to_version}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(diffResult.changes).map(
                        ([field, change]) => (
                          <tr key={field} className="border-t border-slate-800">
                            <td className="py-2 pr-4 font-medium">{field}</td>
                            <td className="py-2 pr-4 text-slate-400">
                              {formatDiffValue(change.previous)}
                            </td>
                            <td className="py-2 text-slate-200">
                              {formatDiffValue(change.current)}
                            </td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </PageShell>
  );
}
