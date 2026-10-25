import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, Eye, EyeOff, RefreshCw, ScanSearch, Search, Send, Settings2, ShieldAlert, ThumbsUp, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, ChannelBadge, Empty, ErrorNote, IconButton, Input, Modal, Select, Spinner, Switch, Textarea, cx, useConfirm, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago } from "../lib/format";

type Tone = "ok" | "warn" | "danger" | "neutral" | "blue";
const GROUPS: { key: string; label: string; hint: string }[] = [
  { key: "todo", label: "To do", hint: "Drafts waiting for you, comments that need a person, and failed sends" },
  { key: "done", label: "Answered", hint: "Replied to or liked" },
  { key: "removed", label: "Hidden / deleted", hint: "Spam and attacks taken off the page" },
  { key: "other", label: "Left alone", hint: "Nothing to do (emoji in others' threads, switched off ...)" },
  { key: "all", label: "All", hint: "Every comment" },
];
const KIND: Record<string, { label: string; tone: Tone }> = {
  question: { label: "Question", tone: "blue" }, praise: { label: "Praise", tone: "ok" }, greeting: { label: "Greeting", tone: "ok" },
  reaction: { label: "Emoji / tag", tone: "neutral" }, complaint: { label: "Complaint", tone: "warn" }, other: { label: "Other", tone: "neutral" },
  against: { label: "Against us", tone: "danger" }, spam: { label: "Spam", tone: "danger" }, abuse: { label: "Abuse", tone: "danger" },
  thread: { label: "Others' thread", tone: "neutral" },
};
const STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "warn" }, needs_staff: { label: "Needs you", tone: "danger" }, failed: { label: "Failed", tone: "danger" },
  replied: { label: "Replied", tone: "ok" }, liked: { label: "Liked", tone: "blue" }, hidden: { label: "Hidden", tone: "neutral" },
  deleted: { label: "Deleted", tone: "neutral" }, ignored: { label: "Left alone", tone: "neutral" }, queued: { label: "Waiting", tone: "neutral" },
  processing: { label: "Working", tone: "neutral" },
};
const MODES = [{ value: "off", label: "Off" }, { value: "review", label: "Review first" }, { value: "auto", label: "Automatic" }];
const platform = (ch: string) => (ch === "instagram" ? "instagram" : "messenger");

/** Comments on every connected Facebook Page and Instagram account, in one compact list. The assistant answers real
 *  questions, thanks praise, and hides or reports spam and attacks; what needs a person is under "To do". */
