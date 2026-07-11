"use client";

export type VideoProvider = "wan" | "happyhorse" | "veo" | "meshapi_veo";

export type FiebattSettings = {
  demoMode: boolean;
  videoProvider: VideoProvider;
};

const SETTINGS_KEY = "fiebatt.settings";

export const VIDEO_PROVIDERS: Array<{
  value: VideoProvider;
  label: string;
  description: string;
}> = [
  {
    value: "wan",
    label: "Wan",
    description: "Default source-video editing path.",
  },
  {
    value: "happyhorse",
    label: "HappyHorse",
    description: "Fallback generation path for image-conditioned variants.",
  },
  {
    value: "veo",
    label: "Veo",
    description: "Google Veo path through Gemini credentials.",
  },
  {
    value: "meshapi_veo",
    label: "Mesh API Veo",
    description: "Mesh gateway path for a Veo 3 model route.",
  },
];

const DEFAULT_SETTINGS: FiebattSettings = {
  demoMode: true,
  videoProvider: "wan",
};

function isVideoProvider(value: unknown): value is VideoProvider {
  return VIDEO_PROVIDERS.some((provider) => provider.value === value);
}

export function getSettings(): FiebattSettings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  const raw = localStorage.getItem(SETTINGS_KEY);
  if (!raw) return DEFAULT_SETTINGS;

  try {
    const parsed = JSON.parse(raw) as Partial<FiebattSettings>;
    return {
      demoMode: typeof parsed.demoMode === "boolean" ? parsed.demoMode : DEFAULT_SETTINGS.demoMode,
      videoProvider: isVideoProvider(parsed.videoProvider)
        ? parsed.videoProvider
        : DEFAULT_SETTINGS.videoProvider,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(next: FiebattSettings): void {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
  window.dispatchEvent(new CustomEvent("fiebatt:settings-change", { detail: next }));
}

export function updateSettings(patch: Partial<FiebattSettings>): FiebattSettings {
  const next = { ...getSettings(), ...patch };
  saveSettings(next);
  return next;
}

export function subscribeToSettings(onStoreChange: () => void): () => void {
  window.addEventListener("storage", onStoreChange);
  window.addEventListener("focus", onStoreChange);
  window.addEventListener("fiebatt:settings-change", onStoreChange);
  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener("focus", onStoreChange);
    window.removeEventListener("fiebatt:settings-change", onStoreChange);
  };
}
