"use client";

import { useCallback, useEffect, useState } from "react";
import { API, apiFetch } from "@/lib/api";

type Deliver = { slack: boolean; webhook: string | null; emails: string[] };
type Schedule = { id: string; agent: string; message: string; when: string; deliver: Deliver; enabled: boolean; next_run: number | null; last_run: number | null; last_status: string | null; last_result: string | null };
type Trigger = { id: string; name: string; agent: string; token: string; deliver: Deliver; calls: number; last_used: number | null };
type Agent = { id: string; name: string };

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const field = "rounded-lg border border-black/15 px-2 py-1.5 text-sm";

function Destinations({ d }: { d: Deliver }) {
  const parts = [d.slack && "Slack", d.webhook && `webhook (${d.webhook})`, d.emails.length > 0 && `email ${d.emails.join(", ")}`].filter(Boolean);
  return <span className="text-black/50">{parts.length ? `→ ${parts.join(", ")}` : "→ shown here only"}</span>;
}

/** Where results go: Slack, a webhook or email. */
function DeliverFields({ value, onChange, emailReady }: { value: { slack_url: string; webhook_url: string; emails: string }; onChange: (v: { slack_url: string; webhook_url: string; emails: string }) => void; emailReady: boolean }) {
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      <input className={field} placeholder="Slack incoming-webhook link (optional)" value={value.slack_url} onChange={(e) => onChange({ ...value, slack_url: e.target.value })} />
      <input className={field} placeholder="Webhook URL (optional)" value={value.webhook_url} onChange={(e) => onChange({ ...value, webhook_url: e.target.value })} />
      <input className={field} disabled={!emailReady} title={emailReady ? "" : "Set SMTP_HOST on the server to send email"} placeholder={emailReady ? "Emails, comma separated" : "Email (server not set up)"} value={value.emails} onChange={(e) => onChange({ ...value, emails: e.target.value })} />
    </div>
  );
}

const noDeliver = { slack_url: "", webhook_url: "", emails: "" };
const toDeliver = (v: typeof noDeliver) => ({ slack_url: v.slack_url.trim(), webhook_url: v.webhook_url.trim(), emails: v.emails.split(",").map((e) => e.trim()).filter(Boolean) });