export default function Comments() {
  const admin = useAuth().user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const [group, setGroup] = useState("todo");
  const [channel, setChannel] = useState("");
  const [page, setPage] = useState("");
  const [kind, setKind] = useState("");
  const [find, setFind] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showCheck, setShowCheck] = useState(false);
  useEffect(() => { const t = setTimeout(() => setQ(find.trim()), 300); return () => clearTimeout(t); }, [find]);
  const list = useQuery({
    queryKey: ["comments", group, channel, page, kind, q],
    queryFn: () => api<any>("/api/comments", { params: { group: group === "all" ? "" : group, channel, page_id: page, kind, q, limit: 200 } }),
    refetchInterval: 20000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["comments"] });
  const save = useMutation({ mutationFn: (body: Record<string, unknown>) => api("/api/ai/settings", { method: "PUT", body }), onSuccess: () => { toast("Saved"); refresh(); } });
  const scan = useMutation({
    mutationFn: () => api<any>("/api/comments/scan", { body: { posts: 10 } }),
    onSuccess: (r) => { toast(`${r.comments} comment(s) on the last ${r.posts} posts of ${r.accounts} account(s) · ${r.new} new${r.errors?.length ? ` · ${r.errors.length} account(s) failed` : ""}`); refresh(); },
  });
  const d = list.data;
  const st = d?.settings ?? {};
  const items: any[] = d?.items ?? [];
  const accounts: any[] = (d?.accounts ?? []).filter((a: any) => !channel || a.channel === channel);

  return (
    <>
      <h1 className="sr-only">Post comments</h1>
      {/* Keep the comment groups above the right-aligned filters. */}
      <div className="mb-3 space-y-1.5 rounded-panel border border-line bg-surface p-1.5">
        <div className="min-w-0 overflow-x-auto">
          <div className="flex w-max min-w-full">
            <div className="flex shrink-0 rounded-ctl bg-sunken p-0.5" role="tablist" aria-label="Which comments">
              {GROUPS.map((g) => {
                const n = g.key === "all" ? Object.values(d?.groups ?? {}).reduce((a: number, b: any) => a + b, 0) : d?.groups?.[g.key];
                return <button key={g.key} type="button" role="tab" aria-selected={group === g.key} title={g.hint} onClick={() => { setGroup(g.key); setOpen(null); }}
                  className={cx("whitespace-nowrap rounded-[5px] px-2 py-1 text-xs font-semibold", group === g.key ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink")}>
                  {g.label}{n != null && <span className={cx("ml-1 tabular-nums", g.key === "todo" && n ? "rounded-full bg-warn px-1.5 text-white" : "opacity-70")}>{n}</span>}</button>;
              })}
            </div>
          </div>
        </div>
        <div className="min-w-0 overflow-x-auto">
          <div className="flex w-max min-w-full items-center justify-start gap-1.5">
            <div className="relative w-[140px] shrink-0">
              <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
              <Input aria-label="Search comments" persistDraft={false} type="search" className="h-8 py-0 pl-7 text-xs" placeholder="Search" value={find} onChange={(e) => setFind(e.target.value)} />
            </div>
            <Select aria-label="Platform" persistDraft={false} className="!w-[84px] h-8 shrink-0 px-2 py-0 pr-7 text-xs" value={channel} onChange={(e) => { setChannel(e.target.value); setPage(""); }}>
              <option value="">FB + IG</option><option value="facebook">Facebook</option><option value="instagram">Instagram</option></Select>
            {accounts.length > 1 && <Select aria-label="Page or account" persistDraft={false} className="!w-[140px] h-8 shrink-0 px-2 py-0 pr-7 text-xs" value={page} onChange={(e) => setPage(e.target.value)}>
              <option value="">All Pages</option>{accounts.map((a) => <option key={`${a.channel}:${a.page_id}`} value={a.page_id}>{a.label || a.page_id} ({a.comments})</option>)}</Select>}
            <Select aria-label="Kind of comment" persistDraft={false} className="!w-[120px] h-8 shrink-0 px-2 py-0 pr-7 text-xs" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">Any kind</option>{Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</Select>
            <span className="min-w-2 flex-1" />
            <Select aria-label="How the assistant handles comments" persistDraft={false} disabled={!admin || save.isPending} title="Off: not read · Review first: the AI drafts, you send · Automatic: the AI replies and likes"
              className={cx("!w-[130px] h-8 shrink-0 px-2 py-0 pr-7 text-xs font-semibold", (st.comment_mode ?? "off") !== "off" && "border-accent text-accent-strong")}
              value={st.comment_mode ?? "off"} onChange={(e) => save.mutate({ comment_mode: e.target.value })}>
              {MODES.map((m) => <option key={m.value} value={m.value}>AI: {m.label}</option>)}</Select>
            <IconButton label="Check a comment (real, spam or against us?)" className="h-8 w-8 shrink-0" onClick={() => setShowCheck(true)}><ShieldAlert className="h-4 w-4" aria-hidden /></IconButton>
            <IconButton label="Scan the last 10 posts of every account" className="h-8 w-8 shrink-0" onClick={() => scan.mutate()} disabled={scan.isPending}><ScanSearch className={cx("h-4 w-4", scan.isPending && "animate-pulse")} aria-hidden /></IconButton>
            <IconButton label="Comment settings" className="h-8 w-8 shrink-0" onClick={() => setShowSettings(true)}><Settings2 className="h-4 w-4" aria-hidden /></IconButton>
          </div>
        </div>
      </div>
      {(st.comment_mode ?? "off") === "off" && <p className="mb-2 text-xs text-muted">The AI is not reading new comments (“AI: Off”). Choose “Review first” or “Automatic” in the bar; scanning works in any mode.</p>}
      <ErrorNote error={list.error || save.error || scan.error} />

      {list.isLoading ? <Spinner /> : !items.length ? (
        <Empty title={group === "todo" ? "Nothing to do" : "No comments here"}>{group === "todo" ? "Drafts and comments that need a person show up here." : "Try another group or filter."}</Empty>
      ) : (
        <ul className="divide-y divide-line overflow-hidden rounded-panel border border-line bg-surface">
          {items.map((c) => <CommentRow key={c.id} c={c} open={open === c.id} onToggle={() => setOpen(open === c.id ? null : c.id)} onChanged={refresh} />)}
        </ul>
      )}
      {showSettings && <SettingsModal st={st} admin={admin} onSave={(b) => save.mutate(b)} saving={save.isPending} onClose={() => setShowSettings(false)} />}
      {showCheck && <CheckModal onClose={() => setShowCheck(false)} />}
    </>
  );
}

function CommentRow({ c, open, onToggle, onChanged }: { c: any; open: boolean; onToggle: () => void; onChanged: () => void }) {
  const toast = useToast();
  const ask = useConfirm();
  const [text, setText] = useState(c.reply_text ?? "");
  useEffect(() => setText(c.reply_text ?? ""), [c.reply_text]);
  const send = useMutation({ mutationFn: () => api<any>(`/api/comments/${c.id}/send`, { body: { text } }), onSuccess: (r) => { toast(r.status === "failed" ? `Not posted: ${r.error}` : "Reply posted"); onChanged(); } });
  const act = useMutation({ mutationFn: (a: string) => api(`/api/comments/${c.id}/${a}`, { method: "POST" }), onSuccess: (_r, a) => { toast({ hide: "Hidden", unhide: "Visible again", delete: "Deleted", dismiss: "Left alone", redraft: "New draft ready" }[a] ?? "Done"); onChanged(); } });
  const kind = KIND[c.kind] ?? null;
  const status = STATUS[c.status] ?? { label: c.status, tone: "neutral" as Tone };
  const canReply = !c.replied_at && !["deleted"].includes(c.status) && !["spam", "abuse", "against"].includes(c.kind);
  return (
    <li>
      <button type="button" onClick={onToggle} aria-expanded={open} className={cx("flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-sunken/60", open && "bg-sunken/60")}>
        {open ? <ChevronDown className="h-4 w-4 shrink-0 text-muted" aria-hidden /> : <ChevronRight className="h-4 w-4 shrink-0 text-muted" aria-hidden />}
        <span className="w-28 shrink-0"><ChannelBadge channel={platform(c.channel)} /></span>
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2"><span className="truncate text-sm font-semibold">{c.from_name || "Someone"}</span>
            <span className="truncate text-xs text-muted">{c.account_label ? `on ${c.account_label} · ` : ""}{ago(c.received_at)}{c.in_thread ? " · reply in a thread" : ""}</span></span>
          <span className="block truncate text-sm">{c.message || <span className="text-muted">(no text)</span>}</span>
        </span>
        <span className="hidden shrink-0 items-center gap-1 sm:flex">
          {kind && <Badge tone={kind.tone}>{kind.label}</Badge>}
          {c.liked && <ThumbsUp className="h-3.5 w-3.5 text-blue" aria-label="liked" />}
          <Badge tone={status.tone}>{status.label}</Badge>
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-line bg-sunken/30 px-4 py-3 pl-10 text-sm">
          <div className="flex flex-wrap gap-2 sm:hidden">{kind && <Badge tone={kind.tone}>{kind.label}</Badge>}<Badge tone={status.tone}>{status.label}</Badge></div>
          <p className="whitespace-pre-wrap break-words">{c.message}</p>
          {c.post_text && <p className="line-clamp-2 border-l-2 border-line pl-2 text-xs text-muted">Post: {c.post_text}</p>}
          {c.reason && <p className="text-xs text-muted"><b>Why the AI thinks so:</b> {c.reason}{c.confidence != null && ` (${Math.round(c.confidence * 100)}% sure)`}{c.action && c.action !== "notify" && !["hidden", "deleted"].includes(c.status) ? ` · it suggests: ${c.action}` : ""}</p>}
          {c.error && <p className="text-xs text-danger">{c.error}</p>}
          {canReply ? (
            <div className="space-y-2">
              <Textarea persistDraft={false} rows={2} aria-label={`Reply to ${c.from_name || "comment"}`} placeholder="Write a reply…" value={text} onChange={(e) => setText(e.target.value)} />
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="primary" disabled={!text.trim()} loading={send.isPending} onClick={() => send.mutate()}><Send className="h-4 w-4" aria-hidden />Post reply</Button>
                <Button size="sm" loading={act.isPending && act.variables === "redraft"} onClick={() => act.mutate("redraft")}><RefreshCw className="h-4 w-4" aria-hidden />AI again</Button>
                {c.status !== "ignored" && <Button size="sm" variant="ghost" onClick={() => act.mutate("dismiss")}><X className="h-4 w-4" aria-hidden />Leave alone</Button>}
              </div>
            </div>
          ) : c.reply_text && <p className="rounded-ctl bg-surface p-2"><b>Page replied:</b> {c.reply_text}{c.replied_at && <span className="ml-1 text-xs text-muted">({ago(c.replied_at)}{c.reply_path === "staff" ? ", by staff" : ""})</span>}</p>}
          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2">
            {c.comment_url && <a className="inline-flex items-center gap-1 text-xs font-semibold text-accent-strong underline" href={c.comment_url} target="_blank" rel="noreferrer">Open on {c.channel === "instagram" ? "Instagram" : "Facebook"}<ExternalLink className="h-3 w-3" aria-hidden /></a>}
            <span className="flex-1" />
            {c.status !== "deleted" && (c.status === "hidden"
              ? <Button size="sm" onClick={() => act.mutate("unhide")}><Eye className="h-4 w-4" aria-hidden />Show again</Button>
              : <Button size="sm" onClick={() => act.mutate("hide")}><EyeOff className="h-4 w-4" aria-hidden />Hide</Button>)}
            {c.status !== "deleted" && <Button size="sm" variant="ghost" onClick={async () => (await ask({ title: "Delete this comment?", message: `${c.message} (This cannot be undone. Hiding can.)`, confirmLabel: "Delete", danger: true })) && act.mutate("delete")}><Trash2 className="h-4 w-4" aria-hidden />Delete</Button>}
          </div>
          <ErrorNote error={send.error || act.error} />
        </div>
      )}
    </li>
  );
}

