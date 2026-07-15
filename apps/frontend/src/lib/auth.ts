"use client";

const TOKEN_KEY = "fiebatt.auth_token";

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function hasAuthToken(): boolean {
  return Boolean(getAuthToken());
}

export function redirectToLogin(): void {
  clearAuthToken();
  const next = `${window.location.pathname}${window.location.search}`;
  const target = next && next !== "/login" ? `/login?next=${encodeURIComponent(next)}` : "/login";
  window.location.assign(target);
}
