"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { ArrowLeft } from "lucide-react";

import { login, signup } from "@/lib/api";
import { setAuthToken } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type AuthFormProps = {
  mode: "login" | "signup";
};

const CREDITS_EXHAUSTED =
  process.env.NEXT_PUBLIC_AI_CREDITS_EXHAUSTED === "true";
const CREDITS_MESSAGE =
  "Sorry, our AI models ran out of backend credits. It will be back up soon.";

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
    if (isSignup && CREDITS_EXHAUSTED) {
      setError(CREDITS_MESSAGE);
      return;
    }
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
    <main className="grid min-h-screen bg-background text-foreground lg:grid-cols-2">
      <section className="relative hidden min-h-screen overflow-hidden bg-muted lg:block">
        <Image
          alt=""
          aria-hidden
          className="object-cover"
          fill
          priority
          sizes="50vw"
          src="/auth-landscape.jpg"
        />
        <div className="absolute inset-0 bg-black/10" />
      </section>

      <section className="relative flex min-h-screen items-center justify-center px-6 py-10 sm:px-10">
        <div className="absolute left-6 right-6 top-6 flex items-center justify-between sm:left-10 sm:right-10">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-10 rounded-full"
            onClick={() => router.back()}
            aria-label="Go back"
          >
            <ArrowLeft className="size-5" />
          </Button>

          <Link
            className="flex items-center gap-3 text-2xl font-semibold"
            href="/"
          >
            <Image alt="" aria-hidden height={38} priority src="/logo.png" width={38} />
            fiebatt
          </Link>
        </div>

        <div className="w-full max-w-[390px]">
          <h1 className="text-4xl font-semibold tracking-normal">
            {isSignup ? "Create account" : "Welcome back"}
          </h1>
          <p className="mt-3 text-base leading-7 text-muted-foreground">
            {isSignup
              ? "Create a fiebatt account to save projects and continue edits."
              : "Log in to open your projects and continue editing."}
          </p>

          {CREDITS_EXHAUSTED ? (
            <p className="mt-6 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm font-medium leading-6 text-destructive">
              {CREDITS_MESSAGE}
            </p>
          ) : null}

          <form className="mt-10 space-y-6" onSubmit={handleSubmit}>
            <div className="space-y-2.5">
              <Label htmlFor="email">Email</Label>
              <Input
                autoComplete="email"
                className="h-12 rounded-xl bg-transparent text-base"
                id="email"
                onChange={(event) => setEmail(event.target.value)}
                required
                type="email"
                value={email}
              />
            </div>

            <div className="space-y-2.5">
              <Label htmlFor="password">Password</Label>
              <Input
                autoComplete={isSignup ? "new-password" : "current-password"}
                className="h-12 rounded-xl bg-transparent text-base"
                id="password"
                minLength={8}
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
            </div>

            {error ? (
              <p className="text-sm leading-6 text-destructive">
                {error}
              </p>
            ) : null}

            <Button
              className="h-12 w-full rounded-xl text-base"
              disabled={busy || (isSignup && CREDITS_EXHAUSTED)}
              type="submit"
            >
              {busy ? "Please wait" : isSignup ? "Sign up" : "Log in"}
            </Button>
          </form>

          <p className="mt-8 text-sm text-muted-foreground">
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
