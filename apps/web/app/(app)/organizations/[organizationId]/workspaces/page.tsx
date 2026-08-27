"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
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
import { canManage } from "@/lib/organizations";
import { createWorkspace } from "@/lib/workspaces";

export default function WorkspacesPage() {
  const params = useParams<{ organizationId: string }>();
  const organizationId = params.organizationId;

  const {
    status,
    error,
    activeOrganization,
    setActiveOrganization,
    workspaces,
    refresh,
  } = useTenant();

  useEffect(() => {
    // Skip while the provider's own initial resolution is still in flight -
    // `activeOrganization` starts null before that settles, which would
    // otherwise read as "wrong org" and fire a redundant, racing fetch
    // alongside the provider's own.
    if (status !== "loading" && activeOrganization?.id !== organizationId) {
      setActiveOrganization(organizationId);
    }
  }, [status, organizationId, activeOrganization, setActiveOrganization]);

  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setFormError(null);
    setCreating(true);

    try {
      await createWorkspace(organizationId, name);

      setName("");
      await refresh();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Could not create workspace.",
      );
    } finally {
      setCreating(false);
    }
  }

  if (status === "error") {
    return (
      <PageShell title="Workspaces">
        <ErrorText message={error ?? "Could not load workspaces."} />

        <p className="mt-6">
          <Link
            href={`/organizations/${organizationId}`}
            className="text-slate-400 underline"
          >
            Back to organization
          </Link>
        </p>
      </PageShell>
    );
  }

  if (
    status === "loading" ||
    !activeOrganization ||
    activeOrganization.id !== organizationId
  ) {
    return (
      <PageShell title="Workspaces">
        <LoadingState />
      </PageShell>
    );
  }

  const manages = canManage(activeOrganization.role);

  return (
    <PageShell
      title={`${activeOrganization.name} workspaces`}
      description="Locations, teams, or departments within this organization."
      action={
        <Link href={`/organizations/${organizationId}`}>
          <Button variant="secondary">Back to organization</Button>
        </Link>
      }
    >
      {manages && (
        <Card>
          <h2 className="text-lg font-semibold">Create a workspace</h2>

          <form onSubmit={handleCreate} className="mt-4 space-y-4" noValidate>
            {formError && <ErrorText message={formError} />}

            <div className="flex flex-wrap gap-3">
              <input
                aria-label="Workspace name"
                className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                placeholder="Downtown Clinic"
                required
                disabled={creating}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />

              <Button type="submit" disabled={creating || !name.trim()}>
                {creating ? "Creating..." : "Create"}
              </Button>
            </div>
          </form>
        </Card>
      )}

      <div className={manages ? "mt-8" : undefined}>
        {workspaces.length === 0 ? (
          <EmptyState
            message={
              manages
                ? "No workspaces yet. Create one above to get started."
                : "You have not been granted access to any workspace in this organization yet."
            }
          />
        ) : (
          <ul className="space-y-3">
            {workspaces.map((workspace) => (
              <li key={workspace.id}>
                <Link
                  href={`/organizations/${organizationId}/workspaces/${workspace.id}`}
                  className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-900 px-6 py-4 transition hover:border-slate-700"
                >
                  <span className="font-medium">{workspace.name}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PageShell>
  );
}
