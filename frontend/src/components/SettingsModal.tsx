"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, getAdminToken, setAdminToken } from "@/lib/api";
import AutomationsTab from "@/components/AutomationsTab";
import { FeedbackCard, RetrievalCard } from "@/components/QualityExtras";

type Key = { id: number | string; provider: string; label: string; masked: string; source: "env" | "saved"; status: string; last_error?: string };
type QuotaModel = { model: string; calls_today: number; quota_hits: number; errors: number; keys: number; cooling: number; estimated_left: number; available_in_s: number };
type Quota = { level: "none" | "ok" | "low" | "exhausted"; estimated_left: number; providers: { provider: string; label: string; keys: number; daily_per_key?: number; models: QuotaModel[] }[] };
const PROVIDER_OPTIONS: [string, string][] = [
  ["gemini", "Gemini"],
  ["groq", "Groq"],
  ["openrouter", "OpenRouter"],
  ["cerebras", "Cerebras"],
];
type Row = { calls: number; errors: number; tokens_in: number; tokens_out: number; p50_ms: number; p95_ms: number; [k: string]: string | number };
type Metrics = {
  llm_calls: number;
  llm_errors: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  p50_ms: number;
  p95_ms: number;
  tool_calls: number;
  plans_total: number;
  plans_without_llm: number;
  by_agent: Row[];
  by_model: Row[];
  by_tool: Row[];
};
type Report = {
  started?: number;
  duration_s?: number;
  total?: number;
  passed?: number;
  routing_precision?: number;
  routing_coverage?: number;
  by_category?: Record<string, { passed: number; total: number }>;
  cases?: { id: string; passed: boolean; checks: { name: string; ok: boolean; detail: string }[] }[];
};

const TABS = [
  ["keys", "API keys"],
  ["team", "Team"],
  ["auto", "Automations"],
  ["health", "Health"],
  ["quality", "Quality"],
] as const;

function Card({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border border-black/10 p-3">
      <p className="text-[11px] uppercase tracking-wide text-black/50">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
      {hint && <p className="text-[11px] text-black/50">{hint}</p>}
    </div>
  );
}

