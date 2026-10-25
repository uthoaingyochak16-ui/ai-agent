import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookCheck, Check, GraduationCap, Pencil, Plus, Send, Trash2, User as UserIcon, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Empty, ErrorNote, IconButton, Switch, Textarea, cx, useConfirm, useToast } from "../components/ui";
import { DiagnosisCard, type DiagnosisResult } from "../components/Diagnosis";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

type Rule = { id: string; text: string; enabled: boolean; created_at?: string; by?: string };
type Msg = { id: number; role: "staff" | "assistant"; content: string; proposals?: string[]; replaces?: string[]; diagnosis?: DiagnosisResult };
let seq = 1;
const EXAMPLES = [
  "Keu course korte agrohi bolle registration link dao, ar kon shohore thake jiggesh koro.",
  "Reply gulo aro choto koro, 2-3 line er beshi na.",
  "\"Sylhet e office ache?\" eta jiggesh korle keno uttor dite parlo na?",
  "Amar chat e aro ki improvement lagbe?",
];

export default function TeachTab() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length]);

  const rules = useQuery({ queryKey: ["teach-rules"], queryFn: () => api<{ instructions: Rule[] }>("/api/teach/instructions") });
  const list = rules.data?.instructions ?? [];
  const put = useMutation({
    mutationFn: (next: Rule[]) => api<{ instructions: Rule[] }>("/api/teach/instructions", { method: "PUT", body: { instructions: next } }),
    onSuccess: (r) => qc.setQueryData(["teach-rules"], r),
  });
  const chat = useMutation({
    mutationFn: (history: Msg[]) => api<{ reply: string; instructions: string[]; replaces: string[]; diagnosis?: DiagnosisResult }>("/api/teach/chat", { body: { messages: history.map((m) => ({ role: m.role, content: m.content })) } }),
    onSuccess: (r) => setMsgs((m) => [...m, { id: seq++, role: "assistant", content: r.reply, proposals: r.diagnosis ? [] : r.instructions, replaces: r.replaces, diagnosis: r.diagnosis }]),
  });

  const sendText = (m: string) => {
    if (!m.trim() || chat.isPending) return;
    const next = [...msgs, { id: seq++, role: "staff" as const, content: m.trim() }];
    setMsgs(next); setText(""); chat.mutate(next);
  };
  const saveRule = async (ruleText: string, replaces: string[] = []) => {
    const kept = list.filter((r) => !replaces.includes(r.id));
    await put.mutateAsync([...kept, { id: "", text: ruleText, enabled: true } as Rule]);
    setSaved((s) => new Set(s).add(ruleText));
    toast(replaces.length ? "Rule updated. The next reply follows it." : "Rule saved. The next reply follows it.");
  };
  const answerRouteMissing = chat.error instanceof Error && chat.error.message.includes("no route configured for task 'answer'");

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(380px,0.8fr)]">
      <section aria-label="Teach the assistant" className="flex flex-col rounded-panel border border-line bg-surface">
        <div className="min-h-[340px] flex-1 space-y-3 overflow-y-auto p-4" style={{ maxHeight: "62vh" }} role="log" aria-label="Teaching conversation">
          {msgs.length === 0 && (
            <div className="rounded-panel border border-dashed border-line px-5 py-8 text-center">
              <GraduationCap className="mx-auto mb-2 h-6 w-6 text-accent-strong" aria-hidden />
              <p className="font-semibold">Explain how the assistant should behave</p>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted">Write the way you would brief a new front-desk volunteer, in Bangla, Banglish or English. Ask why it failed on a guest question and it will run that question and find the reason, or ask what it should improve.</p>
              <div className="mt-4 flex flex-col items-center gap-2">
                {EXAMPLES.map((e) => <button key={e} type="button" disabled={!admin} onClick={() => sendText(e)} className="max-w-md rounded-full border border-line px-3 py-1.5 text-sm hover:border-accent hover:bg-sunken disabled:opacity-50">{e}</button>)}
              </div>
            </div>
          )}
          {msgs.map((m) => m.role === "staff" ? (
            <div key={m.id} className="flex justify-end"><div className="max-w-[85%] rounded-panel bg-blue-soft px-3 py-2"><p className="mb-0.5 flex items-center gap-1 text-xs font-semibold text-muted"><UserIcon className="h-3 w-3" aria-hidden />You</p><p className="whitespace-pre-wrap break-words">{m.content}</p></div></div>
          ) : (
            <div key={m.id} className="flex justify-start">
              <div className="max-w-[90%] rounded-panel bg-sunken px-3 py-2">
                <p className="mb-0.5 flex items-center gap-1 text-xs font-semibold text-muted"><GraduationCap className="h-3 w-3" aria-hidden />Teaching helper</p>
                {m.diagnosis ? <div className="mt-1"><p className="mb-2 text-xs text-muted">I ran "{m.diagnosis.question}" as a guest would and checked how the reply was made:</p><DiagnosisCard d={m.diagnosis} /></div>
                  : <p className="whitespace-pre-wrap break-words">{m.content}</p>}
                {!!m.proposals?.length && (
                  <div className="mt-3 space-y-2">
                    <p className="text-xs font-semibold text-muted">Proposed rule{m.proposals.length > 1 ? "s" : ""}{m.replaces?.length ? " (replaces an existing rule)" : ""}</p>
                    {m.proposals.map((p) => (
                      <div key={p} className="flex items-start gap-2 rounded-ctl border border-line bg-surface p-2.5">
                        <BookCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent-strong" aria-hidden />
                        <p className="flex-1 text-sm">{p}</p>
                        {saved.has(p) ? <Badge tone="ok"><Check className="h-3 w-3" aria-hidden />Saved</Badge>
                          : admin && <Button size="sm" variant="primary" loading={put.isPending} onClick={() => saveRule(p, m.replaces)}>Save</Button>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          {chat.isPending && <p role="status" className="text-sm text-muted">Thinking</p>}
          <div ref={end} />
        </div>
        <form className="space-y-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); sendText(text); }}>
          {answerRouteMissing ? (
            <div role="alert" className="space-y-2 rounded-ctl border border-warn/50 bg-warn-soft px-3 py-2 text-sm text-warn">
              <p>No answer model is assigned yet. Saving an API key does not choose which model handles answers.</p>
              <Link to="/ai?tab=routing" className="inline-flex rounded-ctl border border-warn/40 px-3 py-1.5 font-semibold hover:bg-surface">Configure the Answer model</Link>
            </div>
          ) : <ErrorNote error={chat.error} />}
          <ErrorNote error={put.error} />
          {!admin && <p className="text-sm text-muted">Only admins can teach the assistant.</p>}
          <div className="flex gap-2">
            <Textarea aria-label="Instruction" rows={2} className="min-h-[44px] flex-1" disabled={!admin} placeholder="e.g. Keu 'ha' bolle ager proshner sathe mil rekhe porer step bolo" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendText(text); } }} />
            <Button type="submit" variant="primary" className="self-end" loading={chat.isPending} disabled={!admin || !text.trim()}><Send className="h-4 w-4" aria-hidden />Send</Button>
          </div>
          {msgs.length > 0 && <Button size="sm" variant="ghost" onClick={() => { setMsgs([]); setSaved(new Set()); }}>Start over</Button>}
        </form>
      </section>

      <RuleList rules={list} loading={rules.isLoading} admin={admin} saving={put.isPending}
        onChange={(next) => put.mutate(next)}
        onDelete={async (r) => (await ask({ title: "Delete this rule?", message: r.text, confirmLabel: "Delete", danger: true })) && put.mutate(list.filter((x) => x.id !== r.id))} />
    </div>
  );
}

function RuleList({ rules, loading, admin, saving, onChange, onDelete }: { rules: Rule[]; loading: boolean; admin: boolean; saving: boolean; onChange: (r: Rule[]) => void; onDelete: (r: Rule) => void }) {
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [adding, setAdding] = useState("");
  const on = rules.filter((r) => r.enabled).length;
  return (
    <section aria-label="Saved rules" className="rounded-panel border border-line bg-surface xl:sticky xl:top-4 xl:self-start">
      <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 className="font-bold">Saved rules</h2>
        <Badge tone={on ? "ok" : "neutral"}>{on} active</Badge>
      </header>
      <div className="space-y-2 p-4">
        <p className="text-xs text-muted">Active rules are added to every answer. They can never override privacy, safety or the "don't invent facts" rules.</p>
        {loading ? null : !rules.length ? <Empty title="No rules yet">Teach the assistant on the left, then save a rule.</Empty> : rules.map((r) => (
          <div key={r.id} className={cx("rounded-ctl border border-line p-2.5", !r.enabled && "opacity-60")}>
            {editing?.id === r.id ? (
              <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (editing.text.trim()) { onChange(rules.map((x) => x.id === r.id ? { ...x, text: editing.text.trim() } : x)); setEditing(null); } }}>
                <Textarea aria-label="Rule" rows={3} value={editing.text} onChange={(e) => setEditing({ ...editing, text: e.target.value })} />
                <div className="flex justify-end gap-2"><Button size="sm" onClick={() => setEditing(null)}><X className="h-3.5 w-3.5" aria-hidden />Cancel</Button><Button size="sm" type="submit" variant="primary" loading={saving}>Save</Button></div>
              </form>
            ) : (
              <>
                <p className="text-sm">{r.text}</p>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <Switch checked={r.enabled} disabled={!admin || saving} onChange={(v) => onChange(rules.map((x) => x.id === r.id ? { ...x, enabled: v } : x))} label={r.enabled ? "On" : "Off"} />
                  {admin && <div className="flex gap-1">
                    <IconButton label="Edit this rule" className="h-8 w-8" onClick={() => setEditing({ id: r.id, text: r.text })}><Pencil className="h-3.5 w-3.5" aria-hidden /></IconButton>
                    <IconButton label="Delete this rule" className="h-8 w-8" onClick={() => onDelete(r)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></IconButton>
                  </div>}
                </div>
              </>
            )}
          </div>
        ))}
        {admin && (
          <form className="flex gap-2 border-t border-line pt-3" onSubmit={(e) => { e.preventDefault(); if (adding.trim()) { onChange([...rules, { id: "", text: adding.trim(), enabled: true }]); setAdding(""); } }}>
            <Textarea aria-label="Write a rule yourself" rows={1} className="min-h-[38px] flex-1" placeholder="Or write a rule yourself" value={adding} onChange={(e) => setAdding(e.target.value)} />
            <Button size="sm" type="submit" className="self-end" disabled={!adding.trim()} loading={saving}><Plus className="h-3.5 w-3.5" aria-hidden />Add</Button>
          </form>
        )}
      </div>
    </section>
  );
}
