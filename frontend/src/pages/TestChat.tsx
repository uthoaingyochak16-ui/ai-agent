import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Send, ShieldCheck, ThumbsDown, User as UserIcon, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Empty, ErrorNote, Field, Input, Meter, Modal, PageHeader, Panel, Select, Switch, Tabs, Textarea, useToast, cx } from "../components/ui";
import ReplyInspector from "../components/ReplyInspector";
import TeachTab from "./TeachTab";
import { api } from "../lib/api";
import { num, usd } from "../lib/format";

type Turn = { id: number; role: "user" | "assistant"; content: string; debug?: any; delivery?: string; paused?: boolean; profile?: any; followup?: boolean };
const PATH: Record<string, { label: string; tone: "ok" | "blue" | "warn" | "danger" | "neutral"; note: string }> = {
  qa_shortcut: { label: "Q&A shortcut", tone: "ok", note: "Answered from your Q&A table. No AI call, no cost." },
  cache: { label: "Semantic cache", tone: "ok", note: "A very similar question was answered recently. No AI call, no cost." },
  llm: { label: "AI, standard model", tone: "blue", note: "Answered by the standard model from the retrieved knowledge." },
  llm_large: { label: "AI, large model", tone: "warn", note: "Escalated to the large model because the match was weak or the question complex." },
  no_context: { label: "Nothing found", tone: "warn", note: "No knowledge matched, so a canned reply offers a person. No AI call, no cost." },
  llm_unavailable: { label: "AI unavailable", tone: "danger", note: "No provider could answer; the canned reply was used." },
  handover: { label: "Handed to a person", tone: "warn", note: "The AI is paused for this conversation." },
  none: { label: "No reply generated", tone: "neutral", note: "No AI reply was produced for this message." },
};
let seq = 1;
const newSession = () => Math.random().toString(36).slice(2, 12);

export default function TestChat() {
  const [tab, setTab] = useState<"chat" | "teach">("chat");
  return (
    <>
      <PageHeader title="Test chat" subtitle={tab === "chat" ? "Talk to the assistant exactly as a customer would. Nothing is sent to Meta. Select any reply to inspect how it was produced." : "Tell the assistant in your own words how it should behave. It turns that into rules; nothing changes until you save one."} />
      <Tabs<"chat" | "teach"> value={tab} onChange={setTab} tabs={[{ id: "chat", label: "Customer chat" }, { id: "teach", label: "Teach the assistant" }]} />
      {/* both stay mounted, so switching tabs keeps each conversation */}
      <div hidden={tab !== "chat"}><CustomerChat /></div>
      <div hidden={tab !== "teach"}><TeachTab /></div>
    </>
  );
}

