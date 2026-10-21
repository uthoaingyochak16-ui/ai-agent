import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ClipboardList, Clock, Pause, Play, Send, User, UserRound, ThumbsDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Avatar, Badge, Button, ChannelBadge, Empty, ErrorNote, Field, Input, PageHeader, Spinner, Textarea, useToast, cx } from "../components/ui";
import ReplyInspector from "../components/ReplyInspector";
import { ApiError, api } from "../lib/api";
import { ago, when } from "../lib/format";

type Folder = { key: string; label: string; count?: number; params: Record<string, string | boolean>; sub?: boolean; note?: string; warn?: boolean };
const PLATFORM: Record<string, string> = { messenger: "Facebook Messenger", instagram: "Instagram", whatsapp: "WhatsApp" };

/** One inbox for every platform and every connected Facebook Page, Instagram account and WhatsApp number, in the
 *  manner of Meta Business Suite: folders on the left, conversations in the middle, the chat on the right. */
export default function InboxPage() {
  const [sp, setSp] = useSearchParams();
  const [folder, setFolder] = useState("all");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => { const t = setTimeout(() => setQ(search.trim()), 350); return () => clearTimeout(t); }, [search]);
  const sel = sp.get("conv") ? Number(sp.get("conv")) : null;
  const sum = useQuery({ queryKey: ["inbox-summary"], queryFn: () => api<any>("/api/conversations/summary"), refetchInterval: 10000 });
  const s = sum.data;
  const folders: Folder[] = [
    { key: "all", label: "All messages", count: s?.all, params: {} },
    { key: "waiting", label: "Waiting for a reply", count: s?.waiting, params: { waiting: true } },
    { key: "handover", label: "Needs a person", count: s?.handover, params: { status: "handover" } },
    // every platform is listed, connected or not, so it is clear what is missing
    ...Object.keys(PLATFORM).flatMap((ch) => {
      const accs = (s?.accounts ?? []).filter((a: any) => a.channel === ch);
      const pl = s?.platforms?.[ch];
      const note = !pl?.accounts ? "not connected" : !pl.working ? "connection problem" : undefined;
      return [{ key: ch, label: PLATFORM[ch], count: s?.channels?.[ch], params: { channel: ch }, note, warn: !!pl?.accounts && !pl.working },
        ...accs.map((a: any) => ({ key: `${ch}:${a.account_id}`, label: a.label || `${ch === "whatsapp" ? "Number" : ch === "instagram" ? "Account" : "Page"} ${a.account_id}`, count: a.conversations,
          params: { channel: ch, account_id: a.account_id }, sub: true, warn: a.status === "error", note: a.status === "error" ? "problem" : undefined }))];
    }),
  ];
  const current = folders.find((f) => f.key === folder) ?? folders[0];
  const list = useQuery({ queryKey: ["convs", current.params, q], queryFn: () => api<any[]>("/api/conversations", { params: { ...current.params, q } }), refetchInterval: 5000 });
  return (
    <>
      <PageHeader title="Inbox" subtitle="Messages from all your Facebook Pages, Instagram accounts and WhatsApp numbers in one place. The AI answers them; you can step in any time." />
      <div className="grid gap-4 lg:grid-cols-[220px_320px_1fr]" style={{ minHeight: "60vh" }}>
        <nav aria-label="Inbox folders" className={cx("rounded-panel border border-line bg-surface p-2", !!sel && "hidden lg:block")}>
          <ul className="space-y-0.5">{folders.map((f) => (
            <li key={f.key}><button type="button" onClick={() => setFolder(f.key)} aria-current={folder === f.key}
                className={cx("flex w-full items-center justify-between gap-2 rounded-ctl px-2.5 py-1.5 text-left text-sm", f.sub && "pl-6 text-xs",
                  folder === f.key ? "bg-accent-soft font-semibold text-accent-strong" : "text-ink hover:bg-sunken")}>
              <span className="flex min-w-0 flex-col"><span className="flex min-w-0 items-center gap-1.5">{!f.sub && PLATFORM[f.key] && <ChannelBadge channel={f.key} />}<span className="truncate">{PLATFORM[f.key] ? "" : f.label}</span></span>
                {f.note && <span className={cx("text-[11px]", f.warn ? "text-danger" : "text-muted")}>{f.note}</span>}</span>
              {f.count != null && <span className={cx("shrink-0 tabular-nums text-xs", (f.key === "waiting" || f.key === "handover") && f.count ? "font-bold text-warn" : "text-muted")}>{f.count}</span>}
            </button></li>
          ))}</ul>
          <Link to="/comments" className="mt-2 flex items-center justify-between rounded-ctl border-t border-line px-2.5 pt-2 text-sm text-ink hover:text-accent-strong">
            <span>Facebook post comments</span>{!!s?.comments_waiting && <Badge tone="warn">{s.comments_waiting}</Badge>}</Link>
          {s && Object.values(s.platforms ?? {}).some((p: any) => !p.accounts || !p.working) && <p className="mt-2 px-2.5 text-xs text-muted">Connect or fix a Page, Instagram account or WhatsApp number on <Link className="font-semibold underline" to="/channels">Pages and numbers</Link>; its messages then arrive here.</p>}
        </nav>
        <section aria-label="Conversations" className={cx("rounded-panel border border-line bg-surface", !!sel && "hidden lg:block")}>
          <div className="border-b border-line p-3">
            <Input persistDraft={false} type="search" aria-label="Search by name or words" placeholder="Search name or words" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          {list.isLoading ? <Spinner /> : !list.data?.length ? <div className="p-4"><Empty title={q ? "Nothing found" : "No conversations here"}>{q ? "Try other words." : "Messages from your connected channels appear here."}</Empty></div> : (
            <ul className="max-h-[70vh] divide-y divide-line overflow-y-auto">
              {list.data.map((c) => (
                <li key={c.id}><button onClick={() => setSp({ conv: String(c.id) })} aria-current={sel === c.id} className={cx("flex w-full gap-3 px-3 py-3 text-left hover:bg-sunken", sel === c.id && "bg-accent-soft")}>
                  <span className="relative"><Avatar name={c.name} src={c.avatar_url} />{c.waiting && <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-surface bg-warn" title="Waiting for a reply" />}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2"><span className={cx("truncate", c.waiting ? "font-bold" : "font-semibold")}>{c.name ?? `Customer ${c.id}`}</span><span className="shrink-0 text-xs text-muted">{ago(c.last_message_at)}</span></div>
                    <p className={cx("truncate text-sm", c.waiting ? "text-ink" : "text-muted")}>{c.last_role && c.last_role !== "user" ? (c.last_role === "agent" ? "You: " : "AI: ") : ""}{c.last_message ?? "No messages"}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-1"><ChannelBadge channel={c.channel} />{c.account_label && <span className="truncate text-xs text-muted">{c.account_label}</span>}
                      {c.status === "handover" && <Badge tone="warn">Needs a person</Badge>}{c.ai_paused && c.status !== "handover" && <Badge>AI paused</Badge>}</div>
                  </div>
                </button></li>
              ))}
            </ul>
          )}
        </section>
        <section aria-label="Conversation" className={cx(!sel && "hidden lg:block")}>
          {sel ? <Thread id={sel} onBack={() => setSp({})} /> : <Empty title="Select a conversation">Pick one to read it, take over from the AI, or reply yourself.</Empty>}
        </section>
      </div>
    </>
  );
}

