"use client";

import { useCallback, useEffect, useState } from "react";
import { API, apiFetch, clearAuthToken, setAuthToken } from "@/lib/api";

type Me = { auth: boolean; name: string; email: string; role: string };

/** With AUTH_MODE=on on the server, shows a sign-in / sign-up form until you're signed in. Otherwise it does nothing. */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<"loading" | "open" | "signin" | "ready">("loading");
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [form, setForm] = useState({ email: "", password: "", name: "", invite: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const check = useCallback(async () => {
    try {
      const cfg = await (await fetch(`${API}/auth/config`)).json();
      if (!cfg.auth) return setState("open");
      const r = await apiFetch("/auth/me");
      setState(r.ok ? "ready" : "signin");
    } catch {
      setState("open"); // backend unreachable: let the app show its own error
    }
  }, []);

  useEffect(() => {
    check();
    const onOut = () => setState("signin");
    window.addEventListener("deskmates-signed-out", onOut);
    return () => window.removeEventListener("deskmates-signed-out", onOut);
  }, [check]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const body = mode === "login" ? { email: form.email, password: form.password } : form;
    const r = await fetch(`${API}/auth/${mode}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) return setErr(typeof data.detail === "string" ? data.detail : "Something went wrong");
    setAuthToken(data.token);
    setState("ready");
  }

  if (state === "loading") return <div className="p-8 text-sm text-black/50">Loading…</div>;
  if (state === "open" || state === "ready") return <>{children}</>;
  const input = "w-full rounded-lg border border-black/15 px-3 py-2 text-sm";
  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-6">
      <div>
        <h1 className="text-2xl font-bold">Deskmates</h1>
        <p className="text-sm text-black/60">{mode === "login" ? "Sign in to your office." : "Create your office, or join one with an invite code."}</p>
      </div>
      <form onSubmit={submit} className="space-y-2">
        {mode === "signup" && <input className={input} placeholder="Your name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}
        <input className={input} type="email" autoComplete="email" placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <input className={input} type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="Password (8+ characters)" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        {mode === "signup" && <input className={input} placeholder="Invite code (optional)" value={form.invite} onChange={(e) => setForm({ ...form, invite: e.target.value })} />}
        {err && <p className="text-sm text-red-600">{err}</p>}
        <button disabled={busy || !form.email || !form.password} className="w-full rounded-lg bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>
      <button
        onClick={() => {
          setMode(mode === "login" ? "signup" : "login");
          setErr("");
        }}
        className="text-sm underline"
      >
        {mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}
      </button>
    </main>
  );
}

export function SignOut() {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    apiFetch("/auth/me")
      .then((r) => r.json())
      .then(setMe)
      .catch(() => {});
  }, []);
  if (!me?.auth) return null;
  return (
    <button
      onClick={() => {
        clearAuthToken();
        window.dispatchEvent(new Event("deskmates-signed-out"));
      }}
      title={`${me.email} (${me.role})`}
      className="rounded-full bg-black/10 px-3 py-1.5 text-xs font-medium"
    >
      Sign out {me.name}
    </button>
  );
}
