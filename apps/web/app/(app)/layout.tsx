"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { AppShell } from "@/components/app/app-shell";
import { SessionProvider, useSession } from "@/components/app/session-provider";
import { TenantProvider } from "@/components/app/tenant-provider";

function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { status } = useSession();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
    }
  }, [status, router]);

  if (status === "loading") {
    return (
      <main className="min-h-screen bg-slate-950 text-white">
        <p className="p-12 text-slate-400">Loading...</p>
      </main>
    );
  }

  if (status === "unauthenticated") {
    return null;
  }

  return (
    <TenantProvider>
      <AppShell>{children}</AppShell>
    </TenantProvider>
  );
}

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <AuthGuard>{children}</AuthGuard>
    </SessionProvider>
  );
}