export default function AutomationsTab() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [emailReady, setEmailReady] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [s, setS] = useState({ agent: "", message: "", kind: "daily", every: 60, at: "09:00", days: [0, 1, 2, 3, 4], deliver: noDeliver });
  const [t, setT] = useState({ name: "", agent: "", deliver: noDeliver });

  const load = useCallback(async () => {
    const [a, sc, tr] = await Promise.all([apiFetch("/characters").then((r) => r.json()), apiFetch("/schedules").then((r) => r.json()), apiFetch("/triggers").then((r) => r.json())]);
    setAgents(a);
    setSchedules(sc.schedules);
    setTriggers(tr.triggers);
    setEmailReady(sc.email_ready);
    setS((x) => (x.agent ? x : { ...x, agent: a[0]?.id ?? "" }));
    setT((x) => (x.agent ? x : { ...x, agent: a[0]?.id ?? "" }));
  }, []);

  useEffect(() => {
    load().catch(() => setMsg({ ok: false, text: "Can't reach the backend." }));
  }, [load]);

  async function call(path: string, method: string, body?: unknown, okText?: string) {
    const r = await apiFetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const data = await r.json().catch(() => ({}));
    setMsg(r.ok ? (okText ? { ok: true, text: okText } : null) : { ok: false, text: typeof data.detail === "string" ? data.detail : "Couldn't do that" });
    await load();
    return r.ok ? data : null;
  }

  async function addSchedule(e: React.FormEvent) {
    e.preventDefault();
    const ok = await call("/schedules", "POST", { agent_id: s.agent, message: s.message, kind: s.kind, every_min: s.every, at: s.at, days: s.days, tz_offset_min: -new Date().getTimezoneOffset(), deliver: toDeliver(s.deliver) }, "Schedule saved.");
    if (ok) setS({ ...s, message: "", deliver: noDeliver });
  }

  async function runNow(id: string) {
    setMsg({ ok: true, text: "Running… (the agent is answering)" });
    const r = await apiFetch(`/schedules/${id}/run`, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    setMsg({ ok: r.ok && d.status === "ok", text: r.ok ? (d.status === "ok" ? "Done. The answer was delivered." : d.status) : "Couldn't run it" });
    await load();
  }

  async function addTrigger(e: React.FormEvent) {
    e.preventDefault();
    const ok = await call("/triggers", "POST", { name: t.name, agent_id: t.agent, deliver: toDeliver(t.deliver) }, "Trigger created. Copy its link below.");
    if (ok) setT({ ...t, name: "", deliver: noDeliver });
  }

  async function download() {
    const r = await apiFetch("/export");
    if (!r.ok) return setMsg({ ok: false, text: "Couldn't export." });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = "deskmates-export.zip";
    a.click();
  }

  async function upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    const r = await apiFetch("/import", { method: "POST", body: form });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) return setMsg({ ok: false, text: d.detail ?? "Import failed" });
    setMsg({ ok: true, text: `Imported ${d.agents.length} agent(s) and ${d.docs.length} document(s).${d.skipped.length ? ` Skipped: ${d.skipped.join("; ")}` : ""}` });
    await load();
  }

  const name = (id: string) => agents.find((a) => a.id === id)?.name ?? id;
  const when = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : "never");

  return (
    <div className="space-y-6">
      {msg && <p className={`text-sm ${msg.ok ? "text-green-700" : "text-red-600"}`}>{msg.text}</p>}

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Scheduled tasks</h3>
        <p className="text-xs text-black/60">Ask an agent something on a timetable and send the answer to Slack, a webhook or email. Slack links are stored encrypted and never shown again.</p>
        {schedules.map((x) => (
          <div key={x.id} className="space-y-1 rounded-lg border border-black/10 px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <b>{name(x.agent)}</b>
              <span>{x.when}</span>
              <Destinations d={x.deliver} />
              <span className="ml-auto flex gap-1">
                <button onClick={() => runNow(x.id)} className="rounded-full bg-black/10 px-2 py-0.5 font-medium">
                  Run now
                </button>
                <button onClick={() => call(`/schedules/${x.id}?enabled=${!x.enabled}`, "PATCH")} className="rounded-full bg-black/10 px-2 py-0.5 font-medium">
                  {x.enabled ? "Pause" : "Resume"}
                </button>
                <button onClick={() => call(`/schedules/${x.id}`, "DELETE")} className="rounded-full bg-red-50 px-2 py-0.5 font-medium text-red-700">
                  Delete
                </button>
              </span>
            </div>
            <p className="text-black/70">“{x.message}”</p>
            <p className="text-black/50">
              {x.enabled ? `Next: ${when(x.next_run)}` : "Paused"} · Last: {when(x.last_run)} {x.last_status && <span className={x.last_status === "ok" ? "text-green-700" : "text-red-700"}>({x.last_status})</span>}
            </p>
            {x.last_result && <p className="line-clamp-2 text-black/60">{x.last_result}</p>}
          </div>
        ))}
        <form onSubmit={addSchedule} className="space-y-2 rounded-xl border border-black/10 p-3">
          <div className="flex flex-wrap gap-2">
            <select className={field} value={s.agent} onChange={(e) => setS({ ...s, agent: e.target.value })}>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
            <select className={field} value={s.kind} onChange={(e) => setS({ ...s, kind: e.target.value })}>
              <option value="daily">Every day</option>
              <option value="weekly">On weekdays I pick</option>
              <option value="interval">Every N minutes</option>
            </select>
            {s.kind === "interval" ? (
              <input type="number" min={5} className={`${field} w-24`} value={s.every} onChange={(e) => setS({ ...s, every: Number(e.target.value) })} />
            ) : (
              <input type="time" className={field} value={s.at} onChange={(e) => setS({ ...s, at: e.target.value })} />
            )}
          </div>
          {s.kind === "weekly" && (
            <div className="flex gap-1">
              {DAYS.map((d, i) => (
                <button type="button" key={d} onClick={() => setS({ ...s, days: s.days.includes(i) ? s.days.filter((x) => x !== i) : [...s.days, i] })} className={`rounded-full px-2 py-0.5 text-xs ${s.days.includes(i) ? "bg-black text-white" : "bg-black/10"}`}>
                  {d}
                </button>
              ))}
            </div>
          )}
          <textarea className={`${field} w-full`} rows={2} maxLength={2000} placeholder="What should they do? e.g. Summarise yesterday's sales from the documents." value={s.message} onChange={(e) => setS({ ...s, message: e.target.value })} />
          <DeliverFields value={s.deliver} onChange={(deliver) => setS({ ...s, deliver })} emailReady={emailReady} />
          <button disabled={!s.agent || !s.message.trim()} className="rounded-lg bg-black px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Add schedule
          </button>
        </form>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Triggers (let other tools ask an agent)</h3>
        <p className="text-xs text-black/60">
          Each trigger is a secret link. POST <code>{`{"message": "..."}`}</code> to it from Zapier, GitHub, a mail rule or a script and you get <code>{`{"reply": "..."}`}</code> back. Use the same link as a <b>Slack slash command</b> request URL and the answer is posted back into the channel. Anyone with the link can use it, so keep it private and delete it if it leaks.
        </p>
        {triggers.map((x) => (
          <div key={x.id} className="space-y-1 rounded-lg border border-black/10 px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <b>{x.name}</b>
              <span>asks {name(x.agent)}</span>
              <Destinations d={x.deliver} />
              <button onClick={() => call(`/triggers/${x.id}`, "DELETE")} className="ml-auto rounded-full bg-red-50 px-2 py-0.5 font-medium text-red-700">
                Delete
              </button>
            </div>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-black/5 px-2 py-1">{`${API}/hooks/${x.token}`}</code>
              <button onClick={() => navigator.clipboard.writeText(`${API}/hooks/${x.token}`)} className="rounded-full bg-black/10 px-2 py-0.5 font-medium">
                Copy
              </button>
            </div>
            <p className="text-black/50">
              Used {x.calls} time{x.calls === 1 ? "" : "s"} · last {when(x.last_used)}
            </p>
          </div>
        ))}
        <form onSubmit={addTrigger} className="space-y-2 rounded-xl border border-black/10 p-3">
          <div className="flex flex-wrap gap-2">
            <input className={`${field} min-w-0 flex-1`} placeholder="Name, e.g. Slack /ask-pam" value={t.name} maxLength={60} onChange={(e) => setT({ ...t, name: e.target.value })} />
            <select className={field} value={t.agent} onChange={(e) => setT({ ...t, agent: e.target.value })}>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
          <DeliverFields value={t.deliver} onChange={(deliver) => setT({ ...t, deliver })} emailReady={emailReady} />
          <button disabled={!t.agent} className="rounded-lg bg-black px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Create trigger
          </button>
        </form>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Backup and move</h3>
        <p className="text-xs text-black/60">Export your agents and documents as one file, or import one from another workspace. API keys, database connections, chats and notes are not included.</p>
        <div className="flex flex-wrap gap-2">
          <button onClick={download} className="rounded-full bg-black px-3 py-1.5 text-xs font-medium text-white">
            Export everything
          </button>
          <label className="cursor-pointer rounded-full bg-black/10 px-3 py-1.5 text-xs font-medium">
            Import a file…
            <input
              type="file"
              accept=".zip"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload(f);
                e.target.value = "";
              }}
            />
          </label>
        </div>
      </section>
    </div>
  );
}