function SettingsModal({ st, admin, saving, onSave, onClose }: { st: any; admin: boolean; saving: boolean; onSave: (b: Record<string, unknown>) => void; onClose: () => void }) {
  const Row = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
    <div className="flex flex-wrap items-center justify-between gap-2 py-2"><div><p className="text-sm font-semibold">{label}</p>{hint && <p className="text-xs text-muted">{hint}</p>}</div>{children}</div>);
  return (
    <Modal title="How the assistant handles comments" onClose={onClose}>
      <div className="divide-y divide-line">
        <Row label="Like comments" hint="Questions, thanks and emoji (Facebook only: Instagram cannot be liked by apps)"><Switch label="Like comments" checked={!!st.comment_auto_like} disabled={!admin || saving} onChange={(v) => onSave({ comment_auto_like: v })} /></Row>
        <Row label="Thank praise and greetings" hint="A short thank-you under “Alhamdulillah”, “MashaAllah” …"><Switch label="Thank praise" checked={!!st.comment_reply_to_praise} disabled={!admin || saving} onChange={(v) => onSave({ comment_reply_to_praise: v })} /></Row>
        <Row label="Spam and abuse">
          <Select persistDraft={false} aria-label="Spam and abuse" className="w-auto" value={st.comment_spam_action ?? "hide"} disabled={!admin || saving} onChange={(e) => onSave({ comment_spam_action: e.target.value })}>
            <option value="ignore">Leave it</option><option value="hide">Hide it</option><option value="delete">Delete it</option></Select></Row>
        <Row label="Against Quantum Method / Foundation" hint="Staff are always told">
          <Select persistDraft={false} aria-label="Against the organisation" className="w-auto" value={st.comment_against_action ?? "notify"} disabled={!admin || saving} onChange={(e) => onSave({ comment_against_action: e.target.value })}>
            <option value="notify">Only tell staff</option><option value="hide">Hide + tell staff</option><option value="delete">Delete + tell staff</option></Select></Row>
        <Row label="Also message the commenter" hint="One Messenger message after answering a question (Facebook)"><Switch label="Private reply" checked={!!st.comment_private_reply} disabled={!admin || saving} onChange={(v) => onSave({ comment_private_reply: v })} /></Row>
      </div>
      <p className="mt-3 text-xs text-muted">The AI hides or deletes on its own only when it is at least {Math.round((st.comment_remove_min_confidence ?? 0.8) * 100)}% sure. Replies wait {st.comment_reply_delay_seconds ?? 0} s; above {st.comment_max_replies_per_hour ?? 0} an hour they wait as drafts.{!admin && " Only an admin can change these."}</p>
      <div className="mt-4 flex justify-end"><Button onClick={onClose}>Done</Button></div>
    </Modal>
  );
}

function CheckModal({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const check = useMutation({ mutationFn: () => api<any>("/api/comments/check", { body: { text } }) });
  const r = check.data;
  return (
    <Modal title="Check a comment" onClose={onClose}>
      <p className="mb-2 text-sm text-muted">Paste any comment: is it a real one, spam, or against the organisation? Nothing is saved or posted.</p>
      <Textarea persistDraft={false} rows={3} aria-label="Comment to check" value={text} onChange={(e) => setText(e.target.value)} />
      <ErrorNote error={check.error} />
      {r && <div className="mt-3 rounded-ctl border border-line p-3 text-sm"><p className="flex flex-wrap items-center gap-2"><Badge tone={KIND[r.kind]?.tone ?? "neutral"}>{KIND[r.kind]?.label ?? r.kind}</Badge><span className="text-muted">{Math.round((r.confidence ?? 0) * 100)}% sure</span></p><p className="mt-1">{r.reason}</p></div>}
      <div className="mt-4 flex justify-end gap-2"><Button onClick={onClose}>Close</Button><Button variant="primary" disabled={!text.trim()} loading={check.isPending} onClick={() => check.mutate()}>Check</Button></div>
    </Modal>
  );
}
