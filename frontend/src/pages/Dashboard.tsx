import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, ChannelBadge, Empty, Meter, PageHeader, Panel, Select, Spinner, Td, Th, TableWrap } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, num, usd } from "../lib/format";

type Daily = { date: string; messages: number; cost: number };
const PATHS: Record<string, string> = { qa_shortcut: "Q&A shortcut (no AI call)", cache: "Cache (no AI call)", llm: "AI, standard model", llm_large: "AI, large model", no_context: "Nothing found, handover offered", llm_unavailable: "AI unavailable", unknown: "Other" };

function Bars({ data, field, label, fmt }: { data: Daily[]; field: "messages" | "cost"; label: string; fmt: (n: number) => string }) {
  const max = Math.max(...data.map((d) => d[field]), field === "messages" ? 1 : 0.0001);
  const W = 560, H = 120, bw = W / data.length;
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H + 18}`} role="img" aria-label={`${label}, last ${data.length} days. Peak ${fmt(max)}.`} className="w-full">
        {data.map((d, i) => {
          const h = (d[field] / max) * H;
          return <rect key={d.date} x={i * bw + 2} y={H - h} width={Math.max(2, bw - 4)} height={Math.max(h, d[field] > 0 ? 2 : 0)} rx="2" className="fill-accent"><title>{`${d.date}: ${fmt(d[field])}`}</title></rect>;
        })}
        <line x1="0" x2={W} y1={H} y2={H} className="stroke-line" />
        <text x="0" y={H + 13} className="fill-muted text-[10px]">{data[0]?.date.slice(5)}</text>
        <text x={W} y={H + 13} textAnchor="end" className="fill-muted text-[10px]">{data[data.length - 1]?.date.slice(5)}</text>
      </svg>
      <figcaption className="mt-1 text-xs text-muted">{label} (peak {fmt(max)})</figcaption>
    </figure>
  );
}

export default function Dashboard() {
  const [days, setDays] = useState(14);
  const nav = useNavigate();
  const { user } = useAuth();
  const q = useQuery({ queryKey: ["dashboard", days], queryFn: () => api("/api/stats/dashboard", { params: { days } }), refetchInterval: 30000 });
  if (q.isLoading) return <Spinner />;
  const d = q.data;
  if (!d) return <Empty title="Could not load the dashboard">Check that the backend is running.</Empty>;
  const maxPath = Math.max(1, ...d.paths.map((p: any) => p.count));
  const b = d.budget;
  return (
    <>
      <PageHeader title="Overview" subtitle="How many guests wrote, how the assistant answered them, and what needs your attention."
        actions={<Select aria-label="Period" value={days} onChange={(e) => setDays(+e.target.value)} className="w-auto"><option value={7}>Last 7 days</option><option value={14}>Last 14 days</option><option value={30}>Last 30 days</option></Select>} />
      {d.embeddings?.hash_warning && <div className="mb-5 rounded-panel border border-warn bg-warn-soft px-4 py-3 text-sm text-warn"><strong>Embedding warning:</strong> hash embeddings are for testing and give weak Bangla/Banglish semantic search. Configure OpenAI, Google, or a private local endpoint in AI Settings → Retrieval.</div>}
      {user?.role === "admin" && <SystemHealth days={days} />}
      <dl className="mb-5 grid grid-cols-2 gap-x-6 gap-y-3 border-y border-line py-4 sm:grid-cols-4">
        {[["Messages received", num(d.totals.messages_in)], ["AI cost", usd(d.totals.cost)], ["Tokens in / out", `${num(d.totals.tokens_in)} / ${num(d.totals.tokens_out)}`], ["Unanswered questions", num(d.unanswered.open)]].map(([k, v]) => (
          <div key={k}><dt className="text-sm text-muted">{k}</dt><dd className="text-2xl font-bold tabular-nums">{v}</dd></div>
        ))}
      </dl>
      {b.level !== "unlimited" && (
        <Panel className="mb-5" title="Monthly AI budget" actions={<Badge tone={b.level === "ok" ? "ok" : b.level === "alert" ? "warn" : "danger"}>{b.level === "ok" ? "On track" : b.level === "alert" ? "Alert threshold reached" : "Budget used up"}</Badge>}>
          <Meter value={b.percent / 100} tone={b.level === "ok" ? "accent" : b.level === "alert" ? "warn" : "danger"} label="Budget used" />
          <p className="mt-2 text-sm text-muted">{usd(b.spent)} of {usd(b.budget)} ({b.percent}%). {b.level === "exceeded" && "Replies now use the cheaper fallback model if one is set."}</p>
        </Panel>
      )}
      <div className="mb-5 grid gap-5 lg:grid-cols-2">
        <Panel title="Messages per day"><Bars data={d.daily} field="messages" label="Customer messages per day" fmt={num} /></Panel>
        <Panel title="AI cost per day"><Bars data={d.daily} field="cost" label="Estimated AI cost per day" fmt={usd} /></Panel>
      </div>
      <div className="mb-5 grid gap-5 lg:grid-cols-2">
        <Panel title="By channel">
          {d.channels.length === 0 ? <Empty title="No messages yet">Connect a channel to start receiving customer messages.</Empty> : (
            <TableWrap compact><thead><tr><Th>Channel</Th><Th className="text-right">Customers</Th><Th className="text-right">AI replies</Th><Th className="text-right">Staff replies</Th></tr></thead>
              <tbody>{d.channels.map((c: any) => <tr key={c.channel}><Td><ChannelBadge channel={c.channel} /></Td><Td className="text-right tabular-nums">{num(c.user)}</Td><Td className="text-right tabular-nums">{num(c.assistant)}</Td><Td className="text-right tabular-nums">{num(c.agent)}</Td></tr>)}</tbody></TableWrap>
          )}
        </Panel>
        <Panel title="How questions were answered">
          {d.paths.length === 0 ? <p className="text-sm text-muted">No AI replies in this period.</p> : (
            <ul className="space-y-3">{d.paths.sort((a: any, b: any) => b.count - a.count).map((p: any) => (
              <li key={p.path}><div className="mb-1 flex justify-between text-sm"><span>{PATHS[p.path] ?? p.path}</span><span className="tabular-nums text-muted">{num(p.count)}</span></div><Meter value={p.count / maxPath} tone={p.path === "no_context" ? "warn" : "accent"} label={PATHS[p.path] ?? p.path} /></li>
            ))}</ul>
          )}
        </Panel>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Unanswered questions" actions={<Link to="/train?tab=failed" className="text-sm font-semibold text-accent-strong underline">Find why and fix</Link>}>
          <dl className="mb-3 grid grid-cols-3 gap-2 text-center">
            {[["Asked more than once", d.unanswered.repeated], ["Answer-is-wrong feedback", d.unanswered.feedback], ["Drafts awaiting approval", d.unanswered.pending_approval]].map(([k, v]) => (
              <div key={k as string} className="rounded-ctl bg-sunken p-2"><dt className="text-xs text-muted">{k}</dt><dd className="text-lg font-semibold">{num((v as number) ?? 0)}</dd></div>))}
          </dl>
          {!!d.unanswered.topics?.length && <p className="mb-2 text-xs text-muted"><span className="font-semibold">Repeated topics:</span> {d.unanswered.topics.map((t: any) => `${t.category} (${t.asked}×)`).join(" · ")}</p>}
          {d.unanswered.recent.length === 0 ? <p className="text-sm text-muted">Nothing waiting. Questions the assistant could not answer appear here.</p> : (
            <ul className="divide-y divide-line">{d.unanswered.recent.map((u: any) => (
              <li key={u.id} className="flex items-start justify-between gap-3 py-2"><div className="min-w-0"><p className="break-words">{u.question}</p><p className="text-xs text-muted">{u.channel ?? "test"}, {ago(u.created_at)}{u.occurrences > 1 && `, asked ${u.occurrences}×`}{u.reason === "output_blocked" && " (answer blocked by the privacy filter)"}{u.reason === "wrong_answer" && " (answer marked wrong)"}</p></div>
                <Button size="sm" onClick={() => nav("/qa", { state: { question: u.question, unansweredId: u.id } })}>Add answer</Button></li>
            ))}</ul>
          )}
        </Panel>
        <Panel title="Handover queue" actions={<Link to="/inbox" className="text-sm font-semibold text-accent-strong underline">Open inbox</Link>}>
          {d.handover.length === 0 ? <p className="text-sm text-muted">No conversations are waiting for a person.</p> : (
            <ul className="divide-y divide-line">{d.handover.map((h: any) => (
              <li key={h.id} className="flex items-center justify-between gap-3 py-2"><div><ChannelBadge channel={h.channel} /> <span className="ml-1 font-semibold">{h.name ?? `Conversation ${h.id}`}</span><p className="text-xs text-muted">{h.reason}, {ago(h.last_message_at)}</p></div><Link to={`/inbox?conv=${h.id}`} className="text-sm font-semibold text-accent-strong underline">Reply</Link></li>
            ))}</ul>
          )}
        </Panel>
      </div>
    </>
  );
}

const STATE_TONE: Record<string, "ok" | "warn" | "danger"> = { ok: "ok", amber: "warn", red: "danger" };
const pct = (v: number | null | undefined) => (v == null ? "-" : `${Math.round(v * 100)}%`);
const secs = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)} h` : s >= 60 ? `${Math.floor(s / 60)} min` : `${s} s`);

