import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CalendarDays, Database, FileQuestion, FileText, Hash, HelpCircle, History, LayoutGrid, Plus, Search, SearchCheck, Tags, Variable, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Empty, Input, PageHeader, Spinner, cx } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import Knowledge from "./Knowledge";
import QAManager from "./QAManager";
import SheetView, { NewSheet, type Sheet } from "./SheetView";

type Item = { id: string; label: string; icon: typeof BookOpen; count?: number; warn?: boolean; hint: string };

/** Everything the assistant knows, in one place: a row of buttons for each kind of knowledge (and each data sheet)
 *  with its size, one search box that looks through all of it, and a start page that says where a new fact belongs. */
export default function KnowledgeHub() {
  const admin = useAuth().user?.role === "admin";
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") || (params.get("section") === "qa" ? "qa" : "home");
  const go = (v: string) => setParams({ view: v }, { replace: false });
  const [find, setFind] = useState("");
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [advOpen, setAdvOpen] = useState(false);
  useEffect(() => { const t = setTimeout(() => setQ(find.trim()), 350); return () => clearTimeout(t); }, [find]);
  const o = useQuery({ queryKey: ["knowledge-overview"], queryFn: () => api<any>("/api/knowledge/overview"), refetchInterval: 30000 });
  const d = o.data;
  const sheets: Sheet[] = d?.sheets ?? [];
  const groups: { title: string; items: Item[] }[] = [
    { title: "", items: [{ id: "home", label: "Start here", icon: LayoutGrid, hint: "What goes where" }] },
    { title: "What the assistant knows", items: [
      { id: "documents", label: "Documents", icon: FileText, count: d?.documents, warn: !!d?.documents_failed, hint: "Files it reads" },
      { id: "qa", label: "Q&A", icon: FileQuestion, count: d?.qa, hint: "Exact answers" },
      { id: "values", label: "Values", icon: Variable, count: d?.values, warn: !!d?.values_empty, hint: "Fees, phones, links used in answers" },
      { id: "programs", label: "Programs", icon: CalendarDays, count: d?.programs, hint: "Upcoming courses and events" },
    ] },
    { title: "Data sheets", items: sheets.map((s) => ({ id: `sheet:${s.id}`, label: s.name, icon: Database, count: s.rows, hint: s.description ?? "", warn: !s.active })) },
    { title: "Needs attention", items: [{ id: "unknown", label: "Unknown questions", icon: HelpCircle, count: d?.unknown, warn: !!d?.unknown, hint: "Asked, but not answered" }] },
    { title: "Advanced", items: [
      { id: "cache", label: "Saved AI answers", icon: History, count: d?.saved_answers, hint: "Answers reused for the same question" },
      { id: "search", label: "Search test", icon: SearchCheck, hint: "What the search finds for a question" },
      { id: "keywords", label: "Keywords", icon: Tags, hint: "The search index behind every answer" },
    ] },
  ];
  const sheet = view.startsWith("sheet:") ? sheets.find((s) => `sheet:${s.id}` === view) : undefined;

  return (
    <>
      <PageHeader title="Knowledge" subtitle="Everything the assistant answers from. Pick a kind below, or search everything at once." />
      <div className="relative mb-4">
        <Search className="pointer-events-none absolute left-3 top-3 h-5 w-5 text-muted" aria-hidden />
        <Input aria-label="Search everything the assistant knows" persistDraft={false} type="search" className="h-11 pl-10 text-base"
               placeholder="Search everything: a book, an office, a fee, a program…" value={find} onChange={(e) => setFind(e.target.value)} />
        {find && <button type="button" aria-label="Clear the search" className="absolute right-3 top-3 text-muted hover:text-ink" onClick={() => setFind("")}><X className="h-5 w-5" aria-hidden /></button>}
      </div>
      {q.length >= 2 ? <SearchResults q={q} onOpen={(v) => { setFind(""); go(v); }} /> : (
        <div className="space-y-4">
          <nav aria-label="Kinds of knowledge" className="space-y-2.5 rounded-panel border border-line bg-surface p-3">
            {groups.map((g) => {
              const advanced = g.title === "Advanced";
              const shown = !advanced || advOpen || g.items.some((it) => it.id === view);
              return (g.items.length || g.title === "Data sheets") ? (
                <div key={g.title || "home"} className="flex flex-wrap items-center gap-1.5">
                  <span className="mr-1 w-full text-[11px] font-bold uppercase tracking-wide text-muted sm:w-48 sm:shrink-0">{g.title}</span>
                  {shown && g.items.map((it) => (
                    <button key={it.id} type="button" onClick={() => go(it.id)} aria-current={view === it.id} title={it.hint}
                      className={cx("inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors", view === it.id ? "border-accent bg-accent-soft font-semibold text-accent-strong" : "border-line bg-surface hover:bg-sunken")}>
                      <it.icon className="h-4 w-4 shrink-0 text-muted" aria-hidden /><span className="max-w-[16rem] truncate">{it.label}</span>
                      {it.count != null && <span className={cx("text-xs tabular-nums", it.warn ? "font-bold text-warn" : "text-muted")}>{it.count}</span>}
                    </button>
                  ))}
                  {advanced && !shown && <button type="button" onClick={() => setAdvOpen(true)} className="rounded-full border border-dashed border-line px-3 py-1.5 text-sm text-muted hover:bg-sunken">Show {g.items.length} more tools</button>}
                  {g.title === "Data sheets" && admin && <button type="button" onClick={() => setCreating(true)} className="inline-flex items-center gap-1.5 rounded-full border border-dashed border-accent/50 px-3 py-1.5 text-sm text-accent-strong hover:bg-sunken"><Plus className="h-4 w-4" aria-hidden />New sheet</button>}
                  {g.title === "Data sheets" && !sheets.length && !admin && <span className="text-xs text-muted">None yet.</span>}
                </div>
              ) : null;
            })}
          </nav>
          <section aria-label="Selected knowledge" className="min-w-0">
            {o.isLoading ? <Spinner /> :
              view === "home" ? <Home d={d} sheets={sheets} admin={admin} go={go} onNewSheet={() => setCreating(true)} /> :
              view === "documents" ? <Knowledge view="documents" /> :
              view === "cache" ? <Knowledge view="cache" /> :
              view === "search" ? <Knowledge view="search" /> :
              view === "qa" ? <QAManager view="fixed" /> :
              view === "values" ? <QAManager view="variables" /> :
              view === "programs" ? <QAManager view="programs" /> :
              view === "unknown" ? <QAManager view="unknown" /> :
              view === "keywords" ? <QAManager view="keywords" /> :
              sheet ? <SheetView key={sheet.id} sheet={sheet} onDeleted={() => go("home")} /> :
              <Empty title="Not found">This sheet no longer exists.</Empty>}
          </section>
        </div>
      )}
      {creating && <NewSheet onClose={() => setCreating(false)} onCreated={(s) => { setCreating(false); qc.invalidateQueries({ queryKey: ["knowledge-overview"] }); go(`sheet:${s.id}`); }} />}
    </>
  );
}

