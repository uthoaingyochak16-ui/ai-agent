import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookCheck, Check, FileQuestion, FlaskConical, MessageSquareText, RotateCcw, Search } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Badge, Button, ErrorNote, Textarea, useToast } from "./ui";

export type Fix = { type: "rule" | "qa" | "knowledge" | "message"; text?: string; question?: string; answer?: string; note?: string };
export type DiagnosisResult = { facts: string[]; summary: string | null; cause: string | null; fixes: Fix[]; question: string; reply: string | null; path?: string; error?: string };
type Turn = { role: "user" | "assistant"; content: string };

const CAUSE: Record<string, string> = {
  missing_knowledge: "Knowledge is missing", knowledge_not_matched: "Knowledge not found / not used", misunderstood: "Question misunderstood",
  rule_or_style: "Behaviour / style", saved_answer_wrong: "A saved answer is wrong", ready_message: "A ready-made message was sent",
  safety_filter: "Safety filter", policy: "By policy", technical: "Technical problem", other: "Other",
};

/** "Find the reason" for one reply: the staff member says (casually) what is wrong, the system runs the trace and the AI
 *  explains the cause and proposes fixes, each saved with one click. */
export function FindReason({ question, reply, debug, history = [], unansweredId, compact }: {
  question: string; reply?: string | null; debug?: any; history?: Turn[]; unansweredId?: number; compact?: boolean;
}) {
  const [complaint, setComplaint] = useState("");
  const run = useMutation({
    mutationFn: () => api<DiagnosisResult>("/api/teach/diagnose", { body: { question, reply: reply ?? null, debug: debug ?? null, history, complaint } }),
  });
  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <Textarea persistDraft={false} rows={compact ? 1 : 2} className="min-h-[40px] flex-1" aria-label="What is wrong (optional)"
                  placeholder="What is wrong? (optional, any language) e.g. eta bhul, office er phone number dite hobe"
                  value={complaint} onChange={(e) => setComplaint(e.target.value)} />
        <Button variant="primary" className="self-end" loading={run.isPending} onClick={() => run.mutate()}><Search className="h-4 w-4" aria-hidden />Find the reason</Button>
      </div>
      <ErrorNote error={run.error} />
      {run.data && <DiagnosisCard d={run.data} history={history} unansweredId={unansweredId} />}
    </div>
  );
}

export function DiagnosisCard({ d, history = [], unansweredId }: { d: DiagnosisResult; history?: Turn[]; unansweredId?: number }) {
  return (
    <div className="space-y-3 rounded-panel border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        {d.cause && <Badge tone="warn">{CAUSE[d.cause] ?? d.cause}</Badge>}
        {d.path && <Badge>reply came from: {d.path}</Badge>}
      </div>
      {d.error && <p className="text-sm text-danger">{d.error}</p>}
      {d.summary && <p className="whitespace-pre-wrap">{d.summary}</p>}
      {d.facts.length > 0 && (
        <details className="text-sm"><summary className="cursor-pointer font-semibold text-muted">What the system saw ({d.facts.length})</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{d.facts.map((f) => <li key={f}>{f}</li>)}</ul></details>
      )}
      {d.reply != null && <p className="rounded-ctl bg-sunken p-2 text-sm"><b>Reply now:</b> {d.reply || "(no reply)"}</p>}
      {d.fixes.length > 0 && <div className="space-y-2"><p className="text-xs font-semibold text-muted">How to fix it</p>
        {d.fixes.map((f, i) => <FixRow key={i} f={f} question={d.question} history={history} unansweredId={unansweredId} />)}</div>}
    </div>
  );
}

function FixRow({ f, question, history, unansweredId }: { f: Fix; question: string; history: Turn[]; unansweredId?: number }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const [done, setDone] = useState(false);
  const [answer, setAnswer] = useState(f.answer ?? "");
  const [retry, setRetry] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      if (f.type === "rule") {
        const cur = await api<{ instructions: any[] }>("/api/teach/instructions");
        await api("/api/teach/instructions", { method: "PUT", body: { instructions: [...cur.instructions, { id: "", text: f.text, enabled: true }] } });
      } else if (f.type === "qa") {
        await api("/api/qa", { body: { question: f.question || question, answer_template: answer, category: "corrections", active: true, resolve_unanswered_id: unansweredId ?? null } });
      }
    },
    onSuccess: () => { setDone(true); qc.invalidateQueries({ queryKey: ["teach-rules"] }); qc.invalidateQueries({ queryKey: ["unanswered"] }); toast(f.type === "rule" ? "Rule saved. The next reply follows it." : "Saved to Q&A."); },
  });
  const asTest = useMutation({
    mutationFn: () => api("/api/eval/cases", { body: { question, expected_answer: f.type === "qa" ? answer : f.text, language: /[ঀ-৿]/.test(question) ? "bn" : "banglish", tags: ["from-diagnosis"], history, active: true } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["eval-cases"] }); toast("Added to the test set"); },
  });
  const tryAgain = useMutation({
    mutationFn: () => api("/api/test-chat", { body: { message: question, no_save: true, history } }),
    onSuccess: (r: any) => setRetry(r.messages.filter((m: any) => m.role === "assistant").map((m: any) => m.content).join("\n") || "(no reply)"),
  });
  const Icon = { rule: BookCheck, qa: FileQuestion, knowledge: FlaskConical, message: MessageSquareText }[f.type];
  const label = { rule: "Team rule", qa: "Saved Q&A", knowledge: "Add knowledge", message: "Edit a ready-made message" }[f.type];
  return (
    <div className="rounded-ctl border border-line p-2.5 text-sm">
      <p className="mb-1 flex items-center gap-1 text-xs font-semibold text-muted"><Icon className="h-3.5 w-3.5" aria-hidden />{label}</p>
      {f.type === "rule" && <p>{f.text}</p>}
      {f.type === "qa" && <div className="space-y-1"><p><b>Q:</b> {f.question || question}</p>
        <Textarea persistDraft={false} rows={2} aria-label="Answer to save" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="The correct answer" /></div>}
      {(f.type === "knowledge" || f.type === "message") && <p>{f.note}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        {(f.type === "rule" || f.type === "qa") && (done ? <Badge tone="ok"><Check className="h-3 w-3" aria-hidden />Saved</Badge>
          : <Button size="sm" variant="primary" disabled={(f.type === "rule" && !admin) || (f.type === "qa" && !answer.trim())} loading={save.isPending} onClick={() => save.mutate()}>Save</Button>)}
        {done && <Button size="sm" loading={tryAgain.isPending} onClick={() => tryAgain.mutate()}><RotateCcw className="h-3.5 w-3.5" aria-hidden />Try the question again</Button>}
        {(f.type === "rule" || f.type === "qa") && <Button size="sm" variant="ghost" loading={asTest.isPending} disabled={f.type === "qa" && !answer.trim()} onClick={() => asTest.mutate()}>Add to test set</Button>}
      </div>
      {f.type === "rule" && !admin && <p className="mt-1 text-xs text-muted">Only an admin can save rules.</p>}
      {retry != null && <p className="mt-2 whitespace-pre-wrap rounded-ctl bg-ok-soft p-2"><b>Now:</b> {retry}</p>}
      <ErrorNote error={save.error || asTest.error || tryAgain.error} />
    </div>
  );
}
