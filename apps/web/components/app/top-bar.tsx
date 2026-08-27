"use client";

import { useRouter } from "next/navigation";

import { useSession } from "@/components/app/session-provider";
import { useTenant } from "@/components/app/tenant-provider";
import { Button } from "@/components/organizations/ui";

export function TopBar() {
  const router = useRouter();
  const { user, signOut } = useSession();
  const {
    organizations,
    activeOrganization,
    setActiveOrganization,
    workspaces,
    activeWorkspace,
    setActiveWorkspace,
  } = useTenant();

  function handleOrganizationChange(id: string) {
    setActiveOrganization(id);
    router.push(`/organizations/${id}`);
  }

  function handleWorkspaceChange(id: string) {
    if (!activeOrganization) {
      return;
    }

    setActiveWorkspace(id);
    router.push(`/organizations/${activeOrganization.id}/workspaces/${id}`);
  }

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-slate-950 px-6 py-3">
      <div className="flex flex-wrap items-center gap-3">
        {organizations.length > 0 && (
          <select
            aria-label="Active organization"
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
            value={activeOrganization?.id ?? ""}
            onChange={(event) => handleOrganizationChange(event.target.value)}
          >
            {organizations.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </select>
        )}

        {activeOrganization && workspaces.length > 0 && (
          <select
            aria-label="Active workspace"
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
            value={activeWorkspace?.id ?? ""}
            onChange={(event) => handleWorkspaceChange(event.target.value)}
          >
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="flex items-center gap-3">
        {user && (
          <span className="text-sm text-slate-400">
            {user.full_name ?? user.email}
          </span>
        )}

        <Button variant="secondary" onClick={() => signOut()}>
          Sign out
        </Button>
      </div>
    </header>
  );
}
