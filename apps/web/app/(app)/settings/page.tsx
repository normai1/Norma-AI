"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useSession } from "@/components/app/session-provider";
import { useTenant } from "@/components/app/tenant-provider";
import { Field } from "@/components/auth/form-controls";
import {
  Button,
  Card,
  EmptyState,
  ErrorText,
  LoadingState,
  PageShell,
} from "@/components/organizations/ui";
import { changePassword, updateProfile } from "@/lib/auth";
import {
  businessHoursFromApi,
  businessHoursToApi,
  emptyBusinessHoursForm,
  type BusinessHoursForm,
  type DayRowState,
} from "@/lib/business-hours";
import {
  canManage,
  SUPPORTED_CURRENCIES,
  updateOrganization,
} from "@/lib/organizations";
import {
  BUSINESS_HOURS_DAYS,
  COMMON_LOCALES,
  COMMON_TIMEZONES,
  updateWorkspace,
  type BusinessHoursDay,
} from "@/lib/workspaces";

export default function SettingsPage() {
  const { user, refreshUser } = useSession();
  const {
    status: tenantStatus,
    error: tenantError,
    activeOrganization,
    workspaces,
    activeWorkspace,
    refresh: refreshTenant,
  } = useTenant();

  const [currency, setCurrency] = useState("USD");
  const [savingOrgSettings, setSavingOrgSettings] = useState(false);
  const [orgSettingsError, setOrgSettingsError] = useState<string | null>(null);
  const [orgSettingsSaved, setOrgSettingsSaved] = useState(false);

  useEffect(() => {
    if (activeOrganization) {
      setCurrency(activeOrganization.settings.currency);
    }
  }, [activeOrganization]);

  const [timezone, setTimezone] = useState("UTC");
  const [locale, setLocale] = useState("en-US");
  const [businessHours, setBusinessHours] = useState<BusinessHoursForm>(
    emptyBusinessHoursForm(),
  );
  const [savingWorkspaceSettings, setSavingWorkspaceSettings] = useState(false);
  const [workspaceSettingsError, setWorkspaceSettingsError] = useState<
    string | null
  >(null);
  const [workspaceSettingsSaved, setWorkspaceSettingsSaved] = useState(false);

  useEffect(() => {
    if (activeWorkspace) {
      setTimezone(activeWorkspace.settings.timezone);
      setLocale(activeWorkspace.settings.locale);
      setBusinessHours(businessHoursFromApi(activeWorkspace.settings.business_hours));
    }
  }, [activeWorkspace]);

  function updateDay(day: BusinessHoursDay, patch: Partial<DayRowState>) {
    setBusinessHours((prev) => ({ ...prev, [day]: { ...prev[day], ...patch } }));
  }

  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [avatarUrl, setAvatarUrl] = useState(user?.avatar_url ?? "");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileSaved, setProfileSaved] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordChanged, setPasswordChanged] = useState(false);

  async function handleProfileSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setProfileError(null);
    setProfileSaved(false);
    setSavingProfile(true);

    try {
      await updateProfile({ fullName, avatarUrl });
      await refreshUser();
      setProfileSaved(true);
    } catch (err) {
      setProfileError(
        err instanceof Error ? err.message : "Could not save your profile.",
      );
    } finally {
      setSavingProfile(false);
    }
  }

  async function handlePasswordSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setPasswordError(null);
    setPasswordChanged(false);

    if (newPassword !== confirmPassword) {
      setPasswordError("New password and confirmation do not match.");

      return;
    }

    setChangingPassword(true);

    try {
      await changePassword({ currentPassword, newPassword });

      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordChanged(true);
    } catch (err) {
      setPasswordError(
        err instanceof Error ? err.message : "Could not change your password.",
      );
    } finally {
      setChangingPassword(false);
    }
  }

  async function handleOrgSettingsSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeOrganization) {
      return;
    }

    setOrgSettingsError(null);
    setOrgSettingsSaved(false);
    setSavingOrgSettings(true);

    try {
      await updateOrganization(activeOrganization.id, {
        settings: { currency },
      });
      await refreshTenant();
      setOrgSettingsSaved(true);
    } catch (err) {
      setOrgSettingsError(
        err instanceof Error
          ? err.message
          : "Could not save organization settings.",
      );
    } finally {
      setSavingOrgSettings(false);
    }
  }

  async function handleWorkspaceSettingsSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    if (!activeOrganization || !activeWorkspace) {
      return;
    }

    setWorkspaceSettingsError(null);
    setWorkspaceSettingsSaved(false);
    setSavingWorkspaceSettings(true);

    try {
      await updateWorkspace(activeOrganization.id, activeWorkspace.id, {
        settings: {
          timezone,
          locale,
          business_hours: businessHoursToApi(businessHours),
        },
      });
      await refreshTenant();
      setWorkspaceSettingsSaved(true);
    } catch (err) {
      setWorkspaceSettingsError(
        err instanceof Error
          ? err.message
          : "Could not save workspace settings.",
      );
    } finally {
      setSavingWorkspaceSettings(false);
    }
  }

  if (!user) {
    return (
      <PageShell title="Settings">
        <LoadingState />
      </PageShell>
    );
  }

  return (
    <PageShell
      title="Settings"
      description="Your account, organization, and workspace preferences."
    >
      <Card>
        <h2 className="text-lg font-semibold">Account</h2>

        <form onSubmit={handleProfileSubmit} className="mt-4 space-y-4" noValidate>
          {profileError && <ErrorText message={profileError} />}
          {profileSaved && <p className="text-sm text-green-400">Saved.</p>}

          <Field
            label="Name"
            name="full_name"
            type="text"
            disabled={savingProfile}
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            placeholder="Jane Doe"
          />

          <Field
            label="Avatar URL"
            name="avatar_url"
            type="url"
            disabled={savingProfile}
            value={avatarUrl}
            onChange={(event) => setAvatarUrl(event.target.value)}
            placeholder="https://example.com/avatar.png"
          />

          <Button type="submit" disabled={savingProfile}>
            {savingProfile ? "Saving..." : "Save profile"}
          </Button>
        </form>

        <form
          onSubmit={handlePasswordSubmit}
          className="mt-8 space-y-4 border-t border-slate-800 pt-6"
          noValidate
        >
          <h3 className="text-sm font-semibold text-slate-300">
            Change password
          </h3>

          {passwordError && <ErrorText message={passwordError} />}
          {passwordChanged && (
            <p className="text-sm text-green-400">Password changed.</p>
          )}

          <Field
            label="Current password"
            name="current_password"
            type="password"
            autoComplete="current-password"
            required
            disabled={changingPassword}
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
          />

          <Field
            label="New password"
            name="new_password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            disabled={changingPassword}
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            hint="At least 8 characters."
          />

          <Field
            label="Confirm new password"
            name="confirm_password"
            type="password"
            autoComplete="new-password"
            required
            disabled={changingPassword}
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
          />

          <Button type="submit" disabled={changingPassword}>
            {changingPassword ? "Changing..." : "Change password"}
          </Button>
        </form>
      </Card>

      <div className="mt-8">
        <Card>
          <h2 className="text-lg font-semibold">Organization</h2>

          {tenantStatus === "loading" && <LoadingState />}

          {tenantStatus === "error" && (
            <ErrorText
              message={tenantError ?? "Could not load organization settings."}
            />
          )}

          {tenantStatus === "ready" && !activeOrganization && (
            <EmptyState message="You do not belong to any organization yet." />
          )}

          {tenantStatus === "ready" &&
            activeOrganization &&
            (canManage(activeOrganization.role) ? (
              <form
                onSubmit={handleOrgSettingsSubmit}
                className="mt-4 space-y-4"
                noValidate
              >
                {orgSettingsError && <ErrorText message={orgSettingsError} />}
                {orgSettingsSaved && (
                  <p className="text-sm text-green-400">Saved.</p>
                )}

                <div>
                  <label
                    htmlFor="currency"
                    className="block text-sm font-medium text-slate-200"
                  >
                    Currency
                  </label>

                  <select
                    id="currency"
                    className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                    disabled={savingOrgSettings}
                    value={currency}
                    onChange={(event) => setCurrency(event.target.value)}
                  >
                    {SUPPORTED_CURRENCIES.map((code) => (
                      <option key={code} value={code}>
                        {code}
                      </option>
                    ))}
                  </select>
                </div>

                <Button type="submit" disabled={savingOrgSettings}>
                  {savingOrgSettings ? "Saving..." : "Save organization settings"}
                </Button>
              </form>
            ) : (
              <p className="mt-4 text-sm text-slate-400">
                Currency: {activeOrganization.settings.currency}
              </p>
            ))}
        </Card>
      </div>

      <div className="mt-8">
        <Card>
          <h2 className="text-lg font-semibold">Workspace</h2>

          {tenantStatus === "loading" && <LoadingState />}

          {tenantStatus === "error" && (
            <ErrorText
              message={tenantError ?? "Could not load workspace settings."}
            />
          )}

          {tenantStatus === "ready" &&
            activeOrganization &&
            workspaces.length === 0 && (
              <EmptyState message="This organization has no workspace yet." />
            )}

          {tenantStatus === "ready" &&
            activeOrganization &&
            activeWorkspace &&
            (canManage(activeOrganization.role) ? (
              <form
                onSubmit={handleWorkspaceSettingsSubmit}
                className="mt-4 space-y-6"
                noValidate
              >
                {workspaceSettingsError && (
                  <ErrorText message={workspaceSettingsError} />
                )}
                {workspaceSettingsSaved && (
                  <p className="text-sm text-green-400">Saved.</p>
                )}

                <div className="flex flex-wrap gap-6">
                  <div>
                    <label
                      htmlFor="timezone"
                      className="block text-sm font-medium text-slate-200"
                    >
                      Timezone
                    </label>

                    <select
                      id="timezone"
                      className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                      disabled={savingWorkspaceSettings}
                      value={timezone}
                      onChange={(event) => setTimezone(event.target.value)}
                    >
                      {COMMON_TIMEZONES.map((tz) => (
                        <option key={tz} value={tz}>
                          {tz}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label
                      htmlFor="locale"
                      className="block text-sm font-medium text-slate-200"
                    >
                      Locale
                    </label>

                    <select
                      id="locale"
                      className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                      disabled={savingWorkspaceSettings}
                      value={locale}
                      onChange={(event) => setLocale(event.target.value)}
                    >
                      {COMMON_LOCALES.map((loc) => (
                        <option key={loc} value={loc}>
                          {loc}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-slate-300">
                    Business hours
                  </h3>

                  <div className="mt-3 space-y-2">
                    {BUSINESS_HOURS_DAYS.map((day) => {
                      const row = businessHours[day];

                      return (
                        <div
                          key={day}
                          className="flex flex-wrap items-center gap-3"
                        >
                          <label className="flex w-32 items-center gap-2 text-sm text-slate-300">
                            <input
                              type="checkbox"
                              checked={row.open}
                              disabled={savingWorkspaceSettings}
                              onChange={(event) =>
                                updateDay(day, { open: event.target.checked })
                              }
                            />

                            <span className="capitalize">{day}</span>
                          </label>

                          {row.open ? (
                            <>
                              <input
                                type="time"
                                aria-label={`${day} open time`}
                                className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-white"
                                disabled={savingWorkspaceSettings}
                                value={row.start}
                                onChange={(event) =>
                                  updateDay(day, { start: event.target.value })
                                }
                              />

                              <span className="text-slate-500">to</span>

                              <input
                                type="time"
                                aria-label={`${day} close time`}
                                className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-white"
                                disabled={savingWorkspaceSettings}
                                value={row.end}
                                onChange={(event) =>
                                  updateDay(day, { end: event.target.value })
                                }
                              />
                            </>
                          ) : (
                            <span className="text-sm text-slate-500">
                              Closed
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                <Button type="submit" disabled={savingWorkspaceSettings}>
                  {savingWorkspaceSettings ? "Saving..." : "Save workspace settings"}
                </Button>
              </form>
            ) : (
              <div className="mt-4 space-y-1 text-sm text-slate-400">
                <p>Timezone: {activeWorkspace.settings.timezone}</p>
                <p>Locale: {activeWorkspace.settings.locale}</p>
              </div>
            ))}
        </Card>
      </div>
    </PageShell>
  );
}
