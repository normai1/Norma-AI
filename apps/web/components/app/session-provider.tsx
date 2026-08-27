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
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      let current: AuthUser | null = null;

      try {
        current = await fetchCurrentUser();
      } catch {
        current = null;
      }

      if (cancelled) {
        return;
      }

      setUser(current);
      setStatus(current ? "authenticated" : "unauthenticated");
    }

    load();

    return () => {
      cancelled = true;
    };
  }, []);

  const signOut = useCallback(async () => {
    await logout();

    setUser(null);
    setStatus("unauthenticated");
  }, []);

  return (
    <SessionContext.Provider value={{ user, status, signOut }}>
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
