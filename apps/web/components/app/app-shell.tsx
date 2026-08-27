import type { ReactNode } from "react";

import { Sidebar } from "@/components/app/sidebar";
import { TopBar } from "@/components/app/top-bar";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-slate-950 text-white">
      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />

        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
