import Link from "next/link";
import type { ReactNode } from "react";

interface AuthShellProps {
  title: string;
  subtitle: string;
  footerPrompt: string;
  footerHref: string;
  footerLinkText: string;
  children: ReactNode;
}

export function AuthShell({
  title,
  subtitle,
  footerPrompt,
  footerHref,
  footerLinkText,
  children,
}: AuthShellProps) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 py-12 text-white">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <Link
            href="/"
            className="text-2xl font-bold tracking-tight text-white"
          >
            Norma AI
          </Link>

          <h1 className="mt-6 text-xl font-semibold">{title}</h1>

          <p className="mt-2 text-sm text-slate-400">{subtitle}</p>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
          {children}
        </div>

        <p className="mt-6 text-center text-sm text-slate-400">
          {footerPrompt}{" "}
          <Link
            href={footerHref}
            className="font-medium text-white underline underline-offset-4 hover:text-slate-200"
          >
            {footerLinkText}
          </Link>
        </p>
      </div>
    </main>
  );
}
