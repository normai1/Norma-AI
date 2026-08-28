"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { fetchCurrentUser, logout, type AuthUser } from "@/lib/auth";

export type SessionStatus = "loading" | "authenticated" | "unauthenticated";

interface SessionContextValue {
  user: AuthUser | null;
  status: SessionStatus;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");

  const fetchUser = useCallback(async (): Promise<AuthUser | null> => {
    try {
      return await fetchCurrentUser();
    } catch {
      return null;
    }
  }, []);

  const applyUser = useCallback((current: AuthUser | null) => {
    setUser(current);
    setStatus(current ? "authenticated" : "unauthenticated");
  }, []);

  const refreshUser = useCallback(async () => {
    applyUser(await fetchUser());
  }, [fetchUser, applyUser]);

  useEffect(() => {
    let cancelled = false;

    fetchUser().then((current) => {
      if (!cancelled) {
        applyUser(current);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [fetchUser, applyUser]);

  const signOut = useCallback(async () => {
    await logout();

    setUser(null);
    setStatus("unauthenticated");
  }, []);

  return (
    <SessionContext.Provider value={{ user, status, signOut, refreshUser }}>
      {children}
    </SessionContext.Provider>
  );
}

/** Resolves the signed-in user once per mount of the provider. */
export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);

  if (!context) {
    throw new Error("useSession must be used within a SessionProvider");
  }

  return context;
}
