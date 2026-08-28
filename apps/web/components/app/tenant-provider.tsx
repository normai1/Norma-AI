"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { listOrganizations, type Organization } from "@/lib/organizations";
import {
  getStoredActiveOrganizationId,
  getStoredActiveWorkspaceId,
  resolveActiveId,
  setStoredActiveOrganizationId,
  setStoredActiveWorkspaceId,
} from "@/lib/tenant-selection";
import { listWorkspaces, type Workspace } from "@/lib/workspaces";

export type TenantStatus = "loading" | "ready" | "error";

interface TenantContextValue {
  status: TenantStatus;
  error: string | null;
  organizations: Organization[];
  activeOrganization: Organization | null;
  setActiveOrganization: (id: string) => void;
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  setActiveWorkspace: (id: string) => void;
  refresh: () => Promise<void>;
}

const TenantContext = createContext<TenantContextValue | null>(null);

export function TenantProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<TenantStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [activeOrganizationId, setActiveOrganizationId] = useState<
    string | null
  >(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(
    null,
  );

  const loadWorkspaces = useCallback(async (organizationId: string) => {
    try {
      const loaded = await listWorkspaces(organizationId);

      setWorkspaces(loaded);

      const resolved = resolveActiveId(
        getStoredActiveWorkspaceId(),
        loaded.map((workspace) => workspace.id),
      );

      setActiveWorkspaceId(resolved);
      setStoredActiveWorkspaceId(resolved);
      setStatus("ready");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not load workspaces.",
      );
      setStatus("error");
    }
  }, []);

  const applyOrganizations = useCallback(
    async (loaded: Organization[]) => {
      setOrganizations(loaded);

      const resolved = resolveActiveId(
        getStoredActiveOrganizationId(),
        loaded.map((organization) => organization.id),
      );

      setActiveOrganizationId(resolved);
      setStoredActiveOrganizationId(resolved);

      if (resolved) {
        await loadWorkspaces(resolved);
      } else {
        setWorkspaces([]);
        setActiveWorkspaceId(null);
        setStoredActiveWorkspaceId(null);
        setStatus("ready");
      }
    },
    [loadWorkspaces],
  );

  const applyLoadError = useCallback((err: unknown) => {
    setError(
      err instanceof Error ? err.message : "Could not load organizations.",
    );
    setStatus("error");
  }, []);

  const load = useCallback(async () => {
    try {
      await applyOrganizations(await listOrganizations());
    } catch (err) {
      applyLoadError(err);
    }
  }, [applyOrganizations, applyLoadError]);

  useEffect(() => {
    let cancelled = false;

    listOrganizations()
      .then((loaded) => {
        if (!cancelled) {
          return applyOrganizations(loaded);
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
  }, [applyOrganizations, applyLoadError]);

  const setActiveOrganization = useCallback(
    (id: string) => {
      setActiveOrganizationId(id);
      setStoredActiveOrganizationId(id);
      setStatus("loading");
      loadWorkspaces(id);
    },
    [loadWorkspaces],
  );

  const setActiveWorkspace = useCallback((id: string) => {
    setActiveWorkspaceId(id);
    setStoredActiveWorkspaceId(id);
  }, []);

  const activeOrganization =
    organizations.find((organization) => organization.id === activeOrganizationId) ??
    null;

  const activeWorkspace =
    workspaces.find((workspace) => workspace.id === activeWorkspaceId) ?? null;

  return (
    <TenantContext.Provider
      value={{
        status,
        error,
        organizations,
        activeOrganization,
        setActiveOrganization,
        workspaces,
        activeWorkspace,
        setActiveWorkspace,
        refresh: load,
      }}
    >
      {children}
    </TenantContext.Provider>
  );
}

/** Active organization, resolved from the real membership list and persisted. */
export function useTenant(): TenantContextValue {
  const context = useContext(TenantContext);

  if (!context) {
    throw new Error("useTenant must be used within a TenantProvider");
  }

  return context;
}