function Card({ icon: Icon, title, count, children, onOpen, action }: { icon: typeof BookOpen; title: string; count?: ReactNode; children: ReactNode; onOpen: () => void; action?: ReactNode }) {
  return (
    <div className="flex flex-col rounded-panel border border-line bg-surface p-4">
      <button type="button" onClick={onOpen} className="flex items-start gap-3 text-left">
        <span className="rounded-ctl bg-accent-soft p-2 text-accent-strong"><Icon className="h-5 w-5" aria-hidden /></span>
        <span className="min-w-0 flex-1"><span className="flex items-baseline justify-between gap-2"><span className="font-bold">{title}</span>{count != null && <span className="text-xl font-bold tabular-nums">{count}</span>}</span>
          <span className="mt-1 block text-sm text-muted">{children}</span></span>
      </button>
      {action && <div className="mt-3 flex justify-end">{action}</div>}
    </div>
  );
}

function Home({ d, sheets, admin, go, onNewSheet }: { d: any; sheets: Sheet[]; admin: boolean; go: (v: string) => void; onNewSheet: () => void }) {
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <Card icon={FileText} title="Documents" count={d?.documents} onOpen={() => go("documents")}>Long texts the assistant reads: FAQs, course guides, office lists.{d?.documents_failed ? ` ${d.documents_failed} failed to process.` : ""}</Card>
        <Card icon={FileQuestion} title="Q&A" count={d?.qa} onOpen={() => go("qa")}>A question and the exact answer to give. Used word for word.</Card>
        <Card icon={Variable} title="Values" count={d?.values} onOpen={() => go("values")}>Fees, phone numbers, links. Change one here and every answer using it changes.{d?.values_empty ? ` ${d.values_empty} still empty.` : ""}</Card>
        <Card icon={CalendarDays} title="Programs" count={d?.programs} onOpen={() => go("programs")}>Upcoming courses and events with dates, places and registration links.</Card>
        <Card icon={Database} title="Data sheets" count={sheets.length} onOpen={() => (sheets[0] ? go(`sheet:${sheets[0].id}`) : admin && onNewSheet())}
          action={admin && <Button size="sm" onClick={onNewSheet}><Plus className="h-4 w-4" aria-hidden />New sheet</Button>}>
          Tables of many similar things: books, branches, products. The AI searches them.</Card>
        <Card icon={HelpCircle} title="Unknown questions" count={d?.unknown} onOpen={() => go("unknown")}>Questions guests asked that nothing here answered. Add the answer to close the gap.</Card>
      </div>
      <div className="rounded-panel border border-line bg-surface p-4">
        <h2 className="mb-2 flex items-center gap-2 font-bold"><Hash className="h-4 w-4 text-muted" aria-hidden />Where should a new fact go?</h2>
        <table className="w-full text-sm"><tbody className="divide-y divide-line">
          {[["One question with one fixed answer (\"How do I register?\")", "Q&A", "qa"],
            ["A number or link used in many answers (course fee, hotline)", "Values", "values"],
            ["A course or event with a date and place", "Programs", "programs"],
            ["Many similar items with the same details (books: name, author, price)", "Data sheet", sheets[0] ? `sheet:${sheets[0].id}` : ""],
            ["A long text with many details (a guide, a brochure, a policy)", "Documents", "documents"]].map(([what, where, v]) => (
            <tr key={where}><td className="py-2 pr-3">{what}</td><td className="py-2 text-right">{v ? <button type="button" className="font-semibold text-accent-strong underline" onClick={() => go(v)}>{where}</button> : <span className="font-semibold">{where}</span>}</td></tr>
          ))}
        </tbody></table>
      </div>
    </div>
  );
}

