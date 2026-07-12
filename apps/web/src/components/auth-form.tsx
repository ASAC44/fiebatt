"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { login, signup } from "@/lib/api";
import { setAuthToken } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type AuthFormProps = {
  mode: "login" | "signup";
};

export function AuthForm({ mode }: AuthFormProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isSignup = mode === "signup";
  const next = searchParams.get("next") || "/projects";

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const response = isSignup
        ? await signup(email, password)
        : await login(email, password);
      setAuthToken(response.access_token);
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? cleanError(err.message) : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-background px-6 py-10 text-foreground">
      <section className="w-full max-w-sm">
        <Link
          className="mx-auto mb-10 flex w-fit items-center gap-3 text-3xl font-semibold"
          href="/"
        >
          <Image alt="" aria-hidden height={42} priority src="/logo.png" width={42} />
          fiebatt
        </Link>

        <div className="rounded-2xl border border-border bg-card/70 p-6">
          <div>
            <h1 className="text-3xl font-semibold tracking-normal">
              {isSignup ? "Create account" : "Welcome back"}
            </h1>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              {isSignup
                ? "Create a fiebatt account to save projects and continue edits."
                : "Log in to open your projects and continue editing."}
            </p>
          </div>

          <form className="mt-7 space-y-5" onSubmit={handleSubmit}>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                autoComplete="email"
                id="email"
                onChange={(event) => setEmail(event.target.value)}
                required
                type="email"
                value={email}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                autoComplete={isSignup ? "new-password" : "current-password"}
                id="password"
                minLength={8}
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
            </div>

            {error ? (
              <div className="rounded-lg border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </div>
            ) : null}

            <Button className="h-11 w-full text-base" disabled={busy} type="submit">
              {busy ? "Please wait" : isSignup ? "Sign up" : "Log in"}
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-muted-foreground">
            {isSignup ? "Already have an account?" : "No account yet?"}{" "}
            <Link
              className="font-medium text-primary hover:underline"
              href={isSignup ? `/login?next=${encodeURIComponent(next)}` : `/signup?next=${encodeURIComponent(next)}`}
            >
              {isSignup ? "Log in" : "Sign up"}
            </Link>
          </p>
        </div>
      </section>
    </main>
  );
}

function cleanError(message: string): string {
  if (message.includes("409")) return "That email is already registered.";
  if (message.includes("401")) return "Invalid email or password.";
  if (message.includes("422")) return "Use a valid email and a password with at least 8 characters.";
  return message;
}
