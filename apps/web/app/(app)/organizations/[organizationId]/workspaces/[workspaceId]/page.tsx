"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  Button,
  Card,
  EmptyState,
  ErrorText,
  LoadingState,
  PageShell,
} from "@/components/organizations/ui";
import {
  canManage,
  getOrganization,
  listMembers,
  type Member,
  type Organization,
} from "@/lib/organizations";
import {
  addWorkspaceMember,
  getWorkspace,
  listWorkspaceMembers,
  removeWorkspaceMember,
  type Workspace,
  type WorkspaceMember,
} from "@/lib/workspaces";

export default function WorkspaceMembersPage() {
  const params = useParams<{ organizationId: string; workspaceId: string }>();
  const { organizationId, workspaceId } = params;

  const [organization, setOrganization] = useState<Organization | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [orgMembers, setOrgMembers] = useState<Member[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [selectedMemberId, setSelectedMemberId] = useState("");
  const [adding, setAdding] = useState(false);

  const fetchWorkspaceDetail = useCallback(async () => {
    const org = await getOrganization(organizationId);
    const workspace = await getWorkspace(organizationId, workspaceId);
    const members = await listWorkspaceMembers(organizationId, workspaceId);
    const orgMembers = canManage(org.role)
      ? await listMembers(organizationId)
      : [];

    return { org, workspace, members, orgMembers };
  }, [organizationId, workspaceId]);

  const applyWorkspaceDetail = useCallback(
    (detail: Awaited<ReturnType<typeof fetchWorkspaceDetail>>) => {
      setOrganization(detail.org);
      setWorkspace(detail.workspace);
      setMembers(detail.members);
      setOrgMembers(detail.orgMembers);
    },
    [],
  );

  const applyLoadError = useCallback((err: unknown) => {
    setError(
      err instanceof Error ? err.message : "Could not load this workspace.",
    );
  }, []);

  const load = useCallback(async () => {
    try {
      applyWorkspaceDetail(await fetchWorkspaceDetail());
    } catch (err) {
      applyLoadError(err);
    }
  }, [fetchWorkspaceDetail, applyWorkspaceDetail, applyLoadError]);

  useEffect(() => {
    let cancelled = false;

    fetchWorkspaceDetail()
      .then((detail) => {
        if (!cancelled) {
          applyWorkspaceDetail(detail);
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
  }, [fetchWorkspaceDetail, applyWorkspaceDetail, applyLoadError]);

  async function run(action: () => Promise<unknown>) {
    setActionError(null);

    try {
      await action();
      await load();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "That action did not work.",
      );
    }
  }

  async function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setActionError(null);
    setAdding(true);

    try {
      await addWorkspaceMember(organizationId, workspaceId, selectedMemberId);

      setSelectedMemberId("");
      await load();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Could not grant access.",
      );
    } finally {
      setAdding(false);
    }
  }

  if (error) {
    return (
      <PageShell title="Workspace">
        <ErrorText message={error} />

        <p className="mt-6">
          <Link
            href={`/organizations/${organizationId}/workspaces`}
            className="text-slate-400 underline"
          >
            Back to workspaces
          </Link>
        </p>
      </PageShell>
    );
  }

  if (!organization || !workspace) {
    return (
      <PageShell title="Workspace">
        <LoadingState />
      </PageShell>
    );
  }

  const manages = canManage(organization.role);
  const memberUserIds = new Set(members.map((member) => member.user.id));
  const addable = orgMembers.filter(
    (member) => !memberUserIds.has(member.user.id),
  );

  return (
    <PageShell
      title={workspace.name}
      description={organization.name}
      action={
        <Link href={`/organizations/${organizationId}/workspaces`}>
          <Button variant="secondary">Back to workspaces</Button>
        </Link>
      }
    >
      {actionError && (
        <div className="mb-6">
          <ErrorText message={actionError} />
        </div>
      )}

      <Card>
        <h2 className="text-lg font-semibold">Members</h2>

        {members.length === 0 ? (
          <div className="mt-4">
            <EmptyState message="Nobody has access to this workspace yet." />
          </div>
        ) : (
          <ul className="mt-4 divide-y divide-slate-800">
            {members.map((member) => (
              <li
                key={member.id}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <span>
                  <span className="font-medium">
                    {member.user.full_name ?? member.user.email}
                  </span>

                  {member.user.full_name && (
                    <span className="ml-2 text-sm text-slate-500">
                      {member.user.email}
                    </span>
                  )}
                </span>

                {manages && (
                  <Button
                    variant="danger"
                    onClick={() =>
                      run(() =>
                        removeWorkspaceMember(
                          organizationId,
                          workspaceId,
                          member.id,
                        ),
                      )
                    }
                  >
                    Remove
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {manages && (
        <div className="mt-8">
          <Card>
            <h2 className="text-lg font-semibold">Grant access</h2>

            {addable.length === 0 ? (
              <p className="mt-4 text-sm text-slate-500">
                Every organization member already has access to this workspace.
              </p>
            ) : (
              <form onSubmit={handleAdd} className="mt-4 flex flex-wrap gap-3">
                <select
                  aria-label="Organization member"
                  className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  required
                  disabled={adding}
                  value={selectedMemberId}
                  onChange={(event) => setSelectedMemberId(event.target.value)}
                >
                  <option value="" disabled>
                    Select a member
                  </option>

                  {addable.map((member) => (
                    <option key={member.id} value={member.id}>
                      {member.user.full_name ?? member.user.email}
                    </option>
                  ))}
                </select>

                <Button type="submit" disabled={adding || !selectedMemberId}>
                  {adding ? "Granting..." : "Grant access"}
                </Button>
              </form>
            )}
          </Card>
        </div>
      )}
    </PageShell>
  );
}
