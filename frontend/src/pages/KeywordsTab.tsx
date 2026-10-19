import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Plus, RefreshCw, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Empty, ErrorNote, Input, Panel, Select, Spinner, Tabs, useConfirm, useToast } from "../components/ui";
import { api } from "../lib/api";

type Kw = { term: string; stem: string; weight: number; origin: string; shared: number };
type Item = { type: string; ref: string; title: string; subtitle: string; keywords: Kw[]; unique: number; ai_at: string | null };
type Node = { term: string; stem: string; count: number; more: number; children: Node[]; records: { type: string; ref: string; title: string }[] };

const ORIGIN: Record<string, { tone: "neutral" | "ok" | "warn" | "danger" | "accent"; label: string }> = {
  title: { tone: "neutral", label: "title" }, file: { tone: "neutral", label: "file" }, doc: { tone: "neutral", label: "document" },
  body: { tone: "neutral", label: "text" }, signature: { tone: "accent", label: "rare word" }, phrase: { tone: "neutral", label: "phrase" },
  ai: { tone: "ok", label: "AI" }, manual: { tone: "warn", label: "by hand" }, unique: { tone: "accent", label: "unique" },
};

export default function KeywordsTab({ isAdmin }: { isAdmin: boolean }) {
  const [view, setView] = useState<"table" | "tree">("table");
  return (
    <div>
      <p className="mb-3 text-sm text-muted">Every record's keywords. They are created automatically from the text (longer text gets more, and more specific, keywords). A keyword shared by many records shows its count; records that look alike get unique keywords so the assistant can tell them apart. Keywords the AI adds are checked against the record's own text, and anything not in it is dropped.</p>
      <Tabs tabs={[{ id: "table", label: "Table" }, { id: "tree", label: "Tree" }]} value={view} onChange={setView} />
      {view === "table" ? <Table isAdmin={isAdmin} /> : <Tree />}
    </div>
  );
}

function Chip({ k, onRemove }: { k: Kw; onRemove?: () => void }) {
  const o = ORIGIN[k.origin] ?? ORIGIN.body;
  return (
    <Badge tone={o.tone} className={k.shared === 1 ? "ring-1 ring-ok" : ""}>
      <span title={`${o.label} · weight ${k.weight} · ${k.shared === 1 ? "only this record" : `${k.shared} records`}`}>{k.term}{k.shared > 1 && <span className="opacity-60"> ×{k.shared}</span>}</span>
      {onRemove && <button type="button" aria-label={`Remove ${k.term}`} className="opacity-60 hover:opacity-100" onClick={onRemove}><X className="h-3 w-3" aria-hidden /></button>}
    </Badge>
  );
}

function Table({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [type, setType] = useState("qa");
  const [search, setSearch] = useState("");
  const q = useQuery({ queryKey: ["kw-records", type, search], queryFn: () => api<{ total: number; items: Item[] }>("/api/keywords/records", { params: { type, search, limit: 60 } }) });
  const status = useQuery({ queryKey: ["kw-ai-status"], queryFn: () => api<any>("/api/keywords/generate-ai/status"), refetchInterval: (s) => (["queued", "running"].includes(s.state.data?.status) ? 2000 : false) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["kw-records"] }); qc.invalidateQueries({ queryKey: ["kw-tree"] }); qc.invalidateQueries({ queryKey: ["kw-stats"] }); };
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "Failed", "danger");
  const edit = useMutation({ mutationFn: (v: { it: Item; add?: string[]; remove?: string[] }) => api(`/api/keywords/records/${v.it.type}/${encodeURIComponent(v.it.ref)}`, { method: "PUT", body: { add: v.add ?? [], remove: v.remove ?? [] } }), onSuccess: refresh, onError: fail });
  const gen = useMutation({
    mutationFn: (v: { it: Item; ai: boolean }) => api<any>(`/api/keywords/records/${v.it.type}/${encodeURIComponent(v.it.ref)}/generate`, { body: { ai: v.ai } }),
    onSuccess: (r) => { refresh(); toast(r.ai ? `AI kept ${r.ai.kept.length}, dropped ${r.ai.rejected.length} not found in the record` : `Regenerated${r.unique?.length ? `; unique: ${r.unique.join(", ")}` : ""}`); },
    onError: fail,
  });
  const rebuild = useMutation({ mutationFn: () => api("/api/keywords/rebuild", { method: "POST" }), onSuccess: () => { refresh(); toast("All keywords regenerated"); }, onError: fail });
  const bulk = useMutation({ mutationFn: () => api("/api/keywords/generate-ai", { body: { types: [type], limit: 50 } }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["kw-ai-status"] }); toast("Started in the background"); }, onError: fail });
  const st = status.data;
  useEffect(() => { if (st?.status === "done") refresh(); }, [st?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Record type" value={type} onChange={(e) => setType(e.target.value)} className="w-40"><option value="qa">Q&A</option><option value="chunk">Knowledge chunks</option><option value="variable">Variables</option><option value="program">Programs</option></Select>
        <Input aria-label="Search records" placeholder="Search records…" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-xs" />
        {isAdmin && <>
          <Button size="sm" loading={rebuild.isPending} onClick={() => rebuild.mutate()} title="Free: regenerates every record's keywords from its text and re-runs the uniqueness check"><RefreshCw className="h-3.5 w-3.5" aria-hidden />Generate for all</Button>
          <Button size="sm" loading={bulk.isPending} onClick={async () => (await ask({ title: "Generate keywords with the AI?", message: "Up to 50 records without AI keywords get one small AI call each (billed). Needs the “Keyword generation” route in AI Settings.", confirmLabel: "Generate" })) && bulk.mutate()}><Sparkles className="h-3.5 w-3.5" aria-hidden />Generate with AI</Button>
        </>}
      </div>
      {st && st.status !== "idle" && <p className="text-sm text-muted" role="status">AI keywords: {st.status}{st.total ? ` — ${st.done}/${st.total}` : ""}{st.kept !== undefined && ` · kept ${st.kept}, dropped ${st.rejected} not in the record`}{st.error && <span className="text-danger"> · {st.error}</span>}</p>}
      <ErrorNote error={q.error} />
      {q.isLoading ? <Spinner /> : !q.data?.items.length ? <Empty title="No records">Nothing to show here yet.</Empty> : (
        <div className="space-y-2">
          {q.data.items.map((it) => <Row key={`${it.type}:${it.ref}`} it={it} isAdmin={isAdmin} busy={gen.isPending || edit.isPending}
            onAdd={(w) => edit.mutate({ it, add: [w] })} onRemove={(stem) => edit.mutate({ it, remove: [stem] })} onGen={(ai) => gen.mutate({ it, ai })} />)}
          {q.data.total > q.data.items.length && <p className="text-xs text-muted">Showing {q.data.items.length} of {q.data.total}. Use search to narrow.</p>}
        </div>
      )}
    </div>
  );
}

