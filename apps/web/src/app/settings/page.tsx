"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { getHealth, me, type HealthResp, type Me } from "@/lib/api";

export default function SettingsPage() {
  const router = useRouter();
  const [profile, setProfile] = useState<Me | null>(null);
  const [health, setHealth] = useState<HealthResp | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([me(), getHealth()])
      .then(([currentUser, serviceHealth]) => {
        if (!currentUser.signed_in) {
          router.replace("/login?next=/settings");
          return;
        }
        setProfile(currentUser);
        setHealth(serviceHealth);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Could not load settings.");
      });
  }, [router]);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="flex h-16 items-center justify-between border-b border-border px-5">
        <div className="flex items-center gap-3">
          <Button asChild size="icon" variant="ghost" className="rounded-full">
            <Link aria-label="Back to projects" href="/projects">
              <ArrowLeft className="size-5" />
            </Link>
          </Button>
          <Link className="flex items-center gap-2 text-2xl font-semibold" href="/">
            <Image alt="" aria-hidden height={34} src="/logo.png" width={34} />
            fiebatt
          </Link>
        </div>
      </header>

      <section className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-6 py-12">
        <div>
          <h1 className="text-4xl font-semibold">Settings</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Generation is configured and routed automatically by Fiebatt.
          </p>
        </div>

        {error ? (
          <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            {error}
          </div>
        ) : null}

        <section className="rounded-2xl border border-border bg-card/40 p-6">
          <h2 className="text-lg font-medium">Account</h2>
          {profile ? (
            <p className="mt-3 text-sm text-muted-foreground">{profile.email}</p>
          ) : (
            <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading account
            </p>
          )}
        </section>

        <section className="rounded-2xl border border-border bg-card/40 p-6">
          <h2 className="text-lg font-medium">Service</h2>
          {health ? (
            <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
              <CheckCircle2 className="size-4 text-emerald-500" />
              {health.ok ? "Ready" : "Temporarily unavailable"}
            </p>
          ) : (
            <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Checking service
            </p>
          )}
        </section>
      </section>
    </main>
  );
}
