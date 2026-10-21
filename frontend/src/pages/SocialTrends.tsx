import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, HelpCircle, RefreshCw, Smile, Target, Users } from "lucide-react";
import type { ReactNode } from "react";
import { Badge, Button, ChannelBadge, Empty, ErrorNote, Spinner, cx, useToast } from "../components/ui";
import { api } from "../lib/api";
import { ago, when } from "../lib/format";

const Section = ({ icon: Icon, title, hint, actions, children }: { icon: typeof Users; title: string; hint?: string; actions?: ReactNode; children: ReactNode }) => (
  <section className="rounded-panel border border-line bg-surface p-3">
    <div className="mb-2 flex flex-wrap items-center gap-2"><h2 className="flex items-center gap-2 font-bold"><Icon className="h-4 w-4 text-muted" aria-hidden />{title}</h2>
      {hint && <span className="text-xs text-muted">{hint}</span>}<span className="flex-1" />{actions}</div>
    {children}
  </section>
);

/** Audience, mood of the comments, what guests ask, what each program's posts brought, and the weekly reports. */
export default function SocialTrends() {
  return (
    <div className="space-y-4">
      <Audience />
      <div className="grid gap-4 xl:grid-cols-2"><Sentiment /><Questions /></div>
      <Funnel />
      <Reports />
    </div>
  );
}