function Row({ it, isAdmin, busy, onAdd, onRemove, onGen }: { it: Item; isAdmin: boolean; busy: boolean; onAdd: (w: string) => void; onRemove: (stem: string) => void; onGen: (ai: boolean) => void }) {
  const [w, setW] = useState("");
  const shown = it.keywords.filter((k) => k.origin !== "phrase");
  return (
    <div className="rounded-panel border border-line bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0"><p className="truncate font-medium">{it.title}</p><p className="truncate text-xs text-muted">{it.subtitle}</p></div>
        <div className="flex items-center gap-2"><Badge tone={it.unique ? "ok" : "warn"}>{it.unique} unique</Badge>{it.ai_at && <Badge tone="ok">AI</Badge>}
          {isAdmin && <><Button size="sm" disabled={busy} onClick={() => onGen(false)} title="Regenerate from the text and check uniqueness"><RefreshCw className="h-3.5 w-3.5" aria-hidden />Regenerate</Button><Button size="sm" disabled={busy} onClick={() => onGen(true)} title="Ask the AI (billed); only words found in the record are kept"><Sparkles className="h-3.5 w-3.5" aria-hidden />AI</Button></>}</div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">{shown.map((k) => <Chip key={k.stem} k={k} onRemove={isAdmin ? () => onRemove(k.stem) : undefined} />)}{!shown.length && <span className="text-xs text-muted">No keywords yet</span>}</div>
      {isAdmin && <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (w.trim()) { onAdd(w.trim()); setW(""); } }}><Input aria-label={`Add a keyword to ${it.title}`} placeholder="Add keyword" value={w} onChange={(e) => setW(e.target.value)} className="max-w-[200px]" /><Button size="sm" type="submit"><Plus className="h-3.5 w-3.5" aria-hidden />Add</Button></form>}
    </div>
  );
}

function Tree() {
  const [search, setSearch] = useState("");
  const q = useQuery({ queryKey: ["kw-tree"], queryFn: () => api<{ records: number; keywords: number; roots: Node[] }>("/api/keywords/tree") });
  const roots = (q.data?.roots ?? []).filter((n) => !search.trim() || JSON.stringify(n).toLowerCase().includes(search.trim().toLowerCase()));
  return (
    <Panel title="Keyword tree" actions={q.data && <span className="text-xs text-muted">{q.data.keywords} keywords · {q.data.records} records</span>}>
      <p className="mb-3 text-sm text-muted">The most shared keywords first. Open one to see the keywords its records also have, narrowing down to the records themselves.</p>
      <Input aria-label="Filter the tree" placeholder="Filter by keyword or record…" value={search} onChange={(e) => setSearch(e.target.value)} className="mb-3 max-w-xs" />
      <ErrorNote error={q.error} />
      {q.isLoading ? <Spinner /> : !roots.length ? <Empty title="Nothing to show">No keyword is shared by two or more records yet.</Empty> : <ul role="tree" className="space-y-0.5">{roots.map((n) => <TreeNode key={n.stem} n={n} open={!!search.trim()} />)}</ul>}
    </Panel>
  );
}

function TreeNode({ n, open: o }: { n: Node; open?: boolean }) {
  const [open, setOpen] = useState(!!o);
  useEffect(() => setOpen(!!o), [o]);
  const has = n.children.length > 0 || n.records.length > 0;
  return (
    <li role="treeitem" aria-expanded={has ? open : undefined}>
      <button type="button" className="flex items-center gap-1 rounded px-1 py-0.5 text-left text-sm hover:bg-sunken" onClick={() => has && setOpen(!open)}>
        {has ? (open ? <ChevronDown className="h-4 w-4" aria-hidden /> : <ChevronRight className="h-4 w-4" aria-hidden />) : <span className="w-4" />}
        <span className="font-medium">{n.term}</span><span className="text-xs text-muted">{n.count}</span>
      </button>
      {open && <ul role="group" className="ml-4 border-l border-line pl-2">
        {n.children.map((c) => <TreeNode key={c.stem} n={c} open={o} />)}
        {n.records.map((r) => <li key={`${r.type}:${r.ref}`} className="flex items-center gap-1.5 px-1 py-0.5 text-sm"><Badge>{r.type}</Badge><span className="truncate">{r.title}</span></li>)}
        {n.more > 0 && <li className="px-1 text-xs text-muted">+{n.more} more</li>}
      </ul>}
    </li>
  );
}
