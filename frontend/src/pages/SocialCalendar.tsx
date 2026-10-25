import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge, Button, ChannelBadge, ErrorNote, Input, Modal, Spinner, cx, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { when } from "../lib/format";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MOVABLE = ["draft", "pending_approval", "scheduled", "failed"];
const TONE: Record<string, string> = {
  published: "border-ok/40 bg-ok-soft", scheduled: "border-blue/40 bg-blue-soft", pending_approval: "border-warn/50 bg-warn-soft",
  draft: "border-dashed border-line bg-sunken", failed: "border-danger/50 bg-danger-soft", publishing: "border-warn/40 bg-warn-soft",
};
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const moment = (p: any) => p.published_at || p.scheduled_at || p.suggested_at;

/** A month of posts: published, scheduled, waiting for approval and AI drafts at their suggested time, with the
 *  programs' dates. Drag a post that is not published yet to another day to reschedule it (same time of day). */
export default function SocialCalendar() {
  const admin = useAuth().user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const [open, setOpen] = useState<any>(null);
  const start = new Date(month); start.setDate(1 - ((month.getDay() + 6) % 7));            // the Monday before the 1st
  const end = new Date(start); end.setDate(start.getDate() + 42);
  const posts = useQuery({ queryKey: ["posts-cal", ymd(start)], queryFn: () => api<any>("/api/posts", { params: { start: start.toISOString(), end: end.toISOString(), limit: 300 } }) });
  const programs = useQuery({ queryKey: ["programs-list"], queryFn: () => api<any>("/api/programs") });
  const byDay = useMemo(() => {
    const m: Record<string, any[]> = {};
    for (const p of posts.data?.items ?? []) { const t = moment(p); if (t) (m[ymd(new Date(t))] ??= []).push(p); }
    Object.values(m).forEach((l) => l.sort((a, b) => +new Date(moment(a)) - +new Date(moment(b))));
    return m;
  }, [posts.data]);
  const progDays = useMemo(() => {
    const m: Record<string, string[]> = {};
    for (const p of programs.data?.items ?? []) {
      const a = new Date(p.start_date), b = new Date(p.end_date || p.start_date);
      for (let d = new Date(a); d <= b; d.setDate(d.getDate() + 1)) (m[ymd(d)] ??= []).push(p.name);
    }
    return m;
  }, [programs.data]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["posts-cal"] }); qc.invalidateQueries({ queryKey: ["posts"] }); };
  const move = useMutation({
    mutationFn: ({ p, day }: { p: any; day: Date }) => {
      const old = new Date(moment(p) || new Date());
      const t = new Date(day); t.setHours(old.getHours(), old.getMinutes(), 0, 0);
      return api(`/api/posts/${p.id}`, { method: "PUT", body: { schedule_at: t.toISOString() } });
    },
    onSuccess: () => { toast("Rescheduled"); refresh(); },
  });
  const days = Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
  const today = ymd(new Date());
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm font-semibold">
          <CalendarDays className="h-4 w-4 text-accent-strong" aria-hidden />
          <span className="sr-only">Choose calendar month</span>
          <Input aria-label="Choose calendar month" persistDraft={false} type="month" className="h-9 w-auto py-1" value={`${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, "0")}`}
            onChange={(e) => {
              const [year, selectedMonth] = e.target.value.split("-").map(Number);
              if (year && selectedMonth) setMonth(new Date(year, selectedMonth - 1, 1));
            }} />
        </label>
        <Button size="sm" variant="ghost" onClick={() => { const d = new Date(); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); }}>Today</Button>
        <span className="flex-1" />
        <span className="flex flex-wrap gap-1 text-[11px] text-muted">{[["published", "Published"], ["scheduled", "Scheduled"], ["pending_approval", "Waits for approval"], ["draft", "Draft (AI suggestion)"], ["failed", "Failed"]].map(([k, l]) =>
          <span key={k} className={cx("rounded border px-1.5", TONE[k])}>{l}</span>)}<span className="rounded border border-accent/40 px-1.5 text-accent-strong">Program day</span></span>
      </div>
      <ErrorNote error={posts.error || move.error} />
      {posts.isLoading ? <Spinner /> : (
        <div className="grid grid-cols-7 overflow-hidden rounded-panel border border-line bg-line/60 text-xs" style={{ gap: 1 }}>
          {DAYS.map((d) => <div key={d} className="bg-sunken px-2 py-1 font-semibold text-muted">{d}</div>)}
          {days.map((d) => {
            const k = ymd(d), items = byDay[k] ?? [], progs = progDays[k] ?? [];
            return (
              <div key={k} onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => { const id = Number(e.dataTransfer.getData("text/post")); const p = (posts.data?.items ?? []).find((x: any) => x.id === id); if (p) move.mutate({ p, day: d }); }}
                className={cx("min-h-[104px] bg-surface p-1", d.getMonth() !== month.getMonth() && "bg-sunken/60 text-muted")}>
                <div className="mb-0.5 flex items-center justify-between"><span className={cx("rounded px-1 font-semibold", k === today && "bg-accent text-on-accent")}>{d.getDate()}</span>
                  {progs.length > 0 && <span className="truncate pl-1 text-[10px] text-accent-strong" title={progs.join(", ")}><CalendarDays className="mr-0.5 inline h-3 w-3" aria-hidden />{progs[0]}{progs.length > 1 ? ` +${progs.length - 1}` : ""}</span>}</div>
                <ul className="space-y-0.5">{items.slice(0, 4).map((p) => (
                  <li key={p.id} draggable={MOVABLE.includes(p.status)} onDragStart={(e) => e.dataTransfer.setData("text/post", String(p.id))}>
                    <button type="button" onClick={() => setOpen(p)} title={p.message}
                      className={cx("block w-full truncate rounded border px-1 py-0.5 text-left", TONE[p.status] ?? "border-line", MOVABLE.includes(p.status) && "cursor-grab")}>
                      <span className="font-semibold">{new Date(moment(p)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span> {p.channel === "instagram" ? "IG" : "FB"} · {p.message || "(image)"}
                    </button></li>))}
                  {items.length > 4 && <li className="text-[10px] text-muted">+{items.length - 4} more</li>}</ul>
              </div>
            );
          })}
        </div>
      )}
      <p className="text-xs text-muted">Drag a post that is not published yet to another day to move it (same time). Drafts sit at the AI's suggested time until someone schedules them.</p>
      {open && <PostModal p={open} admin={admin} onClose={() => setOpen(null)} onChanged={() => { setOpen(null); refresh(); }} />}
    </div>
  );
}

