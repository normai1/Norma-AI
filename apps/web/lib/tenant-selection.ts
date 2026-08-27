const ACTIVE_ORGANIZATION_KEY = "norma.active_organization";
const ACTIVE_WORKSPACE_KEY = "norma.active_workspace";

/**
 * Pick which id should be active given what was previously persisted and the
 * ids actually available right now. A persisted id that no longer appears in
 * the available list (deleted, access revoked, or simply never valid) is
 * discarded silently in favor of the first available id, never surfaced as an
 * error - the caller lost nothing they had a right to see.
 */
export function resolveActiveId(
  persistedId: string | null,
  availableIds: string[],
): string | null {
  if (availableIds.length === 0) {
    return null;
  }

  if (persistedId && availableIds.includes(persistedId)) {
    return persistedId;
  }

  return availableIds[0];
}

function readStored(key: string): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return window.localStorage.getItem(key);
}

function writeStored(key: string, value: string | null): void {
  if (typeof window === "undefined") {
    return;
  }

  if (value === null) {
    window.localStorage.removeItem(key);
  } else {
    window.localStorage.setItem(key, value);
  }
}

export function getStoredActiveOrganizationId(): string | null {
  return readStored(ACTIVE_ORGANIZATION_KEY);
}

export function setStoredActiveOrganizationId(id: string | null): void {
  writeStored(ACTIVE_ORGANIZATION_KEY, id);
}

export function getStoredActiveWorkspaceId(): string | null {
  return readStored(ACTIVE_WORKSPACE_KEY);
}

export function setStoredActiveWorkspaceId(id: string | null): void {
  writeStored(ACTIVE_WORKSPACE_KEY, id);
}
