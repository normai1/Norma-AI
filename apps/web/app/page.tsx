"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { API_URL } from "@/lib/api";
import { fetchCurrentUser, logout, type AuthUser } from "@/lib/auth";

interface HealthResponse {
  service: string;
  version: string;
  status: string;
}

export default function Home() {
  const [health, setHealth] =
    useState<HealthResponse | null>(null);

  const [error, setError] =
    useState<string | null>(null);

  const [user, setUser] = useState<AuthUser | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);

  useEffect(() => {
    async function checkHealth() {
      try {
        const response = await fetch(
          `${API_URL}/api/v1/health`,
        );

        if (!response.ok) {
          throw new Error(
            `HTTP ${response.status}`,
          );
        }

        const data =
          (await response.json()) as HealthResponse;

        setHealth(data);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unknown error",
        );
      }
    }

    checkHealth();
  }, []);

  useEffect(() => {
    async function loadSession() {
      try {
        setUser(await fetchCurrentUser());
      } catch {
        setUser(null);
      } finally {
        setSessionChecked(true);
      }
    }

    loadSession();
  }, []);

  async function handleSignOut() {
    await logout();

    setUser(null);
  }

  return (
    <main className="min-h-screen bg-slate-950 text-white">
      <div className="mx-auto flex min-h-screen max-w-5xl flex-col justify-center px-6">
        <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="mb-3 text-sm font-medium text-slate-400">
              AI Receptionist Platform
            </p>

            <h1 className="text-5xl font-bold tracking-tight">
              Norma AI
            </h1>

            <p className="mt-4 max-w-xl text-lg text-slate-400">
              Foundation environment is running.
            </p>
          </div>

          {sessionChecked && (
            <div className="flex items-center gap-3">
              {user ? (
                <>
                  <span className="text-sm text-slate-400">
                    {user.full_name ?? user.email}
                  </span>

                  <Link
                    href="/overview"
                    className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-900"
                  >
                    Go to app
                  </Link>

                  <button
                    type="button"
                    onClick={handleSignOut}
                    className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-600"
                  >
                    Sign out
                  </button>
                </>
              ) : (
                <>
                  <Link
                    href="/login"
                    className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-900"
                  >
                    Sign in
                  </Link>

                  <Link
                    href="/register"
                    className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-slate-950 transition hover:bg-slate-200"
                  >
                    Create account
                  </Link>
                </>
              )}
            </div>
          )}
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
          <h2 className="text-lg font-semibold">
            System Status
          </h2>

          {health && (
            <div className="mt-4 space-y-2 text-sm">
              <p>
                API:{" "}
                <span className="text-green-400">
                  {health.status}
                </span>
              </p>

              <p>
                Service: {health.service}
              </p>

              <p>
                Version: {health.version}
              </p>

              <p>
                Session:{" "}
                <span className={user ? "text-green-400" : "text-slate-400"}>
                  {sessionChecked
                    ? user
                      ? `signed in as ${user.email}`
                      : "signed out"
                    : "checking..."}
                </span>
              </p>
            </div>
          )}

          {error && (
            <p className="mt-4 text-red-400">
              API unavailable: {error}
            </p>
          )}

          {!health && !error && (
            <p className="mt-4 text-slate-400">
              Checking backend...
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
