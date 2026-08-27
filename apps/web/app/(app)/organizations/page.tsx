"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { useTenant } from "@/components/app/tenant-provider";
import {
  Button,
  Card,
  EmptyState,
  ErrorText,
  LoadingState,
  PageShell,
  RoleBadge,
} from "@/components/organizations/ui";
import { createOrganization } from "@/lib/organizations";

export default function OrganizationsPage() {
  const { status, error, organizations, refresh } = useTenant();

  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setFormError(null);
    setCreating(true);

    try {
      await createOrganization(name);

      setName("");
      await refresh();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Could not create organization.",
      );
    } finally {
      setCreating(false);
    }
  }

  return (
    <PageShell
      title="Organizations"
      description="Workspaces you belong to."
    >
      <Card>
        <h2 className="text-lg font-semibold">Create an organization</h2>

        <form onSubmit={handleCreate} className="mt-4 space-y-4" noValidate>
          {formError && <ErrorText message={formError} />}

          <div className="flex flex-wrap gap-3">
            <input
              aria-label="Organization name"
              className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
              placeholder="Acme Corp"
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

      <div className="mt-8">
        {status === "error" && (
          <ErrorText message={error ?? "Could not load organizations."} />
        )}

        {status === "loading" && <LoadingState message="Loading organizations..." />}

        {status === "ready" && organizations.length === 0 && (
          <EmptyState message="You do not belong to any organization yet. Create one above to get started." />
        )}

        {status === "ready" && organizations.length > 0 && (
          <ul className="space-y-3">
            {organizations.map((organization) => (
              <li key={organization.id}>
                <Link
                  href={`/organizations/${organization.id}`}
                  className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-900 px-6 py-4 transition hover:border-slate-700"
                >
                  <span>
                    <span className="font-medium">{organization.name}</span>

                    <span className="ml-3 text-sm text-slate-500">
                      /{organization.slug}
                    </span>
                  </span>

                  <RoleBadge role={organization.role} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PageShell>
  );
}