function CustomerChat() {
  const toast = useToast();
  const [channel, setChannel] = useState("messenger");
  const [profileId, setProfileId] = useState("");
  const [noSave, setNoSave] = useState(false);
  const [sid, setSid] = useState(newSession);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [fix, setFix] = useState<{ q: string; a: string } | null>(null);
  // a reply marked wrong is never served again from the shared answer cache
  const markWrong = (answer: string) => api<{ blocked: number }>("/api/cache/feedback", { body: { answer } })
    .then((r) => { if (r.blocked) toast(`${r.blocked} cached copy of this answer blocked`); }).catch(() => {});
  const end = useRef<HTMLDivElement>(null);
  const profiles = useQuery({ queryKey: ["profiles-select"], queryFn: () => api("/api/profiles", { params: { limit: 100 } }) });
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [turns.length]);
  const reset = () => { setTurns([]); setSel(null); setSid(newSession()); };

  const send = useMutation({
    mutationFn: (message: string) => api("/api/test-chat", { body: { message, channel, session_id: sid, profile_id: profileId ? +profileId : null, no_save: noSave, history: noSave ? turns.map((t) => ({ role: t.role, content: t.content })) : [] } }),
    onSuccess: (r) => {
      const replies: Turn[] = r.messages.filter((m: any) => m.role === "assistant").map((m: any) => ({ id: seq++, role: "assistant", content: m.content, delivery: m.delivery_status, debug: r.debug, followup: m.sources?.path === "followup", paused: r.ai_paused, profile: r.profile }));
      if (!replies.length) replies.push({ id: seq++, role: "assistant", content: "", debug: r.debug, paused: r.ai_paused, profile: r.profile });
      setTurns((t) => [...t, ...replies]);
      setSel(replies[0].id);
    },
  });
  const submit = () => { const m = text.trim(); if (!m || send.isPending) return; setTurns((t) => [...t, { id: seq++, role: "user", content: m }]); setText(""); send.mutate(m); };
  const selected = useMemo(() => turns.find((t) => t.id === sel) ?? null, [turns, sel]);
  const prevUser = (id: number) => { const i = turns.findIndex((t) => t.id === id); for (let k = i - 1; k >= 0; k--) if (turns[k].role === "user") return turns[k].content; return ""; };

  return (
    <>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(420px,0.95fr)]">
        <section aria-label="Chat" className="flex flex-col rounded-panel border border-line bg-surface">
          <div className="grid gap-3 border-b border-line p-3 sm:grid-cols-[180px_minmax(0,1fr)_auto]">
            <Field label="Simulated channel">{(i) => <Select id={i} value={channel} onChange={(e) => { setChannel(e.target.value); reset(); }}><option value="messenger">Messenger</option><option value="instagram">Instagram</option><option value="whatsapp">WhatsApp</option></Select>}</Field>
            <Field label="Customer">{(i) => <Select id={i} value={profileId} onChange={(e) => { setProfileId(e.target.value); reset(); }}><option value="">New anonymous user</option>{profiles.data?.items.map((p: any) => <option key={p.id} value={p.id}>{p.name || `Anonymous ${p.id}`} (profile {p.id})</option>)}</Select>}</Field>
            <div className="flex items-end gap-2 pb-1"><Button onClick={reset}><RotateCcw className="h-4 w-4" aria-hidden />New chat</Button></div>
            <div className="sm:col-span-3">
              <Switch checked={noSave} onChange={(v) => { setNoSave(v); reset(); }} label="Do not save to the database" />
              <p className="mt-1 text-xs text-muted">{noSave ? "Every message runs in a transaction that is rolled back: no profile, conversation, cache entry or Q&A is kept. Profile changes such as consent do not carry over between messages. AI cost is still logged." : "Saved chats appear under the profile's history but not in the Inbox or the dashboard counts."}</p>
            </div>
          </div>
          <div className="min-h-[340px] flex-1 space-y-3 overflow-y-auto p-4" style={{ maxHeight: "58vh" }} role="log" aria-label="Test conversation">
            {turns.length === 0 && <Empty title="Say something">Try a question from your documents, a greeting from your Q&A table, a message with a phone number, or "I want a human agent".</Empty>}
            {turns.map((t) => t.role === "user" ? (
              <div key={t.id} className="flex justify-end"><div className="max-w-[85%] rounded-panel bg-blue-soft px-3 py-2"><p className="mb-0.5 flex items-center gap-1 text-xs font-semibold text-muted"><UserIcon className="h-3 w-3" aria-hidden />Customer</p><p className="whitespace-pre-wrap break-words">{t.content}</p></div></div>
            ) : (
              <div key={t.id} className="flex justify-start">
                <div className={cx("max-w-[85%] rounded-panel border px-3 py-2", sel === t.id ? "border-accent bg-accent-soft" : "border-transparent bg-sunken")}>
                  <p className="mb-0.5 flex items-center gap-1 text-xs font-semibold text-muted"><Bot className="h-3 w-3" aria-hidden />Assistant{t.followup && <Badge className="ml-1">follow-up</Badge>}{!t.followup && t.debug?.path && <Badge tone={PATH[t.debug.path]?.tone ?? "neutral"} className="ml-1">{PATH[t.debug.path]?.label ?? t.debug.path}</Badge>}</p>
                  {t.content ? <p className="whitespace-pre-wrap break-words">{t.content}</p> : <p className="italic text-muted">{t.paused ? "No reply: the AI is paused for this conversation." : "No reply was generated."}</p>}
                  <div className="mt-2 flex flex-wrap gap-2"><Button size="sm" variant="ghost" aria-pressed={sel === t.id} onClick={() => setSel(t.id)}><ShieldCheck className="h-3.5 w-3.5" aria-hidden />Inspect</Button>
                    {t.content && <Button size="sm" variant="ghost" onClick={() => { setFix({ q: prevUser(t.id), a: t.content }); markWrong(t.content); }}><ThumbsDown className="h-3.5 w-3.5" aria-hidden />Answer is wrong</Button>}</div>
                </div>
              </div>
            ))}
            {send.isPending && <p role="status" className="text-sm text-muted">Assistant is typing</p>}
            <div ref={end} />
          </div>
          <form className="space-y-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <ErrorNote error={send.error} />
            <div className="flex gap-2"><Textarea aria-label="Message" rows={2} className="min-h-[44px] flex-1" placeholder="Type a customer message" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
              <Button type="submit" variant="primary" className="self-end" loading={send.isPending} disabled={!text.trim()}><Send className="h-4 w-4" aria-hidden />Send</Button></div>
          </form>
        </section>
        <div className="xl:sticky xl:top-4 xl:self-start"><DebugPanel turn={selected} /></div>
      </div>
      {fix && <FixModal q={fix.q} a={fix.a} onClose={() => setFix(null)} onSaved={(evalAdded) => { setFix(null); toast(evalAdded ? "Saved to Q&A and the evaluation set." : "Saved to the Q&A table. The next customer asking this gets your answer."); }} />}
    </>
  );
}

