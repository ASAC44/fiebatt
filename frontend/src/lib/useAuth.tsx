import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Session, User } from "@supabase/supabase-js";
import { getOAuthRedirect, isSupabaseConfigured, supabase } from "./supabase";

// ── auth state ────────────────────────────────────────────────────
// thin react context over the supabase session. one source of truth:
// whenever supabase fires onAuthStateChange, we re-render.

const DEV_USER_ID_KEY = "iris.dev_user_id";

function getDevUserId(): string {
  let id = localStorage.getItem(DEV_USER_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(DEV_USER_ID_KEY, id);
  }
  return id;
}

function makeDevSession(): { session: Session; status: "authed" } {
  const userId = getDevUserId();
  return {
    session: {
      access_token: "",
      token_type: "bearer",
      expires_in: 86400,
      expires_at: Math.floor(Date.now() / 1000) + 86400,
      refresh_token: "",
      user: {
        id: userId,
        aud: "authenticated",
        role: "authenticated",
        email: "dev@local.dev",
        email_confirmed_at: new Date().toISOString(),
        phone: "",
        confirmed_at: new Date().toISOString(),
        last_sign_in_at: new Date().toISOString(),
        app_metadata: { provider: "local" },
        user_metadata: { name: "Local Dev", avatar_url: "", email: "dev@local.dev" },
        identities: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    },
    status: "authed" as const,
  };
}

type AuthState = {
  session: Session | null;
  user: User | null;
  status: "loading" | "authed" | "anon";
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>("loading");

  useEffect(() => {
    if (!isSupabaseConfigured()) {
      const dev = makeDevSession();
      setSession(dev.session);
      setStatus("authed");
      return;
    }

    let active = true;

    supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setStatus(data.session ? "authed" : "anon");
    });

    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s);
      setStatus(s ? "authed" : "anon");
    });

    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  const signInWithGoogle = useCallback(async () => {
    if (!isSupabaseConfigured()) return;
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: getOAuthRedirect(),
        scopes: "openid email profile",
      },
    });
    if (error) {
      // eslint-disable-next-line no-alert
      alert(`google sign-in failed: ${error.message}`);
    }
  }, []);

  const signOut = useCallback(async () => {
    if (!isSupabaseConfigured()) return;
    await supabase.auth.signOut();
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      session,
      user: session?.user ?? null,
      status,
      signInWithGoogle,
      signOut,
    }),
    [session, status, signInWithGoogle, signOut],
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
