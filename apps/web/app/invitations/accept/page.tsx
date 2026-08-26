"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import {
  Button,
  Card,
  ErrorText,
  PageShell,
} from "@/components/organizations/ui";
import { authorizedJson, fetchCurrentUser } from "@/lib/auth";

function AcceptInvitationForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Seeded from the link so an invitation URL prefills the field, while
  // staying editable for someone pasting a token by hand.
  const [token, setToken] = useState(() => searchParams.get("token") ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    async function requireSignIn() {
      if (!(await fetchCurrentUser())) {
        router.replace("/login");
      }
    }

    requireSignIn();
  }, [router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setError(null);
    setPending(true);

    try {
      await authorizedJson("/api/v1/invitations/accept", {
        method: "POST",
        body: JSON.stringify({ token }),
      });

      router.push("/organizations");
      router.refresh();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not accept this invitation.",
      );
      setPending(false);
    }
  }

  return (
    <Card>
      <h2 className="text-lg font-semibold">Accept an invitation</h2>

      <p className="mt-2 text-sm text-slate-400">
        Paste the invitation token you were given. It must match the email
        address you are signed in with.
      </p>

      <form onSubmit={handleSubmit} className="mt-4 space-y-4" noValidate>
        {error && <ErrorText message={error} />}

        <input
          aria-label="Invitation token"
          required
          disabled={pending}
          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
          placeholder="Invitation token"
          value={token}
          onChange={(event) => setToken(event.target.value)}
        />

        <div className="flex items-center gap-3">
          <Button type="submit" disabled={pending || !token}>
            {pending ? "Accepting..." : "Accept invitation"}
          </Button>

          <Link href="/organizations" className="text-sm text-slate-400 underline">
            Skip
          </Link>
        </div>
      </form>
    </Card>
  );
}

export default function AcceptInvitationPage() {
  return (
    <PageShell title="Invitation">
      <Suspense fallback={<p className="text-slate-400">Loading...</p>}>
        <AcceptInvitationForm />
      </Suspense>
    </PageShell>
  );
}