function FixModal({ q, a, onClose, onSaved }: { q: string; a: string; onClose: () => void; onSaved: (evalAdded: boolean) => void }) {
  const qc = useQueryClient();
  const [question, setQ] = useState(q);
  const [answer, setA] = useState("");
  const [language, setLanguage] = useState(/[\u0980-\u09ff]/.test(q) ? "bn" : "banglish");
  const [clearAll, setClearAll] = useState(false);
  const save = useMutation({ mutationFn: async (addEval: boolean) => {
    await api("/api/qa", { body: { question, answer_template: answer, category: "corrections", active: true } });
    if (addEval) await api("/api/eval/cases", { body: { question, expected_answer: answer, language, tags: ["correction"], active: true } });
    if (clearAll) await api("/api/cache/feedback", { body: { answer: a, clear_all: true } });
    return addEval;
  }, onSuccess: (addEval) => { qc.invalidateQueries({ queryKey: ["qa"] }); qc.invalidateQueries({ queryKey: ["eval-cases"] }); onSaved(addEval); } });
  return (
    <Modal wide title="Save a corrected answer" onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(false); }}>
        <div className="rounded-ctl bg-sunken px-3 py-2 text-sm"><p className="font-semibold">The assistant said</p><p className="whitespace-pre-wrap">{a}</p></div>
        <Field label="Question">{(i) => <Input id={i} required value={question} onChange={(e) => setQ(e.target.value)} />}</Field>
        <Field label="Correct answer" hint="Use {variable} to insert values from the Variables panel.">{(i) => <Textarea id={i} required rows={5} value={answer} onChange={(e) => setA(e.target.value)} />}</Field>
        <Switch checked={clearAll} onChange={setClearAll} label="Also clear the whole answer cache (verified answers are kept)" />
        <Field label="Evaluation language">{(i) => <Select id={i} value={language} onChange={(e) => setLanguage(e.target.value)}><option value="bn">Bangla</option><option value="banglish">Banglish</option><option value="en">English</option></Select>}</Field>
        <ErrorNote error={save.error} />
        <div className="flex flex-wrap justify-end gap-2"><Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" loading={save.isPending}>Save to Q&A</Button><Button type="button" variant="primary" loading={save.isPending} disabled={!question.trim() || !answer.trim()} onClick={() => save.mutate(true)}>Save + add to eval set</Button></div>
      </form>
    </Modal>
  );
}

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => <section className="border-t border-line pt-3 first:border-0 first:pt-0"><h3 className="mb-2 text-sm font-bold">{title}</h3>{children}</section>;
const KV = ({ k, v }: { k: string; v: React.ReactNode }) => <div className="flex justify-between gap-3 py-0.5 text-sm"><dt className="text-muted">{k}</dt><dd className="text-right font-semibold tabular-nums">{v}</dd></div>;