function Thread({ id, onBack }: { id: number; onBack: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["conv", id], queryFn: () => api(`/api/conversations/${id}`), refetchInterval: 4000 });
  const [text, setText] = useState("");
  const [tpl, setTpl] = useState<{ name: string; language: string; params: string } | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const lastTyping = useRef(0);
  const onType = (v: string) => {
    setText(v);
    if (v.trim() && Date.now() - lastTyping.current > 10_000) {  // Meta keeps the bubble ~20 s
      lastTyping.current = Date.now();
      api(`/api/conversations/${id}/typing`, { method: "POST" }).catch(() => {});
    }
  };
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [q.data?.messages?.length]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["conv", id] }); qc.invalidateQueries({ queryKey: ["convs"] }); };
  // a wrong AI reply is blocked in the shared answer cache so no other guest gets it
  // ...and the guest's question goes to Q&A manager -> Unknown questions, where a corrected draft waits for approval
  const wrong = useMutation({ mutationFn: ({ answer, question }: { answer: string; question?: string }) => api<{ blocked: number }>("/api/cache/feedback", { body: { answer, question } }),
    onSuccess: (r) => toast((r.blocked ? `Marked wrong: ${r.blocked} cached copy blocked.` : "Marked wrong.") + " Added to Unknown questions for a corrected answer.") });
  const questionBefore = (mid: number) => [...(q.data?.messages ?? [])].filter((x: any) => x.role === "user" && x.id < mid).pop()?.content as string | undefined;
  const toggle = useMutation({ mutationFn: (pause: boolean) => api(`/api/conversations/${id}/${pause ? "pause" : "resume"}`, { method: "POST" }), onSuccess: refresh });
  const send = useMutation({
    mutationFn: () => api(`/api/conversations/${id}/reply`, { body: { text, template: tpl ? { name: tpl.name, language: tpl.language, body_params: tpl.params.split(",").map((s) => s.trim()).filter(Boolean) } : null } }),
    onSuccess: () => { setText(""); setTpl(null); refresh(); toast("Reply sent"); },
    onError: (e) => { refresh(); if (e instanceof ApiError && (e.detail as any)?.needs_template) setTpl((t) => t ?? { name: "", language: "en", params: "" }); },
  });
  if (q.isLoading) return <Spinner />;
  const c = q.data;
  if (!c) return <ErrorNote error={q.error} />;
  const windowLeft = c.last_user_message_at ? 24 * 3600e3 - (Date.now() - new Date(c.last_user_message_at).getTime()) : -1;
  const open = windowLeft > 0;
  const err = send.error instanceof ApiError ? send.error : null;
  const outOfWindow = (err?.detail as any)?.error === "out_of_window";
  return (
    <div className="flex h-full flex-col rounded-panel border border-line bg-surface">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div className="flex min-w-0 items-center gap-2"><Button size="sm" variant="ghost" className="lg:hidden" onClick={onBack}>Back</Button>
          <Avatar name={c.name} src={c.avatar_url} size={32} /><span className="min-w-0"><span className="flex items-center gap-1.5"><span className="truncate font-semibold">{c.name ?? `Customer ${c.id}`}</span><ChannelBadge channel={c.channel} /></span>
            <span className="block truncate text-xs text-muted" title={`Guest id on ${c.channel}: ${c.guest_id ?? "-"}`}>to {c.account_label || (c.account_id ? `${c.channel === "whatsapp" ? "number" : c.channel === "instagram" ? "account" : "Page"} ${c.account_id}` : "your account")} · since {when(c.started_at)}</span></span>
          {c.profile_id && <Link className="text-sm font-semibold text-accent-strong underline" to={`/profiles/${c.profile_id}`}>View profile</Link>}
          {c.ai_paused ? <Badge tone="warn">AI paused{c.handover_reason ? `: ${c.handover_reason}` : ""}</Badge> : <Badge tone="ok">AI active</Badge>}
        </div>
        <Button size="sm" onClick={() => toggle.mutate(!c.ai_paused)} loading={toggle.isPending}>{c.ai_paused ? <><Play className="h-3.5 w-3.5" aria-hidden />Resume AI</> : <><Pause className="h-3.5 w-3.5" aria-hidden />Take over</>}</Button>
      </header>
      <div className="flex items-center gap-1.5 border-b border-line bg-sunken/60 px-4 py-1.5 text-xs text-muted"><Clock className="h-3.5 w-3.5" aria-hidden />
        {open ? `Free-form replies allowed for ${Math.floor(windowLeft / 3600e3)} h ${Math.floor((windowLeft % 3600e3) / 60e3)} min` : c.channel === "whatsapp" ? "Window closed: only an approved template can be sent" : "Window closed: replies are blocked (human-agent tag not enabled or expired)"}
        {c.last_user_message_at && <span> (customer last wrote {when(c.last_user_message_at)})</span>}</div>
      {c.comments?.length > 0 && <details className="border-b border-line px-4 py-1.5 text-xs"><summary className="cursor-pointer font-semibold text-muted">{c.comments.length} comment(s) by this person on your posts</summary>
        <ul className="mt-1 space-y-1 pb-1">{c.comments.map((x: any) => <li key={x.id} className="flex items-start gap-2"><Link className="text-accent-strong underline" to="/comments">open</Link><span className="min-w-0"><span className="block">“{x.message}”</span>{x.post_text && <span className="block truncate text-muted">on: {x.post_text}</span>}</span></li>)}</ul></details>}
      {c.ai_paused && <div className="flex flex-wrap items-center justify-between gap-2 border-b border-warn bg-warn-soft px-4 py-2 text-sm text-warn">
        <span><strong>The AI is not replying in this chat</strong> ({c.handover_reason?.startsWith("agent:") ? "a staff member replied" : c.handover_reason?.startsWith("keyword:") ? "the guest asked for a person" : "taken over"}). It answers again on its own after a quiet period without staff replies (AI Settings), or right now:</span>
        <Button size="sm" onClick={() => toggle.mutate(false)} loading={toggle.isPending}><Play className="h-3.5 w-3.5" aria-hidden />Resume AI now</Button></div>}
      <div className="max-h-[52vh] min-h-[280px] flex-1 space-y-3 overflow-y-auto p-4" role="log" aria-label="Messages">
        {c.messages.map((m: any) => {
          const mine = m.role !== "user";
          const Icon = m.role === "user" ? User : m.role === "agent" ? UserRound : m.role === "system" ? ClipboardList : Bot;
          return (
            <div key={m.id} className={cx("flex", mine ? "justify-end" : "justify-start")}>
              <div className={cx("max-w-[85%] rounded-panel px-3 py-2", m.role === "user" ? "bg-sunken" : m.role === "agent" ? "bg-blue-soft" : m.role === "system" ? "border border-dashed border-warn bg-warn-soft" : "bg-accent-soft")}>
                <p className="mb-0.5 flex items-center gap-1 text-xs font-semibold text-muted"><Icon className="h-3 w-3" aria-hidden />{m.role === "user" ? "Customer" : m.role === "agent" ? "Staff" : m.role === "system" ? "Note for staff (not sent)" : "AI"}, {when(m.created_at)}</p>
                <p className="whitespace-pre-wrap break-words">{m.content}</p>
                {m.attachments?.length > 0 && <p className="mt-1 text-xs text-muted">{m.attachments.length} attachment(s): {m.attachments.map((a: any) => a.type).join(", ")}</p>}
                <div className="mt-1 flex flex-wrap gap-1">
                  {m.delivery_status === "blocked" && <Badge tone="danger">Not sent: {m.flag === "out_of_window" ? "outside messaging window" : m.flag}</Badge>}
                  {m.delivery_status === "failed" && <Badge tone="danger">{m.flag === "replies_off" ? "Not sent: “Replies enabled” is off in Channels" : m.flag === "ChannelNotConfigured" ? "Not sent: channel not set up" : "Delivery failed"}</Badge>}
                  {m.flag === "card_shared" && <Badge tone="danger">Alert: card number shared (hidden, guest warned)</Badge>}
                  {m.flag === "card_request" && <Badge tone="danger">Alert: AI asked for card details (reply replaced)</Badge>}
                  {m.sources?.inspector ? <Badge>{m.sources.inspector.path_group}</Badge> : m.sources?.path && <Badge>{m.sources.path}</Badge>}
                  {m.role === "assistant" && <button type="button" className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold text-muted hover:bg-danger-soft hover:text-danger" onClick={() => wrong.mutate({ answer: m.content, question: questionBefore(m.id) })}><ThumbsDown className="h-3 w-3" aria-hidden />Wrong</button>}
                </div>
                {m.sources?.inspector && <details className="mt-2 rounded-ctl border border-line bg-surface px-2 py-1"><summary className="cursor-pointer text-xs font-semibold text-muted">Inspect this reply</summary><ReplyInspector i={m.sources.inspector} /></details>}
              </div>
            </div>
          );
        })}
        <div ref={end} />
      </div>
      <form className="space-y-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
        {(tpl || (outOfWindow && c.channel === "whatsapp")) && tpl && (
          <div className="grid gap-2 rounded-ctl border border-warn bg-warn-soft p-3 sm:grid-cols-3">
            <p className="text-sm text-warn sm:col-span-3">The 24-hour window is closed. Send an approved WhatsApp template instead.</p>
            <Field label="Template name">{(i) => <Input id={i} required value={tpl.name} onChange={(e) => setTpl({ ...tpl, name: e.target.value })} />}</Field>
            <Field label="Language code">{(i) => <Input id={i} required value={tpl.language} onChange={(e) => setTpl({ ...tpl, language: e.target.value })} />}</Field>
            <Field label="Body variables" hint="Comma separated">{(i) => <Input id={i} value={tpl.params} onChange={(e) => setTpl({ ...tpl, params: e.target.value })} />}</Field>
          </div>
        )}
        {err && !tpl && <ErrorNote error={outOfWindow ? new Error("Outside the messaging window. This message was not sent.") : err} />}
        {err && tpl && !outOfWindow && <ErrorNote error={err} />}
        <div className="flex gap-2">
          <Textarea aria-label="Reply" className="min-h-[44px] flex-1" rows={2} placeholder={c.ai_paused ? "Write a reply" : "Sending a reply pauses the AI in this chat for a while"} value={text} onChange={(e) => onType(e.target.value)} />
          <Button type="submit" variant="primary" loading={send.isPending} disabled={!text.trim() && !tpl} className="self-end"><Send className="h-4 w-4" aria-hidden />Send</Button>
        </div>
      </form>
    </div>
  );
}
