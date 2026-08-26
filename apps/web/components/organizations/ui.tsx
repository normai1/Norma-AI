import type { ButtonHTMLAttributes, ReactNode } from "react";

import type { OrganizationRole } from "@/lib/organizations";

export function PageShell({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="min-h-screen bg-slate-950 text-white">
      <div className="mx-auto max-w-4xl px-6 py-12">
        <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">{title}</h1>

            {description && (
              <p className="mt-2 text-slate-400">{description}</p>
            )}
          </div>

          {action}
        </div>

        {children}
      </div>
    </main>
  );
}

export function Card({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
      {children}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <p className="rounded-2xl border border-dashed border-slate-800 px-6 py-10 text-center text-slate-400">
      {message}
    </p>
  );
}

export function ErrorText({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="rounded-lg border border-red-900 bg-red-950/60 px-3 py-2 text-sm text-red-300"
    >
      {message}
    </p>
  );
}

export function RoleBadge({ role }: { role: OrganizationRole }) {
  return (
    <span className="rounded-full border border-slate-700 px-2.5 py-0.5 text-xs font-medium text-slate-300">
      {role}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "pending"
      ? "border-amber-800 text-amber-300"
      : status === "accepted"
        ? "border-green-800 text-green-300"
        : "border-slate-700 text-slate-400";

  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${tone}`}
    >
      {status}
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
};

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonProps) {
  const styles = {
    primary:
      "bg-white text-slate-950 hover:bg-slate-200 focus:ring-white",
    secondary:
      "border border-slate-700 text-white hover:bg-slate-900 focus:ring-slate-600",
    danger:
      "border border-red-900 text-red-300 hover:bg-red-950/60 focus:ring-red-800",
  }[variant];

  return (
    <button
      className={`rounded-lg px-4 py-2 text-sm font-medium transition focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-60 ${styles} ${className}`}
      {...props}
    />
  );
}