function DebugPanel({ turn }: { turn: Turn | null }) {
  if (!turn?.debug) return <Panel title="How this answer was made"><Empty title="Nothing selected">Send a message, then select a reply to see the path, matches, prompt and cost.</Empty></Panel>;
  const d = turn.debug;
  const p = PATH[d.path] ?? PATH.none;
  const kinds: Record<string, string[]> = {};
  (d.redactions ?? []).forEach((r: any) => (kinds[r.kind] ??= []).push(r.placeholder));
  const prof = turn.profile;
  return (
    <Panel title="How this answer was made" actions={<Badge tone={p.tone}>{p.label}</Badge>}>
      <div className="space-y-4">
        {d.inspector && <Section title="Reply inspector"><ReplyInspector key={turn.id} i={d.inspector} /></Section>}
        <Section title="Path"><p className="text-sm">{p.note}</p>{d.error && <p className="mt-1 text-sm text-danger">{d.error}</p>}
          <dl className="mt-2"><KV k="Latency" v={`${d.latency_ms ?? 0} ms`} />{d.tier && <KV k="Tier" v={d.tier === "large" ? "Large model" : "Standard model"} />}{d.fallback_used && <KV k="Fallback provider used" v="Yes" />}{d.budget_fallback && <KV k="Budget fallback" v="Yes" />}{d.cached && <KV k="Saved to cache" v="Yes" />}</dl></Section>

        {d.question_rewrite && <Section title="Question used for retrieval"><div className="space-y-2 text-sm"><div><p className="text-xs font-semibold text-muted">Customer message</p><p className="mt-0.5 whitespace-pre-wrap">{d.question_rewrite.original}</p></div><div><p className="text-xs font-semibold text-muted">Standalone question {d.question_rewrite.used ? "(rewritten)" : "(original)"}</p><p className="mt-0.5 whitespace-pre-wrap rounded-ctl bg-sunken p-2">{d.question_rewrite.rewritten}</p></div></div></Section>}

        {(d.qa_match || d.cache || d.qa_candidates?.length > 0) && (
          <Section title="Q&A and cache matches">
            {d.qa_match && <p className="mb-2 rounded-ctl bg-ok-soft px-3 py-2 text-sm text-ok">Matched Q&A #{d.qa_match.id}: "{d.qa_match.question}" ({d.qa_match.exact ? "exact" : `similarity ${d.qa_match.similarity}`})</p>}
            {d.cache && <p className="mb-2 rounded-ctl bg-ok-soft px-3 py-2 text-sm text-ok">Cached answer to "{d.cache.cached_question}" (similarity {d.cache.similarity})</p>}
            {d.qa_candidates?.length > 0 && <ul className="space-y-1.5">{d.qa_candidates.map((c: any) => <li key={c.id} className="text-sm"><div className="flex justify-between gap-2"><span className="truncate">{c.question}</span><span className="tabular-nums text-muted">{c.similarity}</span></div><Meter value={Math.max(0, c.similarity)} tone={c.similarity >= 0.92 ? "accent" : "blue"} label={`Q&A similarity ${c.similarity}`} /></li>)}</ul>}
          </Section>
        )}

        {d.keyword_search && <Section title="Keyword search">
          <p className="text-sm"><Badge tone={d.keyword_search.used ? "ok" : "neutral"}>{d.keyword_search.used ? "used" : "not used (old search ran)"}</Badge> {d.keyword_search.source && <span className="text-muted">keywords from {d.keyword_search.source === "ai" ? "the AI" : "word rules"}{d.keyword_search.intent ? ` · wants: ${d.keyword_search.intent}` : ""}</span>}</p>
            <p className="mt-1 text-sm">{(d.keyword_search.keywords ?? []).map((k: string) => <Badge key={k} className="mr-1">{k}</Badge>)}</p>
            <p className="mt-1 text-xs text-muted">coverage {d.keyword_search.coverage ?? "-"} · {d.keyword_search.ms ?? "-"} ms{d.keyword_search.ai_note ? ` · ${d.keyword_search.ai_note}` : ""}{d.keyword_search.reason ? ` · ${d.keyword_search.reason}` : ""}</p>
          </Section>}

        {d.chunks && (
          <Section title={`Knowledge retrieved (${d.chunks.length})`}>
            {d.chunks.length === 0 ? <p className="text-sm text-muted">No chunk reached the minimum similarity.</p> : (
              <ul className="space-y-2">{d.chunks.map((c: any) => (
                <li key={c.chunk_id} className="rounded-ctl border border-line p-2 text-sm"><div className="flex justify-between gap-2"><span className="font-semibold">{c.document}{c.page ? `, page ${c.page}` : ""}</span><span className="tabular-nums text-muted">score {c.similarity}</span></div>
                  <p className="mb-1 text-xs text-muted">vector {c.vector} · keywords {c.keywords}{c.rerank != null ? ` · rerank ${c.rerank}` : ""}</p>
                  <Meter value={c.similarity} label={`Similarity ${c.similarity}`} /><p className="mt-1 text-xs text-muted">{c.preview}</p></li>))}</ul>
            )}
            {d.retrieval_confidence !== undefined && <p className="mt-2 text-xs text-muted">Best match {d.retrieval_confidence}</p>}
          </Section>
        )}

        {d.prompt && (
          <Section title="Exact prompt sent to the AI (after privacy filtering)">
            <details className="rounded-ctl border border-line"><summary className="cursor-pointer px-3 py-2 text-sm font-semibold">Show prompt ({num(d.est_tokens_in ?? 0)} tokens estimated)</summary>
              <div className="space-y-2 border-t border-line p-3"><div><p className="text-xs font-semibold text-muted">System</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-ctl bg-sunken p-2 text-xs">{d.prompt.system}</pre></div>
                {d.prompt.messages.map((m: any, i: number) => <div key={i}><p className="text-xs font-semibold text-muted">{m.role}</p><pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-ctl bg-sunken p-2 text-xs">{m.content}</pre></div>)}</div></details>
          </Section>
        )}

        {d.prompt && (
          <Section title="Redacted before sending">
            {Object.keys(kinds).length === 0 ? <p className="text-sm text-muted">Nothing needed redaction.</p> : <ul className="space-y-1 text-sm">{Object.entries(kinds).map(([k, v]) => <li key={k}><Badge>{k}</Badge> <span className="ml-1 font-mono text-xs">{v.join(" ")}</span></li>)}</ul>}
            <p className="mt-1 text-xs text-muted">Original values stay in memory for this request only and are put back into the reply.</p>
            {d.output_blocked && <p className="mt-2 rounded-ctl bg-danger-soft px-3 py-2 text-sm text-danger">The reply was blocked by the output filter ({d.output_reasons.join(", ")}) and replaced by a safe message.</p>}
          </Section>
        )}

        {d.model && (
          <Section title="Model and cost"><dl>
            <KV k="Provider and model" v={`${d.provider} / ${d.model}`} /><KV k="Tokens in" v={num(d.tokens_in)} /><KV k="Tokens out" v={num(d.tokens_out)} /><KV k="Estimated cost" v={usd(d.cost_usd)} /></dl></Section>
        )}

        <Section title="Customer profile">
          {prof ? (
            <dl><KV k="Profile" v={`#${prof.id}`} /><KV k="Consent" v={prof.consent} />
              <div className="py-1 text-sm"><dt className="text-muted">Summary the AI receives</dt><dd className="mt-0.5 rounded-ctl bg-sunken px-2 py-1">{d.profile_injected?.summary || "(none)"}</dd></div>
              <div className="py-1 text-sm"><dt className="text-muted">Fields it may ask for</dt><dd className="mt-0.5">{d.profile_injected?.missing_fields?.length ? d.profile_injected.missing_fields.join(", ") : "none (consent is needed first, or nothing is missing)"}</dd></div>
              {d.post_reply?.info ? <div className="py-1 text-sm"><dt className="text-muted">Guest info in this message</dt><dd className="mt-0.5">
                <ul className="space-y-0.5">{d.post_reply.info.pieces.map((p: any, i: number) => (
                  <li key={i} className="flex flex-wrap items-center gap-1.5"><Badge tone={p.type === "guest_info" ? "ok" : p.type === "question" ? "blue" : "neutral"}>{p.type === "guest_info" ? "guest info" : p.type ?? (p.flagged ? "flagged" : "no keywords")}</Badge><span>{p.text}</span></li>))}</ul>
                <p className="mt-1 text-xs text-muted">{!d.post_reply.info.checked ? "No personal-info keywords: no AI call." : d.post_reply.info.saved?.length ? `Saved: ${d.post_reply.info.saved.map((e: any) => `${e.key} (${e.status})`).join(", ")}` : "Checked by the AI: nothing to save."}</p>
              </dd></div> : <div className="py-1 text-sm"><dt className="text-muted">Extracted from this chat</dt><dd className="mt-0.5">{d.post_reply?.extracted?.length ? d.post_reply.extracted.map((e: any) => `${e.key} (${e.status})`).join(", ") : "nothing this turn (runs every few messages)"}</dd></div>}
              {prof.pending_fields?.length > 0 && <div className="py-1 text-sm"><dt className="text-muted">Held until consent</dt><dd className="mt-0.5">{prof.pending_fields.join(", ")}</dd></div>}
              {d.events?.length > 0 && <div className="py-1 text-sm"><dt className="text-muted">Identity events</dt><dd className="mt-0.5">{d.events.join("; ")}</dd></div>}
            </dl>
          ) : <p className="text-sm text-muted">No profile information for this turn.</p>}
        </Section>
      </div>
    </Panel>
  );
}
