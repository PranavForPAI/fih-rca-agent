/**
 * Client-side auth state.
 * The server sets an HttpOnly cookie; from the client we can only probe
 * whether we're logged in by calling a lightweight /api/me endpoint.
 * We store the user's display name in sessionStorage to avoid a round-trip
 * on every re-render.
 */
import { useCallback, useEffect, useState } from "react";

type AuthState = "loading" | "authed" | "unauthed";

const ME_KEY = "fhi_auth_user";

/** Simple probe: if /api/me returns 200, we're authenticated. */
async function probeAuth(): Promise<boolean> {
  try {
    const res = await fetch("/api/me", { credentials: "include" });
    return res.ok;
  } catch {
    return false;
  }
}

export function useAuth() {
  const [state, setState] = useState<AuthState>("loading");

  const check = useCallback(async () => {
    // Fast path: sessionStorage hint
    const cached = typeof window !== "undefined" ? sessionStorage.getItem(ME_KEY) : null;
    if (cached) {
      setState("authed");
      return;
    }
    const ok = await probeAuth();
    if (ok) {
      sessionStorage.setItem(ME_KEY, "1");
      setState("authed");
    } else {
      setState("unauthed");
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  const onLogin = useCallback(() => {
    sessionStorage.setItem(ME_KEY, "1");
    setState("authed");
  }, []);

  const logout = useCallback(async () => {
    sessionStorage.removeItem(ME_KEY);
    setState("unauthed");
    await fetch("/api/logout", { method: "POST", credentials: "include" });
  }, []);

  return { state, onLogin, logout };
}
