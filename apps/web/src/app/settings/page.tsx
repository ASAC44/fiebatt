"use client";

import Image from "next/image";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  getSettings,
  subscribeToSettings,
  updateSettings,
  VIDEO_PROVIDERS,
  type VideoProvider,
} from "@/lib/settings";

export default function SettingsPage() {
  const settings = useSyncExternalStore(
    subscribeToSettings,
    getSettings,
    getSettings,
  );

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

      <section className="mx-auto flex w-full max-w-3xl flex-col gap-10 px-6 py-12">
        <div>
          <h1 className="text-4xl font-semibold tracking-normal">Settings</h1>
        </div>

        <div className="grid gap-8">
          <section className="rounded-2xl border border-border bg-card/40 p-6">
            <div className="flex items-center justify-between gap-5">
              <div>
                <Label htmlFor="demo-mode" className="text-lg font-medium">
                  Mode
                </Label>
              </div>
              <Switch
                id="demo-mode"
                checked={settings.demoMode}
                onCheckedChange={(demoMode) => updateSettings({ demoMode })}
              />
            </div>
          </section>

          <section className="rounded-2xl border border-border bg-card/40 p-6">
            <div className="grid gap-5">
              <div>
                <Label htmlFor="video-provider" className="text-lg font-medium">
                  Video provider
                </Label>
              </div>

              <Select
                value={settings.videoProvider}
                onValueChange={(value) => updateSettings({ videoProvider: value as VideoProvider })}
              >
                <SelectTrigger id="video-provider" className="h-12 rounded-xl text-base">
                  <SelectValue placeholder="Choose provider" />
                </SelectTrigger>
                <SelectContent>
                  {VIDEO_PROVIDERS.map((provider) => (
                    <SelectItem key={provider.value} value={provider.value}>
                      {provider.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </section>
        </div>
      </section>
    </main>
  );
}
