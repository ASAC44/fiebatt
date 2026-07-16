"use client";

export function redirectToLogin(): void {
  const next = `${window.location.pathname}${window.location.search}`;
  const target = next && next !== "/login" ? `/login?next=${encodeURIComponent(next)}` : "/login";
  window.location.assign(target);
}
