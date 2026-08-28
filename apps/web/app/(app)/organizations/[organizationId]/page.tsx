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
  RoleBadge,
  StatusBadge,
} from "@/components/organizations/ui";
import {
  canManage,
  changeMemberRole,
  getOrganization,
  inviteMember,
  listInvitations,
  listMembers,
  removeMember,
  revokeInvitation,
  type CreatedInvitation,
  type Invitation,
  type Member,
  type Organization,
  type OrganizationRole,
} from "@/lib/organizations";

const ASSIGNABLE_ROLES: OrganizationRole[] = [
  "owner",
  "admin",
  "member",
  "viewer",
];

export default function OrganizationDetailPage() {
  const params = useParams<{ organizationId: string }>();
  const organizationId = params.organizationId;

  const [organization, setOrganization] = useState<Organization | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<OrganizationRole>("member");
  const [inviting, setInviting] = useState(false);
  const [lastInvite, setLastInvite] = useState<CreatedInvitation | null>(null);

  const fetchOrganizationDetail = useCallback(async () => {
    const loaded = await getOrganization(organizationId);
    const members = await listMembers(organizationId);
    const invitations = canManage(loaded.role)
      ? await listInvitations(organizationId)
      : [];

    return { loaded, members, invitations };
  }, [organizationId]);

  const applyOrganizationDetail = useCallback(
    (detail: Awaited<ReturnType<typeof fetchOrganizationDetail>>) => {
      setOrganization(detail.loaded);
      setMembers(detail.members);
      setInvitations(detail.invitations);
    },
    [],
  );

  const applyLoadError = useCallback((err: unknown) => {
    setError(
      err instanceof Error ? err.message : "Could not load this organization.",
    );
  }, []);

  const load = useCallback(async () => {
    try {
      applyOrganizationDetail(await fetchOrganizationDetail());
    } catch (err) {
      applyLoadError(err);
    }
  }, [fetchOrganizationDetail, applyOrganizationDetail, applyLoadError]);

  useEffect(() => {
    let cancelled = false;

    fetchOrganizationDetail()
      .then((detail) => {
        if (!cancelled) {
          applyOrganizationDetail(detail);
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
  }, [fetchOrganizationDetail, applyOrganizationDetail, applyLoadError]);

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

  async function handleInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setActionError(null);
    setInviting(true);

    try {
      const created = await inviteMember(
        organizationId,
        inviteEmail,
        inviteRole,
      );

      setLastInvite(created);
      setInviteEmail("");
      await load();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Could not send that invitation.",
      );
    } finally {
      setInviting(false);
    }
  }

  if (error) {
    return (
      <PageShell title="Organization">
        <ErrorText message={error} />

        <p className="mt-6">
          <Link href="/organizations" className="text-slate-400 underline">
            Back to organizations
          </Link>
        </p>
      </PageShell>
    );
  }

  if (!organization) {
    return (
      <PageShell title="Organization">
        <LoadingState />
      </PageShell>
    );
  }

  const manages = canManage(organization.role);

  return (
    <PageShell
      title={organization.name}
      description={`/${organization.slug}`}
      action={
        <div className="flex gap-3">
          <Link href={`/organizations/${organizationId}/workspaces`}>
            <Button variant="secondary">Workspaces</Button>
          </Link>

          <Link href="/organizations">
            <Button variant="secondary">All organizations</Button>
          </Link>
        </div>
      }
    >
      {actionError && (
        <div className="mb-6">
          <ErrorText message={actionError} />
        </div>
      )}

      <Card>
        <h2 className="text-lg font-semibold">Members</h2>

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

              <span className="flex items-center gap-3">
                {manages ? (
                  <select
                    aria-label={`Role for ${member.user.email}`}
                    className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                    value={member.role}
                    onChange={(event) =>
                      run(() =>
                        changeMemberRole(
                          organizationId,
                          member.id,
                          event.target.value as OrganizationRole,
                        ),
                      )
                    }
                  >
                    {ASSIGNABLE_ROLES.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                ) : (
                  <RoleBadge role={member.role} />
                )}

                {manages && (
                  <Button
                    variant="danger"
                    onClick={() =>
                      run(() => removeMember(organizationId, member.id))
                    }
                  >
                    Remove
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      </Card>

      {manages && (
        <div className="mt-8">
          <Card>
            <h2 className="text-lg font-semibold">Invite someone</h2>

            <form onSubmit={handleInvite} className="mt-4 space-y-4" noValidate>
              <div className="flex flex-wrap gap-3">
                <input
                  aria-label="Invitee email"
                  type="email"
                  required
                  disabled={inviting}
                  className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                  placeholder="teammate@company.com"
                  value={inviteEmail}
                  onChange={(event) => setInviteEmail(event.target.value)}
                />

                <select
                  aria-label="Invitee role"
                  className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  value={inviteRole}
                  disabled={inviting}
                  onChange={(event) =>
                    setInviteRole(event.target.value as OrganizationRole)
                  }
                >
                  {ASSIGNABLE_ROLES.map((role) => (
                    <option key={role} value={role}>
                      {role}
                    </option>
                  ))}
                </select>

                <Button type="submit" disabled={inviting || !inviteEmail}>
                  {inviting ? "Inviting..." : "Invite"}
                </Button>
              </div>
            </form>

            {lastInvite && (
              <div className="mt-4 rounded-lg border border-amber-900 bg-amber-950/40 p-4 text-sm">
                <p className="font-medium text-amber-200">
                  No email provider is configured yet, so share this link
                  yourself.
                </p>

                <code className="mt-2 block break-all rounded bg-slate-950 px-3 py-2 text-xs text-slate-300">
                  {lastInvite.token}
                </code>
              </div>
            )}

            <h3 className="mt-8 text-sm font-semibold text-slate-300">
              Invitations
            </h3>

            {invitations.length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">
                No invitations issued yet.
              </p>
            ) : (
              <ul className="mt-3 divide-y divide-slate-800">
                {invitations.map((invitation) => (
                  <li
                    key={invitation.id}
                    className="flex flex-wrap items-center justify-between gap-3 py-3"
                  >
                    <span className="text-sm">
                      {invitation.email}

                      <span className="ml-2 text-slate-500">
                        {invitation.role}
                      </span>
                    </span>

                    <span className="flex items-center gap-3">
                      <StatusBadge status={invitation.status} />

                      {invitation.status === "pending" && (
                        <Button
                          variant="danger"
                          onClick={() =>
                            run(() =>
                              revokeInvitation(organizationId, invitation.id),
                            )
                          }
                        >
                          Revoke
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}

      {!manages && (
        <div className="mt-8">
          <EmptyState message="Only owners and admins can manage members and invitations." />
        </div>
      )}
    </PageShell>
  );
}
