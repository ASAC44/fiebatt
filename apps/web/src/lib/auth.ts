"use client";

const DEFAULT_AUTH_DESTINATION = "/projects";
const AUTH_PATHS = new Set(["/login", "/signup"]);

export function safeAuthDestination(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return DEFAULT_AUTH_DESTINATION;
  }

  const destination = new URL(value, "https://fiebatt.local");
  if (AUTH_PATHS.has(destination.pathname)) {
    return DEFAULT_AUTH_DESTINATION;
  }
  return `${destination.pathname}${destination.search}${destination.hash}`;
}

export function redirectToLogin(): void {
  if (AUTH_PATHS.has(window.location.pathname)) return;

  const next = `${window.location.pathname}${window.location.search}`;
  const target = `/login?next=${encodeURIComponent(safeAuthDestination(next))}`;
  window.location.assign(target);
}
