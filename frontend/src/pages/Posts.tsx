import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ExternalLink, ImagePlus, Lightbulb, PenLine, RefreshCw, Send, Sparkles, Trash2, TrendingUp } from "lucide-react";
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { Ads, Broadcasts } from "./AdsBroadcast";
import SocialCalendar, { PostModal } from "./SocialCalendar";
import SocialTrends from "./SocialTrends";
import { Badge, Button, ChannelBadge, Empty, ErrorNote, Field, IconButton, Input, PageHeader, Select, Spinner, Tabs, Textarea, cx, useConfirm, useToast } from "../components/ui";
import { api } from "../lib/api";
import { ago, when } from "../lib/format";

type Tab = "calendar" | "posts" | "create" | "insights" | "trends" | "ads" | "whatsapp";
type Account = { channel: "facebook" | "instagram"; account_id: string; label: string; status: string; has_token: boolean };
const STATUS: Record<string, "ok" | "warn" | "danger" | "neutral" | "blue"> = { published: "ok", scheduled: "blue", draft: "neutral", publishing: "warn", failed: "danger", pending_approval: "warn" };
const plat = (c: string) => (c === "instagram" ? "instagram" : "messenger");
const n = (v: unknown) => (typeof v === "number" ? v.toLocaleString() : "–");

/** Posts on the Facebook Pages and Instagram accounts: write (with the AI), schedule, publish; see how each did and
 *  what works best (when, which topic, which format), with the AI's plan for the next weeks. */
export default function Posts() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "calendar";
  return (
    <>
      <PageHeader title="Social media" subtitle="Plan, publish and measure posts on every Page and Instagram account; audience, trends, ads and WhatsApp broadcasts." />
      <Tabs<Tab> value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })}
        tabs={[{ id: "calendar", label: "Calendar" }, { id: "posts", label: "Posts" }, { id: "create", label: "Create" }, { id: "insights", label: "Post insights" },
               { id: "trends", label: "Audience & trends" }, { id: "ads", label: "Ads" }, { id: "whatsapp", label: "WhatsApp broadcast" }]} />
      {tab === "calendar" && <SocialCalendar />}
      {tab === "posts" && <PostList onCreate={() => setParams({ tab: "create" })} />}
      {tab === "create" && <Composer onDone={() => setParams({ tab: "calendar" })} />}
      {tab === "insights" && <Insights />}
      {tab === "trends" && <SocialTrends />}
      {tab === "ads" && <Ads />}
      {tab === "whatsapp" && <Broadcasts />}
    </>
  );
}