function SearchResults({ q, onOpen }: { q: string; onOpen: (view: string) => void }) {
  const r = useQuery({ queryKey: ["knowledge-search", q], queryFn: () => api<any>("/api/knowledge/search", { params: { q } }) });
  if (r.isLoading) return <Spinner label="Searching" />;
  const x = r.data ?? {};
  const total = ["documents", "qa", "values", "programs", "sheets"].reduce((n, k) => n + (x[k]?.length ?? 0), 0);
  if (!total) return <Empty title="Nothing found">No document, Q&A, value, program or sheet holds “{q}”. Try fewer or other words.</Empty>;
  const Group = ({ title, icon: Icon, items, render }: { title: string; icon: typeof BookOpen; items?: any[]; render: (i: any) => ReactNode }) =>
    !items?.length ? null : (
      <section className="rounded-panel border border-line bg-surface">
        <h2 className="flex items-center gap-2 border-b border-line px-4 py-2 font-bold"><Icon className="h-4 w-4 text-muted" aria-hidden />{title}<Badge>{items.length}</Badge></h2>
        <ul className="divide-y divide-line">{items.map((i, n) => <li key={n}>{render(i)}</li>)}</ul>
      </section>
    );
  const Row = ({ onClick, title, sub }: { onClick: () => void; title: ReactNode; sub?: ReactNode }) =>
    <button type="button" onClick={onClick} className="block w-full px-4 py-2.5 text-left hover:bg-sunken"><span className="block font-semibold">{title}</span>{sub && <span className="block text-sm text-muted">{sub}</span>}</button>;
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">{total} result(s) for “{q}”. Click one to open where it is kept.</p>
      <Group title="Data sheets" icon={Database} items={x.sheets} render={(i) => <Row onClick={() => onOpen(`sheet:${i.sheet_id}`)} title={i.sheet} sub={i.snippet} />} />
      <Group title="Q&A" icon={FileQuestion} items={x.qa} render={(i) => <Row onClick={() => onOpen("qa")} title={<>{i.question}{!i.active && <Badge className="ml-2">off</Badge>}</>} sub={i.snippet} />} />
      <Group title="Values" icon={Variable} items={x.values} render={(i) => <Row onClick={() => onOpen("values")} title={i.key} sub={`${i.value || "(empty)"}${i.description ? ` · ${i.description}` : ""}`} />} />
      <Group title="Programs" icon={CalendarDays} items={x.programs} render={(i) => <Row onClick={() => onOpen("programs")} title={i.name} sub={[i.start_date, i.place].filter(Boolean).join(" · ")} />} />
      <Group title="Documents" icon={FileText} items={x.documents} render={(i) => <Row onClick={() => onOpen("documents")} title={`${i.document}${i.title ? ` — ${i.title}` : ""}`} sub={i.snippet} />} />
    </div>
  );
}
