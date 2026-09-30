import { Bot, Eye, EyeOff, Lock, User } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";

interface LoginPageProps {
  onSuccess: () => void;
}

export function LoginPage({ onSuccess }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        onSuccess();
      } else {
        const body = await res.json().catch(() => ({}));
        setError((body as { message?: string }).message ?? "Login failed. Please try again.");
      }
    } catch {
      setError("Network error. Please check your connection.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      {/* Background glow */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-1/3 left-1/2 h-[600px] w-[600px] -translate-x-1/2 rounded-full bg-accent/10 blur-[120px]" />
        <div className="absolute bottom-0 right-0 h-[400px] w-[400px] rounded-full bg-primary/5 blur-[100px]" />
      </div>

      <div className="relative w-full max-w-[400px]">
        {/* Logo */}
        <div className="mb-8 flex flex-col items-center gap-4">
          <div className="grid size-14 place-items-center rounded-[14px] bg-accent shadow-[0_0_40px_var(--accent-glow)] font-display text-base font-black text-accent-foreground">
            FIH
          </div>
          <div className="text-center">
            <p className="font-display text-2xl font-black text-foreground">FIH Agent</p>
            <p className="mt-1 text-sm font-semibold text-muted-foreground">
              Commerce intelligence workspace
            </p>
          </div>
        </div>

        {/* Card */}
        <div className="glass rounded-2xl p-8">
          <h1 className="mb-1 font-display text-lg font-black">Sign in</h1>
          <p className="mb-6 text-xs font-semibold text-muted-foreground">
            Use your workspace credentials to continue.
          </p>

          <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
            {/* Username */}
            <div className="space-y-1.5">
              <label htmlFor="login-username" className="text-[11px] font-bold uppercase text-muted-foreground">
                Username
              </label>
              <div className="relative">
                <User className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="login-username"
                  type="text"
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="w-full rounded-[8px] border border-border bg-background/60 py-2.5 pl-10 pr-4 text-sm font-medium placeholder:text-muted-foreground/50 focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all"
                  placeholder="Enter username"
                />
              </div>
            </div>

            {/* Password */}
            <div className="space-y-1.5">
              <label htmlFor="login-password" className="text-[11px] font-bold uppercase text-muted-foreground">
                Password
              </label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded-[8px] border border-border bg-background/60 py-2.5 pl-10 pr-10 text-sm font-medium placeholder:text-muted-foreground/50 focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all"
                  placeholder="Enter password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            {/* Error */}
            {error && (
              <div
                role="alert"
                className="rounded-[8px] border border-accent/30 bg-accent/10 px-3 py-2.5 text-xs font-semibold text-accent"
              >
                {error}
              </div>
            )}

            {/* Submit */}
            <Button
              type="submit"
              disabled={loading || !username || !password}
              className="mt-2 w-full bg-primary text-primary-foreground hover:bg-primary/90"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="flex gap-1">
                    <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:0ms]" />
                    <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:150ms]" />
                    <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:300ms]" />
                  </span>
                  Signing in…
                </span>
              ) : (
                "Sign in"
              )}
            </Button>
          </form>
        </div>

        {/* Footer */}
        <div className="mt-6 flex items-center justify-center gap-1.5 text-[10px] font-semibold uppercase text-muted-foreground">
          <Bot className="size-3" />
          AI-powered RCA · Evidence-aware
        </div>
      </div>
    </div>
  );
}
