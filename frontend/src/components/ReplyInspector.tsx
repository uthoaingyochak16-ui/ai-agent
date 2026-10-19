import { HelpCircle } from "lucide-react";
import { useState } from "react";
import { Badge, Button } from "./ui";

export type Inspection = {
  question: string; language: string; question_type: string; path: string; path_group: string; profile_facts: string[];
  matched: string[]; keywords: string[]; context: string[]; model: string | null; errors: string[]; final_reply: string;
  followups: string[]; why: string[];
};

const TYPE_LABEL: Record<string, string> = {
  greeting: "Greeting", general: "General question", org_info: "Organization information", program_date: "Program date / schedule",
  program_fee: "Program fee", program_location: "Program location", enrolment: "Enrolment / callback", unknown: "Unknown question",
  handover: "Human handover",
};

const Row = ({ k, children }: { k: string; children: React.ReactNode }) => (
  <div className="grid grid-cols-[130px_minmax(0,1fr)] gap-2 py-1 text-sm"><dt className="text-muted">{k}</dt><dd className="min-w-0 break-words">{children}</dd></div>
);
const none = <span className="text-muted">none</span>;

/** How one reply was made, in plain words. "Why did this happen?" shows the diagnosis. */
export default function ReplyInspector({ i, openWhy = false }: { i: Inspection; openWhy?: boolean }) {
  const [why, setWhy] = useState(openWhy || !i.final_reply);
  const troubled = !i.final_reply || i.errors.length > 0 || ["no_context", "llm_unavailable"].includes(i.path);
  return (
    <div>
      <dl>
        <Row k="Question">{i.question || none}</Row>
        <Row k="Language">{i.language}</Row>
        <Row k="Question type">{TYPE_LABEL[i.question_type] ?? i.question_type}</Row>
        <Row k="Reply path"><Badge tone={troubled ? "warn" : "ok"}>{i.path_group}</Badge> <span className="text-xs text-muted">({i.path})</span></Row>
        <Row k="Profile facts">{i.profile_facts.length ? i.profile_facts.join(" · ") : none}</Row>
        <Row k="Matched data">{i.matched.length ? <ul className="space-y-0.5">{i.matched.map((m) => <li key={m}>{m}</li>)}</ul> : none}</Row>
        <Row k="Keywords">{i.keywords.length ? i.keywords.map((k) => <Badge key={k} className="mr-1">{k}</Badge>) : none}</Row>
        <Row k="Context used">{i.context.length ? <ul className="space-y-0.5 text-xs">{i.context.map((c, n) => <li key={n}>{c}</li>)}</ul> : none}</Row>
        <Row k="Model">{i.model ?? <span className="text-muted">no AI call</span>}</Row>
        <Row k="Errors / fallback">{i.errors.length ? <span className="text-danger">{i.errors.join("; ")}</span> : none}</Row>
        <Row k="Final reply">{i.final_reply ? <span className="whitespace-pre-wrap">{i.final_reply}</span> : <span className="italic text-danger">empty</span>}</Row>
        {i.followups.length > 0 && <Row k="Follow-up bubbles"><ul className="space-y-0.5">{i.followups.map((f, n) => <li key={n} className="whitespace-pre-wrap">{f}</li>)}</ul></Row>}
      </dl>
      <Button size="sm" variant={troubled ? "primary" : "ghost"} className="mt-2" aria-expanded={why} onClick={() => setWhy(!why)}><HelpCircle className="h-3.5 w-3.5" aria-hidden />Why did this happen?</Button>
      {why && <ul className="mt-2 list-disc space-y-1 rounded-ctl bg-sunken py-2 pl-6 pr-3 text-sm">{i.why.map((w, n) => <li key={n}>{w}</li>)}</ul>}
    </div>
  );
}
