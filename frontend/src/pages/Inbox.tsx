import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Clock, Pause, Play, Send, User, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Avatar, Badge, Button, ChannelBadge, Empty, ErrorNote, Field, Input, PageHeader, Select, Spinner, Textarea, useToast, cx } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { ago, when } from "../lib/format";

export default function InboxPage() {
  const [sp, setSp] = useSearchParams();
  const [channel, setChannel] = useState("");
  const [status, setStatus] = useState("");
  const sel = sp.get("conv") ? Number(sp.get("conv")) : null;
  const list = useQuery({ queryKey: ["convs", channel, status], queryFn: () => api<any[]>("/api/conversations", { params: { channel, status } }), refetchInterval: 5000 });
  return (
    <>
      <PageHeader title="Inbox" subtitle="Every conversation across channels. Updates every few seconds." />
      <div className="grid gap-4 lg:grid-cols-[340px_1fr]" style={{ minHeight: "60vh" }}>
        <section aria-label="Conversations" className={cx("rounded-panel border border-line bg-surface", !!sel && "hidden lg:block")}>
          <div className="flex gap-2 border-b border-line p-3">
            <Select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">All channels</option><option value="messenger">Messenger</option><option value="instagram">Instagram</option><option value="whatsapp">WhatsApp</option></Select>
            <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option><option value="open">AI active</option><option value="handover">Needs a person</option></Select>
          </div>
          {list.isLoading ? <Spinner /> : !list.data?.length ? <div className="p-4"><Empty title="No conversations">Messages from your connected channels appear here.</Empty></div> : (
            <ul className="max-h-[70vh] divide-y divide-line overflow-y-auto">
              {list.data.map((c) => (
                <li key={c.id}><button onClick={() => setSp({ conv: String(c.id) })} aria-current={sel === c.id} className={cx("flex w-full gap-3 px-3 py-3 text-left hover:bg-sunken", sel === c.id && "bg-accent-soft")}>
                  <Avatar name={c.name} src={c.avatar_url} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2"><span className="flex min-w-0 items-center gap-1.5"><span className="truncate font-semibold">{c.name ?? `Customer ${c.id}`}</span><ChannelBadge channel={c.channel} /></span><span className="shrink-0 text-xs text-muted">{ago(c.last_message_at)}</span></div>
                    <p className="truncate text-sm text-muted">{c.last_message ?? "No messages"}</p>
                    <div className="mt-1 flex gap-1">{c.status === "handover" && <Badge tone="warn">Needs a person</Badge>}{c.ai_paused && c.status !== "handover" && <Badge>AI paused</Badge>}</div>
                  </div>
                </button></li>
              ))}
            </ul>
          )}
        </section>
        <section aria-label="Conversation" className={cx(!sel && "hidden lg:block")}>
          {sel ? <Thread id={sel} onBack={() => setSp({})} /> : <Empty title="Select a conversation">Pick one on the left to read it, pause the AI, or reply yourself.</Empty>}
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
          <Avatar name={c.name} src={c.avatar_url} size={32} /><span className="truncate font-semibold">{c.name ?? `Customer ${c.id}`}</span><ChannelBadge channel={c.channel} />
          {c.profile_id && <Link className="text-sm font-semibold text-accent-strong underline" to={`/profiles/${c.profile_id}`}>View profile</Link>}
          {c.ai_paused ? <Badge tone="warn">AI paused{c.handover_reason ? `: ${c.handover_reason}` : ""}</Badge> : <Badge tone="ok">AI active</Badge>}
        </div>
        <Button size="sm" onClick={() => toggle.mutate(!c.ai_paused)} loading={toggle.isPending}>{c.ai_paused ? <><Play className="h-3.5 w-3.5" aria-hidden />Resume AI</> : <><Pause className="h-3.5 w-3.5" aria-hidden />Take over</>}</Button>
      </header>
      <div className="flex items-center gap-1.5 border-b border-line bg-sunken/60 px-4 py-1.5 text-xs text-muted"><Clock className="h-3.5 w-3.5" aria-hidden />
        {open ? `Free-form replies allowed for ${Math.floor(windowLeft / 3600e3)} h ${Math.floor((windowLeft % 3600e3) / 60e3)} min` : c.channel === "whatsapp" ? "Window closed: only an approved template can be sent" : "Window closed: replies are blocked (human-agent tag not enabled or expired)"}
        {c.last_user_message_at && <span> (customer last wrote {when(c.last_user_message_at)})</span>}</div>
      {c.ai_paused && <div className="flex flex-wrap items-center justify-between gap-2 border-b border-warn bg-warn-soft px-4 py-2 text-sm text-warn">
        <span><strong>The AI is not replying in this chat</strong> ({c.handover_reason?.startsWith("agent:") ? "a staff member replied" : c.handover_reason?.startsWith("keyword:") ? "the guest asked for a person" : "taken over"}). It answers again on its own after a quiet period without staff replies (AI Settings), or right now:</span>
        <Button size="sm" onClick={() => toggle.mutate(false)} loading={toggle.isPending}><Play className="h-3.5 w-3.5" aria-hidden />Resume AI now</Button></div>}
      <div className="max-h-[52vh] min-h-[280px] flex-1 space-y-3 overflow-y-auto p-4" role="log" aria-label="Messages">
        {c.messages.map((m: any) => {
          const mine = m.role !== "user";
          const Icon = m.role === "user" ? User : m.role === "agent" ? UserRound : Bot;
          return (
            <div key={m.id} className={cx("flex", mine ? "justify-end" : "justify-start")}>
              <div className={cx("max-w-[85%] rounded-panel px-3 py-2", m.role === "user" ? "bg-sunken" : m.role === "agent" ? "bg-blue-soft" : "bg-accent-soft")}>
                <p className="mb-0.5 flex items-center gap-1 text-xs font-semibold text-muted"><Icon className="h-3 w-3" aria-hidden />{m.role === "user" ? "Customer" : m.role === "agent" ? "Staff" : "AI"}, {when(m.created_at)}</p>
                <p className="whitespace-pre-wrap break-words">{m.content}</p>
                {m.attachments?.length > 0 && <p className="mt-1 text-xs text-muted">{m.attachments.length} attachment(s): {m.attachments.map((a: any) => a.type).join(", ")}</p>}
                <div className="mt-1 flex flex-wrap gap-1">
                  {m.delivery_status === "blocked" && <Badge tone="danger">Not sent: {m.flag === "out_of_window" ? "outside messaging window" : m.flag}</Badge>}
                  {m.delivery_status === "failed" && <Badge tone="danger">Delivery failed</Badge>}
                  {m.sources?.path && <Badge>{m.sources.path}</Badge>}
                </div>
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