export default function SettingsModal({ onClose, onKeysChanged }: { onClose: () => void; onKeysChanged: () => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("keys");
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-2xl bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-bold">Settings</h2>
          <button onClick={onClose} className="rounded-full bg-black/10 px-3 py-1 text-xs font-medium">
            Close
          </button>
        </div>
        <div className="mb-3 flex gap-1 rounded-full bg-black/5 p-1 text-xs font-medium">
          {TABS.map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} className={`flex-1 rounded-full px-3 py-1.5 ${tab === id ? "bg-black text-white" : ""}`}>
              {label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto">
          {tab === "keys" && <KeysTab onChanged={onKeysChanged} />}
          {tab === "team" && <TeamTab />}
          {tab === "auto" && <AutomationsTab />}
          {tab === "health" && <HealthTab />}
          {tab === "quality" && <QualityTab />}
        </div>
      </div>
    </div>
  );
}

// ---------------- API keys ----------------

function KeysTab({ onChanged }: { onChanged: () => void }) {
  const [keys, setKeys] = useState<Key[]>([]);
  const [locked, setLocked] = useState(false);
  const [notAdmin, setNotAdmin] = useState(false);
  const [token, setToken] = useState("");
  const [provider, setProvider] = useState("gemini");
  const [secret, setSecret] = useState("");
  const [label, setLabel] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const r = await apiFetch("/settings/keys");
    if (r.status === 401) return setLocked(true);
    if (r.status === 403) return setNotAdmin(true); // signed in, but not an admin
    setLocked(false);
    setNotAdmin(false);
    setKeys((await r.json()).keys ?? []);
  }, []);

  useEffect(() => {
    load().catch(() => setMsg({ ok: false, text: "Can't reach the backend." }));
  }, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    const r = await apiFetch("/settings/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider, key: secret.trim(), label }) });
    const body = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) return setMsg({ ok: false, text: typeof body.detail === "string" ? body.detail : "Couldn't add the key" });
    setSecret("");
    setLabel("");
    setMsg({ ok: true, text: "Key verified and saved. It's encrypted on the server and won't be shown again." });
    await load();
    onChanged();
  }

  async function test(id: number | string) {
    const r = await apiFetch(`/settings/keys/${id}/test`, { method: "POST" });
    const body = await r.json().catch(() => ({}));
    setMsg({ ok: r.ok && body.ok, text: body.message ?? body.detail ?? "Test failed" });
    await load();
  }

  async function remove(id: number | string) {
    await apiFetch(`/settings/keys/${id}`, { method: "DELETE" });
    await load();
    onChanged();
  }

  if (notAdmin) {
    return <p className="text-sm text-black/70">Only workspace admins can see and change API keys. Ask an admin if the agents need a new key.</p>;
  }

  if (locked) {
    return (
      <div className="space-y-3">
        <p className="text-sm">This server asks for an admin token before showing or changing keys.</p>
        <div className="flex gap-2">
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Admin token" className="flex-1 rounded-lg border border-black/15 px-3 py-2 text-sm" />
          <button
            onClick={() => {
              setAdminToken(token);
              load();
            }}
            className="rounded-lg bg-black px-4 py-2 text-sm font-medium text-white"
          >
            Unlock
          </button>
        </div>
        {getAdminToken() && <p className="text-xs text-red-600">That token wasn&apos;t accepted.</p>}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-black/70">
        Keys you add here are checked with the provider, then stored <b>encrypted on the server</b>. They are never sent back to the browser, only the last four characters are shown.
        Several Gemini keys from <i>different</i> Google projects are rotated automatically. Free tiers are small (Gemini allows about 20 requests a day per model), so adding Groq, OpenRouter or Cerebras keys from separate accounts keeps the office answering when Gemini runs dry.
      </p>
      <form onSubmit={add} className="space-y-2 rounded-xl border border-black/10 p-3">
        <div className="flex gap-2">
          <select value={provider} onChange={(e) => setProvider(e.target.value)} className="rounded-lg border border-black/15 px-2 py-2 text-sm">
            {PROVIDER_OPTIONS.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
          <input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Paste API key" className="min-w-0 flex-1 rounded-lg border border-black/15 px-3 py-2 text-sm" />
        </div>
        <div className="flex gap-2">
          <input value={label} maxLength={40} onChange={(e) => setLabel(e.target.value)} placeholder="Label (optional)" className="min-w-0 flex-1 rounded-lg border border-black/15 px-3 py-2 text-sm" />
          <button disabled={busy || secret.trim().length < 20} className="rounded-lg bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
            {busy ? "Checking…" : "Verify and save"}
          </button>
        </div>
      </form>
      {msg && <p className={`text-sm ${msg.ok ? "text-green-700" : "text-red-600"}`}>{msg.text}</p>}
      <div className="space-y-1.5">
        {keys.length === 0 && <p className="text-sm text-black/50">No keys yet.</p>}
        {keys.map((k) => (
          <div key={k.id} className="flex items-center gap-2 rounded-lg border border-black/10 px-3 py-2 text-sm">
            <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-semibold uppercase">{k.provider}</span>
            <span className="font-mono text-xs">{k.masked}</span>
            <span className="truncate text-xs text-black/50">{k.label}</span>
            <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium ${k.status === "ok" ? "bg-green-100 text-green-800" : k.status === "error" ? "bg-red-100 text-red-800" : "bg-black/5 text-black/60"}`}>
              {k.source === "env" ? "from .env" : k.status}
            </span>
            {k.source === "saved" && (
              <>
                <button onClick={() => test(k.id)} className="rounded-full bg-black/10 px-2 py-0.5 text-[10px] font-medium">
                  Test
                </button>
                <button onClick={() => remove(k.id)} className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-700">
                  Remove
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------- team ----------------

type Member = { id: string; email: string; name: string; role: string };
type Invite = { code: string; role: string; expires: number };

function TeamTab() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [role, setRole] = useState("member");
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const cfg = await (await apiFetch("/auth/config")).json();
    setEnabled(cfg.auth);
    if (!cfg.auth) return;
    const r = await apiFetch("/auth/members");
    if (!r.ok) return setMsg("Only admins can manage the team.");
    const d = await r.json();
    setMembers(d.members);
    setInvites(d.invites);
  }, []);

  useEffect(() => {
    load().catch(() => setMsg("Can't reach the backend."));
  }, [load]);

  async function call(path: string, method: string, body?: unknown) {
    const r = await apiFetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) setMsg((await r.json().catch(() => ({}))).detail ?? "Couldn't do that");
    else setMsg("");
    await load();
  }

  if (enabled === null) return <p className="text-sm text-black/50">Loading…</p>;
  if (!enabled)
    return (
      <p className="text-sm text-black/70">
        Sign-in is off, so everyone using this server in the same browser shares one office. To run it for a team, set <code>AUTH_MODE=on</code> on the server and restart. The first person to sign up becomes the admin and can invite others here.
      </p>
    );
  return (
    <div className="space-y-4">
      <p className="text-sm text-black/70">
        Everyone in the workspace shares the agents, documents and tasks. <b>Admins</b> manage keys, databases and members. <b>Members</b> build and chat. <b>Viewers</b> can look and chat but not change things.
      </p>
      {msg && <p className="text-sm text-red-600">{msg}</p>}
      <div className="space-y-1.5">
        {members.map((m) => (
          <div key={m.id} className="flex items-center gap-2 rounded-lg border border-black/10 px-3 py-2 text-sm">
            <span className="font-medium">{m.name}</span>
            <span className="truncate text-xs text-black/50">{m.email}</span>
            <select value={m.role} onChange={(e) => call(`/auth/members/${m.id}`, "PATCH", { role: e.target.value })} className="ml-auto rounded-lg border border-black/15 px-2 py-1 text-xs">
              {["admin", "member", "viewer"].map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
            <button onClick={() => call(`/auth/members/${m.id}`, "DELETE")} className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-700">
              Remove
            </button>
          </div>
        ))}
      </div>
      <div className="space-y-2 rounded-xl border border-black/10 p-3">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium">Invite someone as</span>
          <select value={role} onChange={(e) => setRole(e.target.value)} className="rounded-lg border border-black/15 px-2 py-1 text-xs">
            {["member", "viewer", "admin"].map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <button onClick={() => call("/auth/invites", "POST", { role })} className="rounded-full bg-black px-3 py-1 text-xs font-medium text-white">
            Create code
          </button>
        </div>
        {invites.map((i) => (
          <div key={i.code} className="flex items-center gap-2 text-xs">
            <code className="rounded bg-black/5 px-2 py-1">{i.code}</code>
            <span className="text-black/50">{i.role}, expires {new Date(i.expires * 1000).toLocaleDateString()}</span>
            <button onClick={() => call(`/auth/invites/${i.code}`, "DELETE")} className="ml-auto rounded-full bg-black/10 px-2 py-0.5 text-[10px] font-medium">
              Revoke
            </button>
          </div>
        ))}
        <p className="text-xs text-black/50">Share the code. They choose &quot;Create an account&quot; and paste it in the invite field.</p>
      </div>
    </div>
  );
}

// ---------------- health ----------------

function HealthTab() {
  const [m, setM] = useState<Metrics | null>(null);
  const [usage, setUsage] = useState<{ today: number; daily_limit: number; per_minute: number } | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);

  useEffect(() => {
    apiFetch("/quota").then((r) => r.json()).then(setQuota).catch(() => {});
    apiFetch("/metrics").then((r) => r.json()).then(setM).catch(() => {});
    apiFetch("/usage").then((r) => r.json()).then(setUsage).catch(() => {});
  }, []);

  if (!m) return <p className="text-sm text-black/50">Loading…</p>;
  const skipped = m.plans_total ? Math.round((m.plans_without_llm / m.plans_total) * 100) : 0;
  const table = (title: string, rows: Row[], key: string) => (
    <div>
      <h3 className="mb-1 text-sm font-semibold">{title}</h3>
      <div className="overflow-x-auto rounded-xl border border-black/10">
        <table className="w-full text-left text-xs">
          <thead className="bg-black/5 text-black/60">
            <tr>
              {[key, "calls", "errors", "tokens in/out", "cost", "p50", "p95"].map((h) => (
                <th key={h} className="px-2 py-1.5 font-medium capitalize">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td className="px-2 py-2 text-black/50" colSpan={7}>
                  Nothing yet. Chat with someone first.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={String(r[key])} className="border-t border-black/5">
                <td className="px-2 py-1.5 font-medium">{String(r[key])}</td>
                <td className="px-2 py-1.5">{r.calls}</td>
                <td className="px-2 py-1.5">{r.errors}</td>
                <td className="px-2 py-1.5">
                  {r.tokens_in}/{r.tokens_out}
                </td>
                <td className="px-2 py-1.5">{Number(r.cost) ? `$${Number(r.cost).toFixed(4)}` : "free"}</td>
                <td className="px-2 py-1.5">{r.p50_ms} ms</td>
                <td className="px-2 py-1.5">{r.p95_ms} ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Card label="Model calls (24h)" value={m.llm_calls} hint={`${m.llm_errors} errors`} />
        <Card label="Tokens in / out" value={`${m.tokens_in} / ${m.tokens_out}`} />
        <Card label="Latency p50 / p95" value={`${m.p50_ms} / ${m.p95_ms} ms`} />
        <Card label="Est. cost" value={`$${m.cost.toFixed(4)}`} hint="0 on the free tier" />
        <Card label="Tool calls" value={m.tool_calls} />
        <Card label="Plans without an LLM" value={`${skipped}%`} hint={`${m.plans_without_llm} of ${m.plans_total} skipped the planner`} />
        {usage && <Card label="Your messages today" value={`${usage.today} / ${usage.daily_limit}`} hint={`max ${usage.per_minute} per minute`} />}
      </div>
      {quota && <QuotaPanel q={quota} />}
      {table("By agent", m.by_agent, "agent")}
      {table("By model", m.by_model, "model")}
      {table("By tool", m.by_tool, "name")}
    </div>
  );
}

function QuotaPanel({ q }: { q: Quota }) {
  const wait = (s: number) => (s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 90 ? `${Math.round(s / 60)} min` : `${s} s`);
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold">AI quota today</h3>
      <p className="mb-2 text-xs text-black/60">
        {q.level === "none" ? "No keys yet." : q.level === "exhausted" ? "Every model is out of quota right now." : `About ${q.estimated_left} requests left across all keys.`} This is an estimate from our own call counts. Keys from the same Google project share one quota.
      </p>
      <div className="space-y-2">
        {q.providers
          .filter((p) => p.keys > 0)
          .map((p) => (
            <div key={p.provider} className="overflow-x-auto rounded-xl border border-black/10">
              <div className="flex items-center justify-between bg-black/5 px-2 py-1.5 text-xs font-medium">
                <span>
                  {p.label} · {p.keys} key{p.keys === 1 ? "" : "s"}
                </span>
                <span className="text-black/50">~{p.daily_per_key}/day per key and model</span>
              </div>
              <table className="w-full text-left text-xs">
                <tbody>
                  {p.models.map((m) => (
                    <tr key={m.model} className="border-t border-black/5">
                      <td className="px-2 py-1.5 font-medium">{m.model}</td>
                      <td className="px-2 py-1.5">{m.calls_today} used</td>
                      <td className="px-2 py-1.5">{m.quota_hits ? `${m.quota_hits} limit hits` : ""}</td>
                      <td className="px-2 py-1.5 text-right">
                        {m.available_in_s ? <span className="text-red-700">empty, back in {wait(m.available_in_s)}</span> : <span className={m.estimated_left < 5 ? "text-amber-700" : "text-green-700"}>~{m.estimated_left} left</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </div>
    </div>
  );
}

// ---------------- quality (evals) ----------------

function QualityTab() {
  const [report, setReport] = useState<Report>({});
  const [status, setStatus] = useState<{ running: boolean; done: number; total: number; error: string }>({ running: false, done: 0, total: 0, error: "" });
  const [err, setErr] = useState("");
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadReport = useCallback(() => apiFetch("/evals/latest").then((r) => r.json()).then(setReport).catch(() => {}), []);

  useEffect(() => {
    loadReport();
    return () => {
      if (poll.current) clearInterval(poll.current);
    };
  }, [loadReport]);

  async function run(routingOnly: boolean) {
    setErr("");
    const r = await apiFetch(`/evals/run?routing_only=${routingOnly}`, { method: "POST" });
    if (!r.ok) return setErr((await r.json().catch(() => ({}))).detail ?? "Couldn't start the run");
    poll.current = setInterval(async () => {
      const s = await (await apiFetch("/evals/status")).json();
      setStatus(s);
      if (!s.running) {
        if (poll.current) clearInterval(poll.current);
        loadReport();
      }
    }, 1500);
  }

  const failing = report.cases?.filter((c) => !c.passed) ?? [];
  return (
    <div className="space-y-4">
      <p className="text-sm text-black/70">
        A fixed set of test conversations that checks the agents still behave: the right tools run, permissions hold, the Head picks the right people, and replies stay in format.
        Re-run it after changing a prompt or model.
      </p>
      <div className="flex flex-wrap gap-2">
        <button onClick={() => run(true)} disabled={status.running} className="rounded-full bg-black/10 px-3 py-1.5 text-xs font-medium disabled:opacity-40">
          Run routing check (instant)
        </button>
        <button onClick={() => run(false)} disabled={status.running} className="rounded-full bg-black px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40">
          Run full evaluation (uses API quota, ~1–2 min)
        </button>
        {status.running && (
          <span className="self-center text-xs text-black/60">
            Running… {status.done}/{status.total || "?"}
          </span>
        )}
      </div>
      {(err || status.error) && <p className="text-sm text-red-600">{err || status.error}</p>}
      <RetrievalCard />
      <FeedbackCard />
      {report.total ? (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Card label="Passed" value={`${report.passed}/${report.total}`} hint={report.started ? new Date(report.started * 1000).toLocaleString() : ""} />
            <Card label="Router precision" value={`${Math.round((report.routing_precision ?? 0) * 100)}%`} hint="right when it's confident" />
            <Card label="Router coverage" value={`${Math.round((report.routing_coverage ?? 0) * 100)}%`} hint="how often it needs no LLM" />
            <Card label="Duration" value={`${report.duration_s}s`} />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(report.by_category ?? {}).map(([k, v]) => (
              <span key={k} className={`rounded-full px-2.5 py-1 text-xs font-medium ${v.passed === v.total ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                {k} {v.passed}/{v.total}
              </span>
            ))}
          </div>
          {failing.length > 0 && (
            <div className="space-y-1.5">
              <h3 className="text-sm font-semibold">Failing cases</h3>
              {failing.map((c) => (
                <div key={c.id} className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs">
                  <b>{c.id}</b>: {c.checks.filter((x) => !x.ok).map((x) => `${x.name} (${x.detail})`).join("; ")}
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-black/50">No evaluation has been run yet.</p>
      )}
    </div>
  );
}