function Spark({ values }: { values: (number | null)[] }) {
  const v = values.filter((x): x is number => typeof x === "number");
  if (v.length < 2) return <p className="text-xs text-muted">The trend appears after a few days of numbers.</p>;
  const min = Math.min(...v), max = Math.max(...v), w = 220, h = 40;
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * w},${h - ((x - min) / Math.max(1, max - min)) * (h - 4) - 2}`).join(" ");
  return <svg viewBox={`0 0 ${w} ${h}`} className="h-10 w-full" role="img" aria-label={`From ${min} to ${max}`}><polyline points={pts} fill="none" stroke="rgb(var(--accent))" strokeWidth="2" /></svg>;
}

function Audience() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["audience"], queryFn: () => api<any[]>("/api/social/audience") });
  const refresh = useMutation({ mutationFn: () => api("/api/social/audience/refresh", { method: "POST" }), onSuccess: () => { toast("Audience numbers read"); qc.invalidateQueries({ queryKey: ["audience"] }); } });
  return (
    <Section icon={Users} title="Audience" hint="followers and reach of each account, saved daily"
      actions={<Button size="sm" loading={refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw className="h-4 w-4" aria-hidden />Read now</Button>}>
      <ErrorNote error={q.error || refresh.error} />
      {q.isLoading ? <Spinner /> : !q.data?.length ? <Empty title="No accounts">Connect a Page or an Instagram account first.</Empty> : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{q.data.map((a) => {
          const hours = a.online_hours as Record<string, number> | null;
          const hmax = hours ? Math.max(1, ...Object.values(hours)) : 1;
          return (
            <div key={`${a.channel}:${a.account_id}`} className="rounded-ctl border border-line p-3">
              <div className="flex items-center gap-2"><ChannelBadge channel={a.channel === "instagram" ? "instagram" : "messenger"} /><span className="truncate font-semibold">{a.label}</span></div>
              <p className="mt-1 text-2xl font-bold tabular-nums">{a.followers?.toLocaleString() ?? "–"}<span className="ml-1 text-xs font-normal text-muted">followers</span>
                {a.growth != null && <span className={cx("ml-2 text-sm", a.growth >= 0 ? "text-ok" : "text-danger")}>{a.growth >= 0 ? "+" : ""}{a.growth}</span>}</p>
              <Spark values={a.trend.map((t: any) => t.followers)} />
              {hours && <div className="mt-2"><p className="text-xs font-semibold text-muted">When followers are online (hour)</p>
                <div className="flex h-10 items-end gap-px">{Array.from({ length: 24 }, (_, h) => <div key={h} title={`${h}:00 · ${hours[h] ?? 0}`} className="flex-1 rounded-t-sm bg-accent/70" style={{ height: `${((hours[h] ?? 0) / hmax) * 100}%` }} />)}</div></div>}
              {Object.entries(a.demographics ?? {}).map(([k, rows]: any) => <p key={k} className="mt-1 text-xs"><b className="capitalize">{k}:</b> {rows.slice(0, 4).map((r: any) => `${r.name} (${r.value})`).join(", ")}</p>)}
              <p className="mt-1 text-[11px] text-muted">{a.updated ? `numbers of ${a.updated}` : "no numbers yet: click Read now"}</p>
            </div>
          );
        })}</div>
      )}
      <p className="mt-2 text-xs text-muted">Online hours and age / city / gender come from Instagram (Meta gives them for accounts with enough followers); Facebook Pages give followers and reach.</p>
    </Section>
  );
}

function Sentiment() {
  const q = useQuery({ queryKey: ["sentiment"], queryFn: () => api<any>("/api/social/sentiment") });
  const d = q.data;
  const top = Math.max(1, ...(d?.weeks ?? []).map((w: any) => w.positive + w.negative + w.question + w.spam + w.other));
  return (
    <Section icon={Smile} title="Mood of the comments" hint="week by week">
      {q.isLoading ? <Spinner /> : !d?.weeks?.length ? <Empty title="No comments yet">Comments appear here once the assistant reads them.</Empty> : <>
        <p className="mb-2 text-sm">Overall: <b className={d.mood >= 0 ? "text-ok" : "text-danger"}>{d.mood >= 0.3 ? "mostly positive" : d.mood >= 0 ? "mixed, leaning positive" : d.mood > -0.3 ? "mixed, leaning negative" : "mostly negative"}</b>
          <span className="text-muted"> · {d.totals.positive ?? 0} positive, {d.totals.negative ?? 0} negative, {d.totals.question ?? 0} questions, {d.totals.complaint_chats ?? 0} complaint chats</span></p>
        <div className="flex h-28 items-end gap-1">{d.weeks.map((w: any) => {
          const seg = (n: number, c: string) => n ? <div className={c} style={{ height: `${(n / top) * 100}%` }} /> : null;
          return <div key={w.week} className="flex flex-1 flex-col justify-end" title={`week of ${w.week}: +${w.positive} −${w.negative} ?${w.question}`}>
            {seg(w.negative, "bg-danger/80")}{seg(w.question, "bg-blue/70")}{seg(w.positive, "bg-ok/80")}{seg(w.spam + w.other, "bg-sunken")}</div>;
        })}</div>
        <p className="mt-1 flex gap-3 text-[11px] text-muted"><span><span className="mr-1 inline-block h-2 w-2 bg-ok/80" />positive</span><span><span className="mr-1 inline-block h-2 w-2 bg-blue/70" />questions</span><span><span className="mr-1 inline-block h-2 w-2 bg-danger/80" />negative</span></p>
        <p className="mt-1 text-xs text-muted">A sudden jump in negative comments sends an alert (AI settings → Monitoring webhook).</p>
      </>}
    </Section>
  );
}

function Questions() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["question-trends"], queryFn: () => api<any>("/api/social/questions") });
  const refresh = useMutation({ mutationFn: () => api<any>("/api/social/questions/refresh", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["question-trends"] }) });
  const d = q.data;
  return (
    <Section icon={HelpCircle} title="What guests ask most" hint={d?.at ? `${d.messages} messages, ${ago(d.at)}` : "chats, comments and unanswered questions"}
      actions={<Button size="sm" loading={refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw className="h-4 w-4" aria-hidden />{d?.at ? "Again" : "Find out"}</Button>}>
      <ErrorNote error={refresh.error || (refresh.data?.error ? new Error(refresh.data.error) : null)} />
      {q.isLoading ? <Spinner /> : !d?.topics?.length ? <p className="text-sm text-muted">Click “Find out”: the AI groups the last 30 days of questions (also every Saturday).</p> : (
        <ul className="divide-y divide-line">{d.topics.map((t: any, i: number) => (
          <li key={i} className="py-1.5 text-sm"><div className="flex items-baseline justify-between gap-2"><b>{t.topic}</b><Badge>{t.count}</Badge></div>
            <p className="text-xs text-muted">“{(t.examples ?? []).slice(0, 2).join("”, “")}”</p>
            {t.post_idea && <p className="text-xs"><span className="font-semibold text-accent-strong">Post idea:</span> {t.post_idea}</p>}</li>))}</ul>
      )}
    </Section>
  );
}

function Funnel() {
  const q = useQuery({ queryKey: ["funnel"], queryFn: () => api<any[]>("/api/social/funnel") });
  return (
    <Section icon={Target} title="What each program's posts brought" hint="last 60 days">
      {q.isLoading ? <Spinner /> : !q.data?.length ? <Empty title="No programs">Programs appear here from the schedule.</Empty> : (
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="text-xs text-muted"><tr>
          {["Program", "Date", "Posts", "Engagement", "Asked in chat", "Form link given", "Leads"].map((h) => <th key={h} className="px-2 py-1 text-left font-semibold">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-line">{q.data.map((p) => <tr key={p.id}><td className="px-2 py-1 font-semibold">{p.name}</td><td className="px-2 py-1 text-muted">{p.start_date}</td>
            <td className="px-2 py-1 tabular-nums">{p.posts}</td><td className="px-2 py-1 tabular-nums">{p.engagement}</td><td className="px-2 py-1 tabular-nums">{p.asked}</td>
            <td className="px-2 py-1 tabular-nums">{p.link_given ?? "no link"}</td><td className="px-2 py-1 font-bold tabular-nums">{p.leads}</td></tr>)}</tbody></table></div>
      )}
    </Section>
  );
}

function Reports() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["social-reports"], queryFn: () => api<any[]>("/api/social/reports") });
  const make = useMutation({ mutationFn: () => api<any>("/api/social/reports", { method: "POST" }), onSuccess: (r) => { toast(r.sent ? "Report made and sent to the alert webhook" : "Report made"); qc.invalidateQueries({ queryKey: ["social-reports"] }); } });
  return (
    <Section icon={FileText} title="Weekly reports" hint="every Saturday 09:00, also sent to the alert webhook"
      actions={<Button size="sm" loading={make.isPending} onClick={() => make.mutate()}>Make one now</Button>}>
      <ErrorNote error={make.error} />
      {q.isLoading ? <Spinner /> : !q.data?.length ? <p className="text-sm text-muted">No report yet.</p> : (
        <ul className="space-y-2">{q.data.map((r, i) => (
          <li key={r.id}><details open={i === 0} className="rounded-ctl border border-line p-2 text-sm"><summary className="cursor-pointer font-semibold">{r.period_start} – {r.period_end}{r.sent && <Badge tone="ok" className="ml-2">sent</Badge>}<span className="ml-2 text-xs font-normal text-muted">{when(r.created_at)}</span></summary>
            <p className="mt-1 whitespace-pre-wrap">{r.summary}</p></details></li>))}</ul>
      )}
    </Section>
  );
}
