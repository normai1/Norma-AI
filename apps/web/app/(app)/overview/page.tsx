"use client";

import Link from "next/link";

import { useSession } from "@/components/app/session-provider";
import { useTenant } from "@/components/app/tenant-provider";
import {
  Card,
  EmptyState,
  ErrorText,
  LoadingState,
  PageShell,
} from "@/components/organizations/ui";

export default function OverviewPage() {
  const { user } = useSession();
  const { status, error, activeOrganization, activeWorkspace } = useTenant();

  const greetingName = user?.full_name ?? user?.email ?? "there";

  return (
    <PageShell
      title={`Welcome back, ${greetingName}`}
      description="Here's where things stand."
    >
      {status === "loading" && <LoadingState />}

      {status === "error" && (
        <ErrorText message={error ?? "Could not load your workspace."} />
      )}

      {status === "ready" && !activeOrganization && (
        <EmptyState message="You do not belong to any organization yet. Create one to get started." />
      )}

      {status === "ready" && activeOrganization && (
        <Card>
          <h2 className="text-lg font-semibold">Active</h2>

          <p className="mt-2 text-slate-300">
            {activeOrganization.name}
            {activeWorkspace && (
              <span className="text-slate-500"> / {activeWorkspace.name}</span>
            )}
          </p>
        </Card>
      )}

      <div className="mt-8">
        <Card>
          <h2 className="text-lg font-semibold">Get started</h2>

          <p className="mt-2 text-slate-400">
            Manage your organizations, workspaces, and members.
          </p>

          <Link
            href="/organizations"
            className="mt-4 inline-block text-sm font-medium text-white underline"
          >
            Go to Organizations
          </Link>
        </Card>
      </div>
    </PageShell>
  );
}