export function PostModal({ p, admin, onClose, onChanged }: { p: any; admin: boolean; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const act = useMutation({
    mutationFn: (a: "publish" | "approve" | "reject" | "schedule") => a === "schedule"
      ? api(`/api/posts/${p.id}`, { method: "PUT", body: { schedule_at: p.suggested_at } })
      : api<any>(`/api/posts/${p.id}/${a}`, { method: "POST", body: a === "publish" ? undefined : {} }),
    onSuccess: (r: any, a) => { toast(a === "publish" ? (r.status === "published" ? "Published" : r.status === "pending_approval" ? "Waits for an admin" : `Not published: ${r.error}`) : "Done"); onChanged(); },
  });
  return (
    <Modal title="Post" onClose={onClose}>
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2"><ChannelBadge channel={p.channel === "instagram" ? "instagram" : "messenger"} /><span className="text-muted">{p.account_label}</span>
          <Badge>{p.status.replace("_", " ")}</Badge>{p.ab_variant && <Badge tone="blue">A/B: {p.ab_variant}</Badge>}{p.ai_written && <Badge tone="blue">AI</Badge>}</div>
        {p.image_url && <img src={p.image_url} alt="" className="max-h-48 rounded-ctl border border-line object-contain" />}
        <p className="whitespace-pre-wrap">{p.message || "(no text)"}</p>
        <p className="text-xs text-muted">{p.published_at ? `Published ${when(p.published_at)}` : p.scheduled_at ? `Scheduled for ${when(p.scheduled_at)}` : p.suggested_at ? `The AI suggests ${when(p.suggested_at)}` : "No time yet"}</p>
        {p.note && <p className="rounded-ctl bg-sunken p-2 text-xs"><b>Note:</b> {p.note}</p>}
        {p.error && <p className="text-xs text-danger">{p.error}</p>}
        {p.status === "published" && <p className="text-xs">Engagement <b>{p.engagement}</b>{p.metrics?.reach ? ` · reach ${p.metrics.reach}` : ""}</p>}
        {p.history?.length > 0 && <details className="text-xs"><summary className="cursor-pointer font-semibold text-muted">History ({p.history.length})</summary>
          <ul className="mt-1 space-y-0.5">{p.history.map((h: any, i: number) => <li key={i}>{when(h.at)} · {h.by}: {h.action}</li>)}</ul></details>}
        <ErrorNote error={act.error} />
        <div className="flex flex-wrap justify-end gap-2">
          {p.permalink && <a className="inline-flex items-center gap-1 rounded-ctl px-2 py-1 text-sm font-semibold text-accent-strong underline" href={p.permalink} target="_blank" rel="noreferrer">Open<ExternalLink className="h-3 w-3" aria-hidden /></a>}
          {p.status === "draft" && p.suggested_at && <Button size="sm" loading={act.isPending} onClick={() => act.mutate("schedule")}>Schedule at the suggested time</Button>}
          {p.status === "pending_approval" && admin && <><Button size="sm" onClick={() => act.mutate("reject")}>Send back</Button><Button size="sm" variant="primary" onClick={() => act.mutate("approve")}>Approve</Button></>}
          {["draft", "scheduled", "failed"].includes(p.status) && <Button size="sm" variant="primary" loading={act.isPending} onClick={() => act.mutate("publish")}>Publish now</Button>}
        </div>
      </div>
    </Modal>
  );
}
