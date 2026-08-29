"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

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
  createPromptTemplate,
  listPromptTemplates,
  type PromptTemplate,
} from "@/lib/prompt-templates";

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

export default function PromptTemplatesPage() {
  const {
    status: tenantStatus,
    error: tenantError,
    activeWorkspace,
  } = useTenant();

  const [promptTemplates, setPromptTemplates] = useState<PromptTemplate[] | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [useCase, setUseCase] = useState("");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Resets the list whenever the active workspace changes (including to
  // null) - adjusted during rendering, not in the effect below, since the
  // effect's own job is only the async fetch, not this synchronous reset.
  const [prevActiveWorkspace, setPrevActiveWorkspace] = useState(activeWorkspace);

  if (activeWorkspace !== prevActiveWorkspace) {
    setPrevActiveWorkspace(activeWorkspace);
    setPromptTemplates(null);
  }

  useEffect(() => {
    if (!activeWorkspace) {
      return;
    }

    let cancelled = false;

    listPromptTemplates(activeWorkspace.organization_id, activeWorkspace.id)
      .then((loaded) => {
        if (!cancelled) {
          setPromptTemplates(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Could not load prompt templates.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeWorkspace]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setFormError(null);
    setCreating(true);

    try {
      const created = await createPromptTemplate(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        name,
        useCase,
      );

      setName("");
      setUseCase("");
      setPromptTemplates((current) =>
        current ? [...current, created] : [created],
      );
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Could not create this prompt template.",
      );
    } finally {
      setCreating(false);
    }
  }

  if (tenantStatus === "error") {
    return (
      <PageShell title="Prompt Templates">
        <ErrorText message={tenantError ?? "Could not load your workspace."} />
      </PageShell>
    );
  }

  if (tenantStatus === "loading") {
    return (
      <PageShell title="Prompt Templates">
        <LoadingState />
      </PageShell>
    );
  }

  if (!activeWorkspace) {
    return (
      <PageShell title="Prompt Templates">
        <EmptyState message="Create a workspace first to add a prompt template." />
      </PageShell>
    );
  }

  if (error) {
    return (
      <PageShell title="Prompt Templates">
        <ErrorText message={error} />
      </PageShell>
    );
  }

  return (
    <PageShell
      title="Prompt Templates"
      description="Reusable prompt templates for this workspace's assistants."
    >
      <Card>
        <h2 className="text-lg font-semibold">Create a prompt template</h2>

        <form onSubmit={handleCreate} className="mt-4 space-y-4" noValidate>
          {formError && <ErrorText message={formError} />}

          <div className="flex flex-wrap gap-3">
            <input
              aria-label="Prompt template name"
              className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
              placeholder="Front Desk Receptionist"
              required
              disabled={creating}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />

            <input
              aria-label="Use case"
              className="min-w-48 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
              placeholder="receptionist"
              required
              disabled={creating}
              value={useCase}
              onChange={(event) => setUseCase(event.target.value)}
            />

            <Button
              type="submit"
              disabled={creating || !name.trim() || !useCase.trim()}
            >
              {creating ? "Creating..." : "Create"}
            </Button>
          </div>
        </form>
      </Card>

      <div className="mt-8">
        {promptTemplates === null ? (
          <LoadingState />
        ) : promptTemplates.length === 0 ? (
          <EmptyState message="No prompt templates yet. Create one above to get started." />
        ) : (
          <ul className="space-y-3">
            {promptTemplates.map((promptTemplate) => (
              <li key={promptTemplate.id}>
                <Link
                  href={`/prompt-templates/${promptTemplate.id}`}
                  className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-900 px-6 py-4 transition hover:border-slate-700"
                >
                  <div>
                    <span className="font-medium">{promptTemplate.name}</span>
                    <span className="ml-3 text-sm text-slate-500">
                      {promptTemplate.use_case}
                    </span>
                  </div>
                  <PromptTemplateStatusBadge status={promptTemplate.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PageShell>
  );
}
