"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { AuthShell } from "@/components/auth/auth-shell";
import {
  Field,
  FormError,
  SubmitButton,
} from "@/components/auth/form-controls";
import { register } from "@/lib/auth";

const MIN_PASSWORD_LENGTH = 8;

export default function RegisterPage() {
  const router = useRouter();

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setError(null);

    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(
        `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`,
      );

      return;
    }

    setPending(true);

    try {
      await register({ email, password, fullName });

      router.push("/");
      router.refresh();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Something went wrong. Please try again.",
      );
      setPending(false);
    }
  }

  return (
    <AuthShell
      title="Create your account"
      subtitle="Start working with your business data."
      footerPrompt="Already have an account?"
      footerHref="/login"
      footerLinkText="Sign in"
    >
      <form onSubmit={handleSubmit} className="space-y-5" noValidate>
        {error && <FormError message={error} />}

        <Field
          label="Full name"
          name="full_name"
          type="text"
          autoComplete="name"
          disabled={pending}
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          placeholder="Jane Doe"
          hint="Optional."
        />

        <Field
          label="Email"
          name="email"
          type="email"
          autoComplete="email"
          required
          disabled={pending}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@company.com"
        />

        <Field
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          disabled={pending}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
        />

        <SubmitButton
          pending={pending}
          idleLabel="Create account"
          pendingLabel="Creating account..."
        />
      </form>
    </AuthShell>
  );
}
