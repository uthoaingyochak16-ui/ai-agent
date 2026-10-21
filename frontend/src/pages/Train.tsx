import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { FindReason } from "../components/Diagnosis";
import { Badge, Empty, PageHeader, Panel, Spinner, Tabs, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { ago } from "../lib/format";
import Evaluation from "./Evaluation";
import TeachTab from "./TeachTab";
import TestChat from "./TestChat";

type Tab = "teach" | "chat" | "failed" | "tests";
const SUBTITLE: Record<Tab, string> = {
  chat: "Talk to the assistant exactly as a customer would. Nothing is sent to Meta. Select a reply to see how it was made; if one is wrong, find the reason and fix it right there.",
  teach: "Tell the assistant in your own words how to behave, or ask why it failed on a question. It turns that into rules; nothing changes until you save one.",
  failed: "Questions the assistant could not answer, or a reply you think is wrong: find out why, then fix it with a rule or a saved answer.",
  tests: "A fixed set of questions with the answers they must get. Run it after teaching to check the change worked and nothing else broke.",
};
const REASON: Record<string, string> = { no_context: "nothing found", output_blocked: "blocked by safety filter", wrong_answer: "marked wrong", needs_review: "needs review", payment_disabled: "payment (person)" };

/** One place to improve the assistant: teach it (rules from plain words), find why it failed, and test that it works.
 *  (Was: Test chat -> "Teach the assistant", and the separate Evaluation page.) */
export default function Train() {
  const [params, setParams] = useSearchParams();
  const loc = useLocation();
  const fromState = (loc.state as { question?: string } | null)?.question;
  const tab = (params.get("tab") as Tab) || (fromState ? "tests" : "chat");
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });
  return (
    <>
      <PageHeader title="Train the assistant" subtitle={SUBTITLE[tab]} />
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[{ id: "chat", label: "Test chat" }, { id: "teach", label: "Teach" }, { id: "failed", label: "Why it failed" }, { id: "tests", label: "Test set" }]} />
      {/* all stay mounted, so switching tabs keeps the teaching conversation */}
      <div hidden={tab !== "chat"}><TestChat /></div>
      <div hidden={tab !== "teach"}><TeachTab /></div>
      <div hidden={tab !== "failed"}><WhyItFailed /></div>
      <div hidden={tab !== "tests"}>{tab === "tests" && <Evaluation />}</div>
    </>
  );
}

function WhyItFailed() {
  const [own, setOwn] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const q = useQuery({ queryKey: ["unanswered", "train"], queryFn: () => api<any>("/api/unanswered", { params: { limit: 50 } }) });
  const items: any[] = Array.isArray(q.data) ? q.data : q.data?.items ?? [];
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Panel title="Check any guest question">
        <p className="mb-2 text-sm text-muted">Write a question the way a guest would. It runs through the real reply path (nothing is saved or sent), and the reason is explained in plain words.</p>
        <Textarea persistDraft={false} rows={2} aria-label="Guest question" placeholder="e.g. Sylhet e apnader office ache?" value={own} onChange={(e) => setOwn(e.target.value)} />
        {own.trim() && <div className="mt-3"><FindReason key={own} question={own.trim()} /></div>}
      </Panel>
      <Panel title="Questions it could not answer" actions={<Badge>{items.length}</Badge>}>
        {q.isLoading ? <Spinner /> : !items.length ? <Empty title="Nothing waiting">Questions the assistant could not answer show up here.</Empty> : (
          <ul className="space-y-2">{items.map((u) => (
            <li key={u.id} className="rounded-ctl border border-line p-2.5">
              <button type="button" className="w-full text-left" aria-expanded={open === u.id} onClick={() => setOpen(open === u.id ? null : u.id)}>
                <p className="font-semibold">{u.question}</p>
                <p className="mt-0.5 text-xs text-muted">{REASON[u.reason] ?? u.reason} · asked {u.occurrences ?? 1}× · {ago(u.last_seen_at ?? u.created_at)}</p>
              </button>
              {open === u.id && <div className="mt-3"><FindReason question={u.question} unansweredId={u.id} compact /></div>}
            </li>
          ))}</ul>
        )}
      </Panel>
    </div>
  );
}