function SystemHealth({ days }: { days: number }) {
  const q = useQuery({ queryKey: ["health-deep", days], queryFn: () => api<any>("/api/health/deep", { params: { days } }), refetchInterval: 30000 });
  if (q.isLoading || !q.data) return null;
  const { system: s, ai } = q.data;
  const cells: [string, string, string, string][] = [
    ["Incoming queue", `${s.events.queued} waiting · ${s.events.processing} working · ${s.events.failed} failed`, s.events.oldest_pending_s ? `oldest ${secs(s.events.oldest_pending_s)}` : "nothing waiting", s.events.state],
    ["Replies not delivered (24 h)", `${s.delivery_24h.failed} failed · ${s.delivery_24h.blocked} blocked`, "blocked = outside the messaging window", s.delivery_24h.state],
    ["AI calls (24 h)", `${num(s.llm_24h.calls)} calls · ${pct(s.llm_24h.error_rate)} errors`, s.llm_24h.p95_latency_ms != null ? `p95 ${(s.llm_24h.p95_latency_ms / 1000).toFixed(1)} s` : "no calls yet", s.llm_24h.state],
    ["Budget", s.budget.level === "unlimited" ? "no limit set" : `${s.budget.percent}% used`, s.budget.level, s.budget.state],
    ["Embeddings", s.embeddings.provider, s.embeddings.hash_warning ? "test-only embedder on a live channel" : s.embeddings.mismatch_total ? `${s.embeddings.mismatch_total} items need re-embedding` : "consistent", s.embeddings.state],
    ["Card safety (24 h)", `${s.security_24h.card_shared} card numbers shared · ${s.security_24h.card_request_blocked} AI requests blocked`, s.security_24h.state === "red" ? "see the red badges in the Inbox" : "no card details asked or shared", s.security_24h.state],
  ];
  const worst = cells.some((c) => c[3] === "red") ? "red" : cells.some((c) => c[3] === "amber") ? "amber" : "ok";
  return (
    <Panel className="mb-5" title="System health" actions={<Badge tone={STATE_TONE[worst]}>{worst === "ok" ? "All good" : worst === "amber" ? "Needs attention" : "Problem"}</Badge>}>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{cells.map(([k, v, sub, st]) => (
        <li key={k} className={`rounded-ctl border p-3 ${st === "red" ? "border-danger bg-danger-soft" : st === "amber" ? "border-warn bg-warn-soft" : "border-line"}`}>
          <p className="text-xs font-semibold text-muted">{k}</p><p className="mt-1 font-semibold">{v}</p><p className="text-xs text-muted">{sub}</p></li>))}</ul>
      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-line pt-4 sm:grid-cols-5">
        {[["Resolved by AI", pct(ai.resolution_rate)], ["Handed to a person", pct(ai.handover_rate)], ["“Don't know” replies", pct(ai.no_context_rate)],
          ["AI cost per conversation", ai.avg_cost_per_conversation == null ? "-" : usd(ai.avg_cost_per_conversation)],
          ["Median reply time", ai.median_reply_latency_s == null ? "-" : `${ai.median_reply_latency_s.toFixed(1)} s`]].map(([k, v]) => (
          <div key={k}><dt className="text-sm text-muted">{k}</dt><dd className="text-xl font-bold tabular-nums">{v}</dd></div>))}
      </dl>
      <p className="mt-2 text-xs text-muted">{num(ai.conversations)} conversations in the period. Resolved = got an AI reply, no staff reply and no handover.</p>
    </Panel>
  );
}
