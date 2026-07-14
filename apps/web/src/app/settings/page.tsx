"use client";

import Image from "next/image";
import Link from "next/link";
import { ArrowLeft, Check, Loader2, Trash2 } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  deleteProviderKey,
  listProviderKeys,
  saveProviderKey,
  type ProviderStatus,
} from "@/lib/api";
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
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [providerMessage, setProviderMessage] = useState("");

  useEffect(() => {
    listProviderKeys()
      .then(setProviders)
      .catch(() => setProviderMessage("Sign in to manage provider keys."));
  }, []);

  async function saveProvider(provider: ProviderStatus["provider"]) {
    const value = keys[provider]?.trim();
    if (!value) return;
    setBusy(provider);
    setProviderMessage("");
    try {
      await saveProviderKey(provider, value);
      setKeys((current) => ({ ...current, [provider]: "" }));
      setProviders(await listProviderKeys());
      setProviderMessage(`${provider} key saved securely.`);
    } catch (error) {
      setProviderMessage(error instanceof Error ? error.message : "Could not save key.");
    } finally {
      setBusy(null);
    }
  }

  async function removeProvider(provider: ProviderStatus["provider"]) {
    setBusy(provider);
    setProviderMessage("");
    try {
      await deleteProviderKey(provider);
      setProviders(await listProviderKeys());
      setProviderMessage(`${provider} key removed.`);
    } catch (error) {
      setProviderMessage(error instanceof Error ? error.message : "Could not remove key.");
    } finally {
      setBusy(null);
    }
  }

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

          <section className="rounded-2xl border border-border bg-card/40 p-6">
            <div className="grid gap-5">
              <div>
                <h2 className="text-lg font-medium">AI provider keys</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Keys are encrypted at rest and are never shown again. Codex uses them only for your Fiebatt jobs.
                </p>
              </div>

              {providers.map((provider) => (
                <div className="grid gap-2 sm:grid-cols-[130px_1fr_auto] sm:items-end" key={provider.provider}>
                  <div>
                    <Label className="capitalize" htmlFor={`key-${provider.provider}`}>{provider.provider}</Label>
                    <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                      {provider.configured && <Check className="size-3" />}
                      {provider.configured ? `Saved ••••${provider.key_hint}` : "Not configured"}
                    </div>
                  </div>
                  <Input
                    autoComplete="off"
                    className="h-10"
                    id={`key-${provider.provider}`}
                    onChange={(event) => setKeys((current) => ({ ...current, [provider.provider]: event.target.value }))}
                    placeholder={provider.configured ? "Enter a replacement key" : "Paste API key"}
                    type="password"
                    value={keys[provider.provider] ?? ""}
                  />
                  <div className="flex gap-2">
                    <Button disabled={busy === provider.provider || !keys[provider.provider]?.trim()} onClick={() => saveProvider(provider.provider)}>
                      {busy === provider.provider ? <Loader2 className="size-4 animate-spin" /> : "Save"}
                    </Button>
                    {provider.configured && (
                      <Button aria-label={`Remove ${provider.provider} key`} disabled={busy === provider.provider} onClick={() => removeProvider(provider.provider)} size="icon" variant="outline">
                        <Trash2 className="size-4" />
                      </Button>
                    )}
                  </div>
                </div>
              ))}

              {providerMessage && <p className="text-sm text-muted-foreground" role="status">{providerMessage}</p>}
            </div>
          </section>
        </div>
      </section>
    </main>
  );
}
