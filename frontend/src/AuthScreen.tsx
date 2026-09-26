import { type FormEvent, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { isSupabaseConfigured, supabase } from "./lib/supabase";

type AuthMode = "signin" | "signup";

type AuthScreenProps = {
  onAuthenticated: (session: Session) => void;
  onBack: () => void;
};

export function AuthScreen({ onAuthenticated, onBack }: AuthScreenProps) {
  const [mode, setMode] = useState<AuthMode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;

    setBusy(true);
    setMessage("");
    try {
      const result =
        mode === "signup"
          ? await supabase.auth.signUp({ email, password })
          : await supabase.auth.signInWithPassword({ email, password });
      if (result.error) throw result.error;
      if (result.data.session) {
        onAuthenticated(result.data.session);
      } else {
        setMessage("Check your email to confirm your account, then sign in.");
      }
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="screen auth-screen">
      <button className="auth-back" type="button" onClick={onBack}>
        ← Home
      </button>
      <div className="auth-brand">
        <img src="/brand-logo.png" alt="Grow a Game" />
        <p>Games begin with an idea.</p>
      </div>
      <div className="auth-card">
        <p className="auth-eyebrow">{mode === "signin" ? "WELCOME BACK" : "NEW PLAYER"}</p>
        <h1>{mode === "signin" ? "Sign in" : "Create account"}</h1>
        {!isSupabaseConfigured ? (
          <div className="auth-setup" role="status">
            Add <code>VITE_SUPABASE_URL</code> and <code>VITE_SUPABASE_ANON_KEY</code> to
            your <code>.env</code>, then rebuild the frontend.
          </div>
        ) : (
          <form onSubmit={submit}>
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
                minLength={6}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
            {message && <p className="auth-message" role="status">{message}</p>}
            <button className="auth-submit" type="submit" disabled={busy}>
              {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
            </button>
          </form>
        )}
        <button
          className="auth-switch"
          type="button"
          onClick={() => {
            setMode(mode === "signin" ? "signup" : "signin");
            setMessage("");
          }}
        >
          {mode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}
        </button>
      </div>
    </section>
  );
}