function PostList({ onCreate }: { onCreate: () => void }) {
  const admin = useAuth().user?.role === "admin";
  const [open, setOpen] = useState<any>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [status, setStatus] = useState("");
  const [channel, setChannel] = useState("");
  const [sort, setSort] = useState("recent");
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["posts", status, channel, sort, q], queryFn: () => api<any>("/api/posts", { params: { status, channel, sort, q, limit: 150 } }), refetchInterval: 30000 });
  const refresh = () => qc.invalidateQueries({ queryKey: ["posts"] });
  const sync = useMutation({ mutationFn: () => api<any>("/api/posts/sync", { method: "POST" }),
    onSuccess: (r) => { const ok = r.accounts.filter((a: any) => !a.error); toast(`${ok.reduce((s: number, a: any) => s + (a.posts ?? 0), 0)} post(s) read from ${ok.length} account(s)${r.accounts.length > ok.length ? ` · ${r.accounts.length - ok.length} failed` : ""}`); refresh(); } });
  const publish = useMutation({ mutationFn: (id: number) => api<any>(`/api/posts/${id}/publish`, { method: "POST" }), onSuccess: (r) => { toast(r.status === "published" ? "Published" : `Not published: ${r.error}`); refresh(); } });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/posts/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const review = useMutation({ mutationFn: ({ id, a }: { id: number; a: "approve" | "reject" }) => api<any>(`/api/posts/${id}/${a}`, { body: {} }), onSuccess: (r) => { toast(`Now ${r.status.replace("_", " ")}`); refresh(); } });
  const plan = useMutation({ mutationFn: () => api<any>("/api/posts/auto-plan", { body: { language: "bn" } }), onSuccess: (r) => { toast(r.error ? r.error : `${r.drafts} draft(s) from the AI's plan`); refresh(); } });
  const progDrafts = useMutation({ mutationFn: () => api<any>("/api/posts/program-drafts", { method: "POST" }), onSuccess: (r) => { toast(`${r.drafts} program draft(s)`); refresh(); } });
  const items: any[] = list.data?.items ?? [];
  const c = list.data?.counts ?? {};
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5 rounded-panel border border-line bg-surface p-1.5">
        <Select aria-label="Status" persistDraft={false} className="h-8 w-auto py-0 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All posts</option><option value="pending_approval">Waiting for approval ({c.pending_approval ?? 0})</option><option value="scheduled">Scheduled ({c.scheduled ?? 0})</option><option value="draft">Drafts ({c.draft ?? 0})</option>
          <option value="published">Published ({c.published ?? 0})</option><option value="failed">Failed ({c.failed ?? 0})</option></Select>
        <Select aria-label="Platform" persistDraft={false} className="h-8 w-auto py-0 text-xs" value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">FB + IG</option><option value="facebook">Facebook</option><option value="instagram">Instagram</option></Select>
        <Select aria-label="Sort" persistDraft={false} className="h-8 w-auto py-0 text-xs" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="recent">Newest first</option><option value="best">Best performing first</option></Select>
        <Input aria-label="Search posts" persistDraft={false} type="search" className="h-8 min-w-[140px] flex-1 py-0 text-xs" placeholder="Search text or topic" value={q} onChange={(e) => setQ(e.target.value)} />
        <Button size="sm" loading={sync.isPending} onClick={() => sync.mutate()} title="Read the latest posts and their numbers from Facebook and Instagram (also every 3 hours)"><RefreshCw className="h-4 w-4" aria-hidden />Refresh numbers</Button>
        <Button size="sm" loading={progDrafts.isPending} onClick={() => progDrafts.mutate()} title="An invitation and a reminder draft for each upcoming program without a post">Program drafts</Button>
        <Button size="sm" loading={plan.isPending} onClick={() => plan.mutate()} title="The AI plans the next 2 weeks from your numbers and saves the posts as drafts (nothing is published)"><Sparkles className="h-4 w-4" aria-hidden />AI: plan 2 weeks</Button>
        <Button size="sm" variant="primary" onClick={onCreate}><PenLine className="h-4 w-4" aria-hidden />Create</Button>
      </div>
      {(c.pending_approval ?? 0) > 0 && <p className="rounded-ctl border border-warn/50 bg-warn-soft px-3 py-1.5 text-sm text-warn">{c.pending_approval} post(s) wait for an admin's approval{admin ? ": approve or send them back below." : "."}</p>}
      <ErrorNote error={list.error || sync.error || publish.error || del.error || review.error || plan.error || progDrafts.error} />
      {list.isLoading ? <Spinner /> : !items.length ? <Empty title="No posts yet">Click “Refresh numbers” to read the posts already on your Pages, or create one.</Empty> : (
        <div className="overflow-x-auto rounded-panel border border-line bg-surface">
          <table className="w-full text-sm">
            <thead className="bg-sunken text-xs text-muted"><tr>
              <th className="px-2 py-1.5 text-left font-semibold">Post</th><th className="px-2 py-1.5 text-left font-semibold">Where / when</th>
              <th className="px-2 py-1.5 text-right font-semibold" title="Reactions + comments + shares + saves">Engagement</th>
              <th className="px-2 py-1.5 text-right font-semibold">Reach</th><th className="px-2 py-1.5 text-right font-semibold" title="Engagement per reach">Rate</th>
              <th className="px-2 py-1.5 text-right font-semibold">👍 / 💬 / ↗</th><th className="w-24" /></tr></thead>
            <tbody className="divide-y divide-line">{items.map((p) => (
              <tr key={p.id} className="align-top hover:bg-sunken/40">
                <td className="max-w-[420px] px-2 py-1.5">
                  <div className="flex gap-2">{p.image_url && <img src={p.image_url} alt="" className="h-10 w-10 shrink-0 rounded object-cover" loading="lazy" />}
                    <div className="min-w-0"><button type="button" className="line-clamp-2 break-words text-left hover:underline" onClick={() => setOpen(p)}>{p.message || <span className="text-muted">(no text)</span>}</button>
                      <p className="mt-0.5 flex flex-wrap gap-1">{p.topic && <Badge>{p.topic}</Badge>}{p.media_type && <Badge>{p.media_type}</Badge>}{p.ai_written && <Badge tone="blue">AI</Badge>}{p.ab_variant && <Badge tone="blue">A/B {p.ab_variant}</Badge>}
                        {p.status !== "published" && <Badge tone={STATUS[p.status] ?? "neutral"}>{p.status}</Badge>}</p>
                      {p.error && <p className="text-xs text-danger">{p.error}</p>}</div></div>
                </td>
                <td className="whitespace-nowrap px-2 py-1.5 text-xs"><ChannelBadge channel={plat(p.channel)} /><span className="block text-muted">{p.account_label}</span>
                  <span className="block text-muted">{p.status === "scheduled" ? <>⏰ {when(p.scheduled_at)}</> : p.published_at ? ago(p.published_at) : p.suggested_at ? <>💡 {when(p.suggested_at)}</> : "draft"}</span></td>
                <td className="px-2 py-1.5 text-right font-semibold tabular-nums">{p.status === "published" ? n(p.engagement) : "–"}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{n(p.metrics.reach)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{p.rate != null ? `${(p.rate * 100).toFixed(1)}%` : "–"}</td>
                <td className="whitespace-nowrap px-2 py-1.5 text-right text-xs tabular-nums text-muted">{n(p.metrics.reactions)} / {n(p.metrics.comments)} / {n(p.metrics.shares)}</td>
                <td className="px-2 py-1.5"><div className="flex justify-end gap-1">
                  {p.permalink && <a className="inline-flex h-8 w-8 items-center justify-center rounded-ctl text-muted hover:bg-sunken" href={p.permalink} target="_blank" rel="noreferrer" aria-label="Open the post"><ExternalLink className="h-4 w-4" aria-hidden /></a>}
                  {p.status === "pending_approval" && admin && <>
                    <Button size="sm" variant="primary" onClick={() => review.mutate({ id: p.id, a: "approve" })}>Approve</Button>
                    <Button size="sm" variant="ghost" onClick={() => review.mutate({ id: p.id, a: "reject" })}>Back</Button></>}
                  {["draft", "scheduled", "failed"].includes(p.status) && <>
                    <IconButton label="Publish now" className="h-8 w-8" onClick={async () => (await ask({ title: "Publish this post now?", message: (p.message || "").slice(0, 200), confirmLabel: "Publish" })) && publish.mutate(p.id)}><Send className="h-4 w-4" aria-hidden /></IconButton>
                    <IconButton label="Delete this draft" className="h-8 w-8" onClick={async () => (await ask({ title: "Delete this post?", message: "It was not published.", confirmLabel: "Delete", danger: true })) && del.mutate(p.id)}><Trash2 className="h-4 w-4" aria-hidden /></IconButton></>}
                </div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {open && <PostModal p={open} admin={admin} onClose={() => setOpen(null)} onChanged={() => { setOpen(null); refresh(); }} />}
      {admin && <AutomationSettings />}
    </div>
  );
}

/** Who may publish, and what the AI prepares by itself (always drafts: a person publishes). */
function AutomationSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const st = useQuery({ queryKey: ["ai-settings"], queryFn: () => api<any>("/api/ai/settings") });
  const save = useMutation({ mutationFn: (b: Record<string, unknown>) => api("/api/ai/settings", { method: "PUT", body: b }), onSuccess: () => { toast("Saved"); qc.invalidateQueries({ queryKey: ["ai-settings"] }); } });
  const v = st.data ?? {};
  const rows: [string, string, string][] = [
    ["post_approval_required", "Staff posts need an admin's approval", "A non-admin's scheduled or published post waits until an admin approves it."],
    ["auto_program_drafts", "Draft posts for new programs", "Every night: an invitation and a day-before reminder draft for each upcoming program."],
    ["auto_plan_weekly", "Weekly AI plan as drafts", "Saturdays: the AI plans the next 2 weeks and saves the posts as drafts (uses AI credit)."],
    ["weekly_report_enabled", "Weekly report", "Saturdays 09:00: a short report to the alert webhook (Audience & trends shows it too)."],
  ];
  return (
    <details className="rounded-panel border border-line bg-surface p-3 text-sm"><summary className="cursor-pointer font-semibold">Automation and approval</summary>
      <ul className="mt-2 divide-y divide-line">{rows.map(([k, label, hint]) => <li key={k} className="flex items-center justify-between gap-3 py-1.5">
        <span><span className="font-semibold">{label}</span><span className="block text-xs text-muted">{hint}</span></span>
        <input type="checkbox" aria-label={label} className="h-4 w-4" checked={!!v[k]} disabled={save.isPending || st.isLoading} onChange={(e) => save.mutate({ [k]: e.target.checked })} /></li>)}
        <li className="flex items-center justify-between gap-3 py-1.5"><span className="font-semibold">Report language</span>
          <Select aria-label="Report language" persistDraft={false} className="h-8 w-auto py-0 text-xs" value={v.report_language ?? "bn"} onChange={(e) => save.mutate({ report_language: e.target.value })}><option value="bn">বাংলা</option><option value="en">English</option></Select></li>
      </ul><ErrorNote error={save.error} /></details>
  );
}

function Composer({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const accounts = useQuery({ queryKey: ["post-accounts"], queryFn: () => api<Account[]>("/api/posts/accounts") });
  const programs = useQuery({ queryKey: ["programs-list"], queryFn: () => api<any>("/api/programs") });
  const [picked, setPicked] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [variantB, setVariantB] = useState<string | null>(null);
  const [format, setFormat] = useState("post");
  const [link, setLink] = useState("");
  const [image, setImage] = useState("");
  const [at, setAt] = useState("");
  const [ai, setAi] = useState(false);
  const [brief, setBrief] = useState("");
  const [program, setProgram] = useState("");
  const [lang, setLang] = useState("bn");
  const file = useRef<HTMLInputElement>(null);
  const accs = (accounts.data ?? []).filter((a) => a.has_token);
  const targets = accs.filter((a) => picked.includes(`${a.channel}:${a.account_id}`));
  const needsImage = targets.some((a) => a.channel === "instagram") && !image;
  const upload = useMutation({ mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); return api<{ url: string; local: boolean }>("/api/posts/upload", { form: fd }); },
    onSuccess: (r) => { setImage(r.url); if (r.local) toast("Uploaded, but the server's public address is localhost: Facebook / Instagram cannot fetch it. Set PUBLIC_BASE_URL, or paste an image link.", "danger"); } });
  const write = useMutation({ mutationFn: () => api<any>("/api/posts/draft", { body: { brief, platform: targets[0]?.channel ?? "facebook", language: lang, program_id: program ? +program : null, format } }) });
  const create = useMutation({
    mutationFn: (mode: "draft" | "schedule" | "now") => api<any[]>("/api/posts", { body: { targets: targets.map((a) => ({ channel: a.channel, account_id: a.account_id })), message: text, variants: variantB?.trim() ? [variantB] : [], link: link || null,
      image_url: image || null, schedule_at: mode === "schedule" && at ? new Date(at).toISOString() : null, publish_now: mode === "now", ai_written: ai } }),
    onSuccess: (r) => { const bad = r.filter((p) => p.status === "failed"); toast(bad.length ? `${bad.length} not published: ${bad[0].error}` : `${r.length} post(s) ${r[0]?.status}`); qc.invalidateQueries({ queryKey: ["posts"] }); if (!bad.length) onDone(); },
  });
  const ready = targets.length > 0 && (text.trim() || image) && !needsImage;
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-3 rounded-panel border border-line bg-surface p-4">
        <div><p className="mb-1 text-sm font-semibold">Post to</p>
          {accounts.isLoading ? <Spinner /> : !accs.length ? <p className="text-sm text-muted">No Facebook Page or Instagram account is connected (Pages and numbers).</p> : (
            <div className="flex flex-wrap gap-2">{accs.map((a) => { const k = `${a.channel}:${a.account_id}`; const on = picked.includes(k); return (
              <button key={k} type="button" aria-pressed={on} onClick={() => setPicked(on ? picked.filter((x) => x !== k) : [...picked, k])}
                className={cx("flex items-center gap-2 rounded-ctl border px-2.5 py-1.5 text-sm", on ? "border-accent bg-accent-soft font-semibold" : "border-line hover:bg-sunken")}>
                <ChannelBadge channel={plat(a.channel)} />{a.label}{a.status === "error" && <span className="text-xs text-danger">(problem)</span>}</button>); })}</div>)}
        </div>
        <Field label="Text">{(i) => <Textarea id={i} persistDraft={false} rows={7} value={text} onChange={(e) => { setText(e.target.value); setAi(false); }} placeholder="What do you want to say?" />}</Field>
        <p className="-mt-2 text-right text-xs text-muted">{text.length} characters{targets.some((a) => a.channel === "instagram") && text.length > 2200 ? " · Instagram allows 2,200" : ""}</p>
        {variantB === null ? <Button size="sm" variant="ghost" onClick={() => setVariantB("")}>+ A/B test: add version B</Button> : (
          <Field label="Version B (A/B test)" hint="Goes out one day after version A at the same time; Post insights then shows which one won.">{(i) =>
            <div className="flex gap-2"><Textarea id={i} persistDraft={false} rows={4} value={variantB} onChange={(e) => setVariantB(e.target.value)} />
              <Button size="sm" variant="ghost" onClick={() => setVariantB(null)}>Remove</Button></div>}</Field>)}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Image" hint="JPG / PNG / WebP up to 8 MB, or a public image link. Instagram needs one.">{(i) => <div className="flex gap-2">
            <Input id={i} persistDraft={false} value={image} placeholder="https://…" onChange={(e) => setImage(e.target.value)} />
            <input ref={file} type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
            <IconButton label="Upload an image" onClick={() => file.current?.click()} disabled={upload.isPending}><ImagePlus className="h-4 w-4" aria-hidden /></IconButton></div>}</Field>
          <Field label="Link (Facebook)" hint="A registration form or article; Instagram does not show links in posts.">{(i) => <Input id={i} persistDraft={false} value={link} placeholder="https://…" onChange={(e) => setLink(e.target.value)} />}</Field>
        </div>
        {image && <img src={image} alt="The post's image" className="max-h-48 rounded-ctl border border-line object-contain" />}
        {needsImage && <p className="text-sm text-danger">Instagram posts need an image.</p>}
        <div className="flex flex-wrap items-end gap-2 border-t border-line pt-3">
          <Field label="Schedule (your local time)" className="min-w-[220px]">{(i) => <Input id={i} persistDraft={false} type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />}</Field>
          <span className="flex-1" />
          <Button disabled={!ready} loading={create.isPending && create.variables === "draft"} onClick={() => create.mutate("draft")}>Save draft</Button>
          <Button disabled={!ready || !at} loading={create.isPending && create.variables === "schedule"} onClick={() => create.mutate("schedule")}><CalendarClock className="h-4 w-4" aria-hidden />Schedule</Button>
          <Button variant="primary" disabled={!ready} loading={create.isPending && create.variables === "now"} onClick={async () => (await ask({ title: `Publish now to ${targets.length} account(s)?`, message: targets.map((a) => a.label).join(", "), confirmLabel: "Publish" })) && create.mutate("now")}><Send className="h-4 w-4" aria-hidden />Publish now</Button>
        </div>
        <ErrorNote error={create.error || upload.error} />
      </div>
      <aside className="space-y-3 rounded-panel border border-line bg-surface p-4 xl:sticky xl:top-4 xl:self-start">
        <p className="flex items-center gap-2 font-bold"><Sparkles className="h-4 w-4 text-accent-strong" aria-hidden />Write with AI</p>
        <p className="text-xs text-muted">Say what the post is for. The AI uses the program's real details and what worked before on your Pages.</p>
        <Textarea persistDraft={false} rows={3} aria-label="What is the post about?" placeholder="e.g. Invite people to Songhayon, remind them it is free" value={brief} onChange={(e) => setBrief(e.target.value)} />
        <div className="grid grid-cols-2 gap-2">
          <Select aria-label="About which program" persistDraft={false} value={program} onChange={(e) => setProgram(e.target.value)}><option value="">Any program</option>{(programs.data?.items ?? []).map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
          <Select aria-label="Language" persistDraft={false} value={lang} onChange={(e) => setLang(e.target.value)}><option value="bn">বাংলা</option><option value="en">English</option></Select>
          <Select aria-label="Format" persistDraft={false} className="col-span-2" value={format} onChange={(e) => setFormat(e.target.value)}><option value="post">Normal post</option><option value="reel">Reel idea + script</option><option value="story">Story frames</option></Select>
        </div>
        <Button variant="primary" className="w-full" disabled={brief.trim().length < 3} loading={write.isPending} onClick={() => write.mutate()}><Sparkles className="h-4 w-4" aria-hidden />Write versions</Button>
        <ErrorNote error={write.error || (write.data?.error ? new Error(write.data.error) : null)} />
        {write.data?.versions?.map((v: any, i: number) => (
          <div key={i} className="rounded-ctl border border-line p-2.5 text-sm">
            <p className="whitespace-pre-wrap">{v.text}</p>
            {v.script && <details className="mt-1 text-xs" open><summary className="cursor-pointer font-semibold text-muted">Script</summary><p className="whitespace-pre-wrap">{v.script}</p></details>}
            <p className="mt-1 text-xs text-muted">{v.best_time && `Best time: ${v.best_time}. `}{v.note}</p>
            <div className="mt-2 flex gap-2"><Button size="sm" onClick={() => { setText(v.text); setAi(true); }}>Use as A</Button>
              <Button size="sm" variant="ghost" onClick={() => { setVariantB(v.text); setAi(true); }}>Use as B</Button></div>
          </div>
        ))}
      </aside>
    </div>
  );
}

function Insights() {
  const toast = useToast();
  const qc = useQueryClient();
  const [days, setDays] = useState(90);
  const [channel, setChannel] = useState("");
  const [lang, setLang] = useState("bn");
  const a = useQuery({ queryKey: ["post-analytics", days, channel], queryFn: () => api<any>("/api/posts/analytics", { params: { days, channel } }) });
  const sync = useMutation({ mutationFn: () => api<any>("/api/posts/sync", { method: "POST" }), onSuccess: () => { toast("Numbers refreshed"); qc.invalidateQueries({ queryKey: ["post-analytics"] }); } });
  const advice = useMutation({ mutationFn: () => api<any>("/api/posts/advice", { body: { language: lang, days } }) });
  const d = a.data;
  const max = Math.max(1, ...(d?.heatmap ?? []).map((x: any) => x.avg));
  const cell = (day: number, hour: number) => d?.heatmap?.find((x: any) => x.day === day && x.hour === hour);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1.5 rounded-panel border border-line bg-surface p-1.5">
        <Select aria-label="Period" persistDraft={false} className="h-8 w-auto py-0 text-xs" value={days} onChange={(e) => setDays(+e.target.value)}>
          {[30, 90, 180, 365].map((x) => <option key={x} value={x}>Last {x} days</option>)}</Select>
        <Select aria-label="Platform" persistDraft={false} className="h-8 w-auto py-0 text-xs" value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">FB + IG</option><option value="facebook">Facebook</option><option value="instagram">Instagram</option></Select>
        <span className="flex-1" />
        <Button size="sm" loading={sync.isPending} onClick={() => sync.mutate()}><RefreshCw className="h-4 w-4" aria-hidden />Refresh numbers</Button>
      </div>
      <ErrorNote error={a.error || sync.error} />
      {a.isLoading ? <Spinner /> : !d?.posts ? <Empty title="No published posts in this period">Click “Refresh numbers” to read your Pages' posts; the analysis needs at least a few.</Empty> : <>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          {[["Posts", d.posts], ["Total engagement", d.total_engagement], ["Engagement per post", d.avg_engagement], ["Reach per post", d.avg_reach ?? "–"]].map(([k, v]) => (
            <div key={k as string} className="rounded-panel border border-line bg-surface px-3 py-2"><p className="text-xs text-muted">{k}</p><p className="text-xl font-bold tabular-nums">{typeof v === "number" ? v.toLocaleString() : v}</p></div>))}
        </div>
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <section className="rounded-panel border border-line bg-surface p-3">
            <h2 className="mb-2 flex items-center gap-2 font-bold"><TrendingUp className="h-4 w-4 text-muted" aria-hidden />When posts do best <span className="text-xs font-normal text-muted">(engagement per post, Bangladesh time)</span></h2>
            <div className="overflow-x-auto"><table className="text-[10px]"><thead><tr><th />{Array.from({ length: 24 }, (_, h) => <th key={h} className="w-5 font-normal text-muted">{h % 3 === 0 ? h : ""}</th>)}</tr></thead>
              <tbody>{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, di) => <tr key={day}><th className="pr-1 text-right font-normal text-muted">{day}</th>
                {Array.from({ length: 24 }, (_, h) => { const c = cell(di, h); return <td key={h} className="p-px"><div title={c ? `${day} ${h}:00 · ${c.posts} post(s) · ${c.avg} avg` : `${day} ${h}:00 · no posts`}
                  className="h-5 w-5 rounded-sm" style={{ background: c ? `rgb(var(--accent) / ${0.15 + 0.85 * (c.avg / max)})` : "rgb(var(--sunken))" }} /></td>; })}</tr>)}</tbody></table></div>
            <p className="mt-2 text-sm"><b>Best slots:</b> {d.best_slots.map((s: any) => `${s.label} (${s.avg}, ${s.posts} post${s.posts > 1 ? "s" : ""})`).join(" · ")}</p>
          </section>
          <section className="space-y-3 rounded-panel border border-line bg-surface p-3">
            <Ranked title="Topics" rows={d.topics} />
            <Ranked title="Formats" rows={d.formats} />
          </section>
        </div>
        {d.ab_tests?.length > 0 && <section className="rounded-panel border border-line bg-surface p-3"><h2 className="mb-2 font-bold">A/B tests</h2>
          <ul className="divide-y divide-line">{d.ab_tests.map((t: any) => <li key={t.group} className="grid gap-2 py-1.5 text-sm md:grid-cols-2">{t.variants.map((v: any) =>
            <div key={v.variant} className={cx("rounded-ctl border p-2", v.variant === t.winner ? "border-ok bg-ok-soft" : "border-line")}>
              <p className="text-xs font-semibold">Version {v.variant}{v.variant === t.winner && " · winner"} · engagement {v.engagement}{v.rate != null && ` · ${(v.rate * 100).toFixed(1)}%`}</p>
              <p className="line-clamp-2 text-xs">{v.message}</p></div>)}</li>)}</ul></section>}
        <div className="grid gap-4 lg:grid-cols-2">
          <PostCards title="Best posts" rows={d.top} />
          {d.weakest?.length > 0 && <PostCards title="Weakest posts" rows={d.weakest} />}
        </div>
      </>}
      <section className="rounded-panel border border-accent/40 bg-accent-soft/30 p-4">
        <div className="flex flex-wrap items-center gap-2"><h2 className="flex items-center gap-2 font-bold"><Lightbulb className="h-4 w-4 text-accent-strong" aria-hidden />The AI's plan for the next 2 weeks</h2><span className="flex-1" />
          <Select aria-label="Language" persistDraft={false} className="h-8 w-auto py-0 text-xs" value={lang} onChange={(e) => setLang(e.target.value)}><option value="bn">বাংলা</option><option value="en">English</option></Select>
          <Button size="sm" variant="primary" loading={advice.isPending} onClick={() => advice.mutate()}><Sparkles className="h-4 w-4" aria-hidden />{advice.data ? "Plan again" : "Make a plan"}</Button></div>
        <p className="mt-1 text-xs text-muted">What to post, when and in which format, from your numbers and the upcoming programs. Nothing is posted by the AI: you create the posts.</p>
        <ErrorNote error={advice.error || (advice.data?.error ? new Error(advice.data.error) : null)} />
        {advice.data?.summary && <div className="mt-3 space-y-3 text-sm">
          <p className="whitespace-pre-wrap">{advice.data.summary}</p>
          {advice.data.plan?.length > 0 && <div className="overflow-x-auto rounded-ctl border border-line bg-surface"><table className="w-full text-sm"><thead className="bg-sunken text-xs text-muted"><tr>
            {["When", "Where", "Format", "Topic", "Idea", "Why"].map((h) => <th key={h} className="px-2 py-1 text-left font-semibold">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-line">{advice.data.plan.map((p: any, i: number) => <tr key={i}><td className="whitespace-nowrap px-2 py-1 font-semibold">{p.when}</td><td className="px-2 py-1">{p.platform}</td><td className="px-2 py-1">{p.format}</td><td className="px-2 py-1">{p.topic}</td><td className="px-2 py-1">{p.idea}</td><td className="px-2 py-1 text-xs text-muted">{p.why}</td></tr>)}</tbody></table></div>}
          {advice.data.tests?.length > 0 && <p><b>Try next:</b> {advice.data.tests.join(" · ")}</p>}
        </div>}
      </section>
    </div>
  );
}

function Ranked({ title, rows }: { title: string; rows: any[] }) {
  const top = Math.max(1, ...rows.map((r) => r.avg));
  return (
    <div><h3 className="mb-1 text-sm font-bold">{title} <span className="font-normal text-muted">(engagement per post)</span></h3>
      <ul className="space-y-1">{rows.slice(0, 8).map((r) => <li key={r.name} className="text-xs">
        <div className="flex justify-between gap-2"><span className="truncate">{r.name} <span className="text-muted">· {r.posts}</span></span><span className="tabular-nums">{r.avg}{r.vs_average != null && <span className={cx("ml-1", r.vs_average >= 1 ? "text-ok" : "text-muted")}>×{r.vs_average}</span>}</span></div>
        <div className="mt-0.5 h-1.5 rounded-full bg-sunken"><div className="h-full rounded-full bg-accent" style={{ width: `${(r.avg / top) * 100}%` }} /></div></li>)}</ul></div>
  );
}

function PostCards({ title, rows }: { title: string; rows: any[] }) {
  return (
    <section className="rounded-panel border border-line bg-surface p-3"><h2 className="mb-2 font-bold">{title}</h2>
      <ul className="divide-y divide-line">{rows.map((p) => <li key={p.id} className="flex items-start gap-2 py-1.5 text-sm">
        <ChannelBadge channel={plat(p.channel)} />
        <span className="min-w-0 flex-1"><span className="line-clamp-2">{p.message || "(no text)"}</span><span className="text-xs text-muted">{p.topic ?? ""}{p.media_type ? ` · ${p.media_type}` : ""} · {when(p.published_at)}</span></span>
        <span className="shrink-0 text-right"><span className="block font-bold tabular-nums">{p.engagement}</span>{p.rate != null && <span className="text-xs text-muted">{(p.rate * 100).toFixed(1)}%</span>}</span>
        {p.permalink && <a href={p.permalink} target="_blank" rel="noreferrer" aria-label="Open the post" className="text-muted hover:text-ink"><ExternalLink className="h-4 w-4" aria-hidden /></a>}
      </li>)}</ul></section>
  );
}
