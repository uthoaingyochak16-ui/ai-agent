import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Megaphone, Send, Sparkles, XCircle } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Empty, ErrorNote, Field, Input, SecretInput, Select, Spinner, Textarea, useConfirm, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { when } from "../lib/format";

/** Ads the AI prepares (audience, budget, text) for a person to start in Meta Ads Manager; nothing here spends money.
 *  Ad results are shown read-only when an ad account is connected. */
export function Ads() {
  const admin = useAuth().user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const [goal, setGoal] = useState("");
  const [program, setProgram] = useState("");
  const [budget, setBudget] = useState("");
  const list = useQuery({ queryKey: ["ads"], queryFn: () => api<any>("/api/social/ads") });
  const results = useQuery({ queryKey: ["ads-results"], queryFn: () => api<any>("/api/social/ads/results") });
  const programs = useQuery({ queryKey: ["programs-list"], queryFn: () => api<any>("/api/programs") });
  const suggest = useMutation({ mutationFn: () => api("/api/social/ads/suggest", { body: { goal, program_id: program ? +program : null, daily_budget: budget ? +budget : null } }),
    onSuccess: () => { toast("Ad draft ready"); setGoal(""); qc.invalidateQueries({ queryKey: ["ads"] }); } });
  const r = results.data;
  return (
    <div className="space-y-4">
      <p className="rounded-ctl border border-line bg-sunken px-3 py-2 text-sm">The AI prepares ad drafts; <b>nothing here creates an ad or spends money</b>. Review a draft, then start it yourself in Meta Ads Manager.</p>
      <section className="rounded-panel border border-line bg-surface p-3">
        <h2 className="mb-2 flex items-center gap-2 font-bold"><Sparkles className="h-4 w-4 text-accent-strong" aria-hidden />Prepare an ad</h2>
        <div className="grid gap-2 md:grid-cols-[1fr_200px_140px_auto]">
          <Input persistDraft={false} aria-label="Goal" placeholder="Goal, e.g. more people at Songhayon from Dhaka" value={goal} onChange={(e) => setGoal(e.target.value)} />
          <Select persistDraft={false} aria-label="Program" value={program} onChange={(e) => setProgram(e.target.value)}><option value="">No program</option>{(programs.data?.items ?? []).map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
          <Input persistDraft={false} aria-label="Budget per day (taka)" type="number" min={0} placeholder="Taka / day" value={budget} onChange={(e) => setBudget(e.target.value)} />
          <Button variant="primary" disabled={goal.trim().length < 3} loading={suggest.isPending} onClick={() => suggest.mutate()}>Prepare</Button>
        </div>
        <ErrorNote error={suggest.error} />
      </section>
      {list.isLoading ? <Spinner /> : !list.data?.drafts.length ? <Empty title="No ad drafts">Describe a goal above.</Empty> : (
        <div className="grid gap-3 lg:grid-cols-2">{list.data.drafts.map((a: any) => <AdCard key={a.id} a={a} manager={r?.ads_manager} />)}</div>
      )}
      <section className="rounded-panel border border-line bg-surface p-3">
        <h2 className="mb-2 flex items-center gap-2 font-bold"><Megaphone className="h-4 w-4 text-muted" aria-hidden />Ad results (last 30 days, read-only)</h2>
        {results.isLoading ? <Spinner /> : !r?.configured ? <p className="text-sm text-muted">{r?.reason}</p> : r.error ? <p className="text-sm text-danger">{r.error}</p> : !r.campaigns?.length ? <p className="text-sm text-muted">No campaign ran in the last 30 days.</p> : (
          <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="text-xs text-muted"><tr>{["Campaign", "Spend", "Reach", "Impressions", "Clicks", "CTR", "Results"].map((h) => <th key={h} className="px-2 py-1 text-left font-semibold">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-line">{r.campaigns.map((c: any, i: number) => <tr key={i}><td className="px-2 py-1 font-semibold">{c.campaign}</td>{["spend", "reach", "impressions", "clicks", "ctr", "results"].map((k) => <td key={k} className="px-2 py-1 tabular-nums">{c[k] ?? "–"}</td>)}</tr>)}</tbody></table></div>
        )}
        {r?.ads_manager && <a className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-accent-strong underline" href={r.ads_manager} target="_blank" rel="noreferrer">Open Ads Manager<ExternalLink className="h-3 w-3" aria-hidden /></a>}
        {admin && <AdsConnection accountId={list.data?.account_id ?? ""} hasToken={!!list.data?.has_token} />}
      </section>
    </div>
  );
}

function AdCard({ a, manager }: { a: any; manager?: string }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: a.name, text: a.text, daily_budget: a.daily_budget ?? "", days: a.days ?? "", note: a.note ?? "" });
  const save = useMutation({ mutationFn: (extra: Record<string, unknown>) => api(`/api/social/ads/${a.id}`, { method: "PUT", body: { ...f, daily_budget: f.daily_budget === "" ? null : +f.daily_budget, days: f.days === "" ? null : +f.days, ...extra } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ads"] }) });
  const au = a.audience ?? {};
  return (
    <div className="space-y-2 rounded-panel border border-line bg-surface p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><Input persistDraft={false} aria-label="Ad name" className="flex-1 font-semibold" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <Badge>{a.objective}</Badge><Badge tone={a.status === "approved" ? "ok" : a.status === "started" ? "blue" : "neutral"}>{a.status}</Badge></div>
      <p className="text-xs text-muted">Audience: {au.age_min ?? "?"}–{au.age_max ?? "?"} yrs · {(au.genders ?? ["all"]).join(", ")} · {(au.locations ?? []).join(", ") || "Bangladesh"}{au.interests?.length ? ` · ${au.interests.join(", ")}` : ""}</p>
      <Textarea persistDraft={false} rows={4} aria-label="Ad text" value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} />
      <div className="grid grid-cols-2 gap-2"><Field label="Taka per day">{(i) => <Input id={i} persistDraft={false} type="number" min={0} value={f.daily_budget} onChange={(e) => setF({ ...f, daily_budget: e.target.value })} />}</Field>
        <Field label="Days">{(i) => <Input id={i} persistDraft={false} type="number" min={1} value={f.days} onChange={(e) => setF({ ...f, days: e.target.value })} />}</Field></div>
      {a.note && <p className="text-xs text-muted"><b>Why:</b> {a.note}</p>}
      {f.daily_budget !== "" && f.days !== "" && <p className="text-xs">Total at most <b>{(+f.daily_budget * +f.days).toLocaleString()} taka</b>.</p>}
      <ErrorNote error={save.error} />
      <div className="flex flex-wrap justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={() => save.mutate({ status: "archived" })}>Archive</Button>
        <Button size="sm" loading={save.isPending} onClick={() => save.mutate({})}>Save</Button>
        {a.status === "draft" && <Button size="sm" variant="primary" onClick={() => save.mutate({ status: "approved" })}>Approve</Button>}
        {a.status === "approved" && <Button size="sm" variant="primary" onClick={() => { if (manager) window.open(manager, "_blank"); save.mutate({ status: "started" }); }}>Start in Ads Manager<ExternalLink className="h-3.5 w-3.5" aria-hidden /></Button>}
      </div>
    </div>
  );
}

function AdsConnection({ accountId, hasToken }: { accountId: string; hasToken: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [acc, setAcc] = useState(accountId);
  const [tok, setTok] = useState("");
  const save = useMutation({ mutationFn: async () => { await api("/api/ai/settings", { method: "PUT", body: { ads_account_id: acc.trim() } }); if (tok.trim()) await api("/api/social/ads-token", { method: "PUT", body: { token: tok } }); },
    onSuccess: () => { toast("Saved"); setTok(""); qc.invalidateQueries({ queryKey: ["ads"] }); qc.invalidateQueries({ queryKey: ["ads-results"] }); } });
  return (
    <details className="mt-3 text-sm"><summary className="cursor-pointer font-semibold text-muted">Connect an ad account (read-only results)</summary>
      <div className="mt-2 grid gap-2 md:grid-cols-[200px_1fr_auto]">
        <Input persistDraft={false} aria-label="Ad account id" placeholder="act_1234567890" value={acc} onChange={(e) => setAcc(e.target.value)} />
        <SecretInput aria-label="Access token with ads_read" configured={hasToken} value={tok} onChange={(e) => setTok(e.target.value)} placeholder="token with ads_read" />
        <Button loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
      </div><ErrorNote error={save.error} /></details>
  );
}

/** One approved WhatsApp template to many guests: only guests who wrote on WhatsApp and gave consent. */
export function Broadcasts() {
  const admin = useAuth().user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [f, setF] = useState({ name: "", template: "", language: "bn", params: "", preview: "", active_days: "" });
  const audience = { active_days: f.active_days ? +f.active_days : undefined };
  const size = useQuery({ queryKey: ["bc-audience", f.active_days], queryFn: () => api<{ count: number }>("/api/social/broadcasts/audience", { body: audience }) });
  const list = useQuery({ queryKey: ["broadcasts"], queryFn: () => api<any[]>("/api/social/broadcasts"), refetchInterval: (q) => (q.state.data?.some((b: any) => b.status === "sending") ? 3000 : false) });
  const create = useMutation({ mutationFn: () => api("/api/social/broadcasts", { body: { name: f.name, template: f.template, language: f.language, params: f.params.split("|").map((x) => x.trim()).filter(Boolean), preview: f.preview || null, audience } }),
    onSuccess: () => { toast("Saved as a draft"); setF({ ...f, name: "", preview: "" }); qc.invalidateQueries({ queryKey: ["broadcasts"] }); } });
  const act = useMutation({ mutationFn: ({ id, a }: { id: number; a: "start" | "cancel" }) => api(`/api/social/broadcasts/${id}/${a}`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["broadcasts"] }) });
  return (
    <div className="space-y-4">
      <p className="rounded-ctl border border-line bg-sunken px-3 py-2 text-sm">WhatsApp lets a business start a conversation only with a template Meta has approved (WhatsApp Manager → Message templates). Only guests who wrote to you on WhatsApp <b>and</b> agreed to be contacted receive it. Replies come to the Inbox.</p>
      {admin ? (
        <section className="space-y-2 rounded-panel border border-line bg-surface p-3">
          <h2 className="font-bold">New broadcast</h2>
          <div className="grid gap-2 md:grid-cols-3">
            <Field label="Name (for you)">{(i) => <Input id={i} persistDraft={false} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Songhayon reminder" />}</Field>
            <Field label="Template name" hint="exactly as approved, e.g. program_news">{(i) => <Input id={i} persistDraft={false} value={f.template} onChange={(e) => setF({ ...f, template: e.target.value.toLowerCase() })} />}</Field>
            <Field label="Template language">{(i) => <Select id={i} persistDraft={false} value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}><option value="bn">Bangla (bn)</option><option value="en">English (en)</option><option value="en_US">English US (en_US)</option></Select>}</Field>
          </div>
          <Field label="Template values" hint="{{1}}, {{2}} … in order, separated by |  e.g.  Songhayon | 21 October | IDEB Bhaban">{(i) => <Input id={i} persistDraft={false} value={f.params} onChange={(e) => setF({ ...f, params: e.target.value })} />}</Field>
          <Field label="What it says (for staff and the Inbox)">{(i) => <Textarea id={i} persistDraft={false} rows={2} value={f.preview} onChange={(e) => setF({ ...f, preview: e.target.value })} />}</Field>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Only guests active in the last … days (empty = all)">{(i) => <Input id={i} persistDraft={false} type="number" min={1} className="w-32" value={f.active_days} onChange={(e) => setF({ ...f, active_days: e.target.value })} />}</Field>
            <p className="pb-2 text-sm">Audience: <b>{size.data?.count ?? "…"}</b> guest(s)</p><span className="flex-1" />
            <Button variant="primary" disabled={!f.name.trim() || !/^[a-z0-9_]+$/.test(f.template)} loading={create.isPending} onClick={() => create.mutate()}>Save as draft</Button>
          </div>
          <ErrorNote error={create.error} />
        </section>
      ) : <p className="text-sm text-muted">Only an admin can create and send broadcasts.</p>}
      <ErrorNote error={act.error} />
      {list.isLoading ? <Spinner /> : !list.data?.length ? <Empty title="No broadcasts yet" /> : (
        <ul className="divide-y divide-line rounded-panel border border-line bg-surface">{list.data.map((b) => (
          <li key={b.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
            <div className="min-w-0 flex-1"><p className="font-semibold">{b.name} <span className="text-xs font-normal text-muted">· {b.template} ({b.language})</span></p>
              <p className="truncate text-xs text-muted">{b.preview}</p></div>
            <Badge tone={b.status === "done" ? "ok" : b.status === "sending" ? "blue" : b.status === "cancelled" ? "neutral" : "warn"}>{b.status}</Badge>
            <span className="text-xs tabular-nums text-muted">{b.sent}/{b.total} sent{b.failed ? ` · ${b.failed} failed` : ""}{b.finished_at ? ` · ${when(b.finished_at)}` : ""}</span>
            {admin && b.status === "draft" && <Button size="sm" variant="primary" onClick={async () => (await ask({ title: `Send "${b.name}" now?`, message: `The template "${b.template}" goes to every guest in its audience. This cannot be undone.`, confirmLabel: "Send" })) && act.mutate({ id: b.id, a: "start" })}><Send className="h-4 w-4" aria-hidden />Send</Button>}
            {admin && (b.status === "draft" || b.status === "sending") && <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: b.id, a: "cancel" })}><XCircle className="h-4 w-4" aria-hidden />Cancel</Button>}
          </li>))}</ul>
      )}
    </div>
  );
}
