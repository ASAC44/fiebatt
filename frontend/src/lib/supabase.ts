import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// ── supabase client ───────────────────────────────────────────────
// auth-only. storage / db / realtime are NOT used here — those live
// on vultr. every other network call goes through src/api/client.ts.

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

if (!url || !anonKey) {
  // fail loud in dev so we don't silently render a dead login button.
  // (VITE_ vars are inlined at build time; missing means .env wasn't read.)
  // eslint-disable-next-line no-console
  console.warn(
    "[supabase] VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY is missing — auth will be disabled.",
  );
}

export const supabase: SupabaseClient = createClient(
  url ?? "https://missing.supabase.co",
  anonKey ?? "missing",
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true, // handles the #access_token=... redirect fragment
      flowType: "pkce",
    },
  },
);

export function isSupabaseConfigured(): boolean {
  if (!url || !anonKey) return false;
  if (url.includes("YOUR_") || anonKey.includes("YOUR_")) return false;
  if (url === "https://missing.supabase.co" || anonKey === "missing") return false;
  return true;
}

// where google bounces users back to. defaults to the current origin so it
// just works on localhost AND on prod without code changes.
export function getOAuthRedirect(): string {
  const fromEnv = import.meta.env.VITE_SUPABASE_REDIRECT as string | undefined;
  if (fromEnv && fromEnv.length > 0) return fromEnv;
  if (typeof window !== "undefined") return window.location.origin;
  return "http://localhost:3000";
}
