import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { Badge, Button, Empty, ErrorNote, Field, IconButton, Input, Modal, PageHeader, Panel, Select, Spinner, Switch, TableWrap, Tabs, Td, Textarea, Th, useConfirm, useToast } from "../components/ui";
import { api, download } from "../lib/api";

type QA = { id?: number; question: string; answer_template: string; category: string | null; active: boolean; resolve_unanswered_id?: number };

export default function QAManager() {
  const loc = useLocation();
  const pre = loc.state as { question?: string; unansweredId?: number } | null;
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [tab, setTab] = useState<"known" | "unknown" | "variables">("unknown");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [editing, setEditing] = useState<QA | null>(pre?.question ? { question: pre.question, answer_template: "", category: null, active: true, resolve_unanswered_id: pre.unansweredId } : null);
  const imp = useRef<HTMLInputElement>(null);
  const list = useQuery({ queryKey: ["qa", search, category], queryFn: () => api("/api/qa", { params: { search, category } }) });
  const unanswered = useQuery({ queryKey: ["unanswered"], queryFn: () => api<any[]>("/api/unanswered") });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/qa/${id}`, { method: "DELETE" }), onSuccess: () => { toast("Deleted"); qc.invalidateQueries({ queryKey: ["qa"] }); } });
  const importCsv = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); return api<{ created: number; updated: number }>("/api/qa-import", { form: fd }); },
    onSuccess: (r) => { toast(`Imported: ${r.created} new, ${r.updated} updated`); qc.invalidateQueries({ queryKey: ["qa"] }); if (imp.current) imp.current.value = ""; },
    onError: (e) => toast(e instanceof Error ? e.message : "Import failed", "danger"),
  });
  return (
    <>
      <PageHeader title="Q&A manager" subtitle="Exact or near-exact questions get these answers instantly, with no AI call and no cost. Use {variable} in answers; values come from the Variables panel."
        actions={<>
          <input ref={imp} type="file" accept=".csv,.tsv,.json,.jsonl,.xlsx,.xlsm" className="sr-only" aria-label="Import a Q&A file" onChange={(e) => e.target.files?.[0] && importCsv.mutate(e.target.files[0])} />
          <Button size="sm" onClick={() => imp.current?.click()} loading={importCsv.isPending} title="CSV, Excel or JSON with question and answer columns"><Upload className="h-3.5 w-3.5" aria-hidden />Import file</Button>
          <Button size="sm" onClick={() => download("/api/qa-export", "qa_pairs.csv").catch(() => toast("Export failed", "danger"))}><Download className="h-3.5 w-3.5" aria-hidden />Export CSV</Button>
          <Button variant="primary" onClick={() => setEditing({ question: "", answer_template: "", category: null, active: true })}><Plus className="h-4 w-4" aria-hidden />Add Q&A</Button></>} />
      <Tabs tabs={[{ id: "unknown", label: `Unknown questions${unanswered.data ? ` (${unanswered.data.length})` : ""}` }, { id: "known", label: `Known Q&A${list.data ? ` (${list.data.total})` : ""}` }, { id: "variables", label: "Variables" }]} value={tab} onChange={setTab} />
      {tab === "unknown" && <div>
          {!!unanswered.data?.length ? <Panel title="Unknown questions">
            <p className="mb-3 text-sm text-muted">Questions the assistant could not answer. Add an answer to make matching future questions reply instantly.</p>
            <ul className="divide-y divide-line">{unanswered.data.map((u) => <li key={u.id} className="flex items-center justify-between gap-3 py-3">
              <span className="min-w-0"><span className="block break-words font-medium">{u.question}</span><span className="text-xs text-muted">{u.channel || "unknown channel"}</span></span>
              <Button size="sm" onClick={() => setEditing({ question: u.question, answer_template: "", category: null, active: true, resolve_unanswered_id: u.id })}>Add answer</Button>
            </li>)}</ul>
          </Panel> : unanswered.isLoading ? <Spinner /> : <Empty title="No unknown questions">Questions the assistant cannot answer will appear here.</Empty>}
        </div>}
      {tab === "known" && <div>
          <div className="mb-3 flex flex-wrap gap-2">
            <Input aria-label="Search questions and answers" placeholder="Search questions and answers" className="min-w-[200px] flex-1" value={search} onChange={(e) => setSearch(e.target.value)} />
            <Select aria-label="Category" className="w-auto" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>{list.data?.categories.map((c: string) => <option key={c}>{c}</option>)}</Select>
          </div>
          {list.isLoading ? <Spinner /> : !list.data?.items.length ? <Empty title="No Q&A pairs">Add common questions such as opening hours and prices, or import a CSV, Excel or JSON file with "question" and "answer" columns (optional: category or topic, active).</Empty> : (
            <TableWrap><thead><tr><Th>Question</Th><Th>Answer</Th><Th>Category</Th><Th><span className="sr-only">Actions</span></Th></tr></thead>
              <tbody>{list.data.items.map((q: any) => (
                <tr key={q.id} className={q.active ? "" : "opacity-60"}><Td className="max-w-[260px] font-semibold">{q.question}{!q.active && <Badge className="ml-2">Off</Badge>}</Td><Td className="max-w-[360px] whitespace-pre-wrap break-words text-muted">{q.answer_template}</Td><Td>{q.category && <Badge tone="blue">{q.category}</Badge>}</Td>
                  <Td><div className="flex justify-end"><IconButton label={`Edit "${q.question}"`} onClick={() => setEditing(q)}><Pencil className="h-4 w-4" aria-hidden /></IconButton><IconButton label={`Delete "${q.question}"`} onClick={async () => (await ask({ title: "Delete this Q&A pair?", message: q.question, confirmLabel: "Delete", danger: true })) && del.mutate(q.id)}><Trash2 className="h-4 w-4" aria-hidden /></IconButton></div></Td></tr>
              ))}</tbody></TableWrap>
          )}
          {list.data && <p className="mt-2 text-sm text-muted">{list.data.total} pair(s)</p>}
        </div>}
      {tab === "variables" && <Variables />}
      {editing && <QAEditor qa={editing} categories={list.data?.categories ?? []} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["qa"] }); qc.invalidateQueries({ queryKey: ["unanswered"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); toast("Saved"); }} />}
    </>
  );
}

function QAEditor({ qa, categories, onClose, onSaved }: { qa: QA; categories: string[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState(qa);
  const vars = useQuery({ queryKey: ["vars"], queryFn: () => api<{ key: string }[]>("/api/variables") });
  const save = useMutation({ mutationFn: () => (qa.id ? api(`/api/qa/${qa.id}`, { method: "PUT", body: f }) : api("/api/qa", { body: f })), onSuccess: onSaved });
  return (
    <Modal wide title={qa.id ? "Edit Q&A" : "Add Q&A"} onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Question" hint="Write it the way customers ask it. Add a few variants as separate pairs.">{(i) => <Input id={i} required value={f.question} onChange={(e) => setF({ ...f, question: e.target.value })} />}</Field>
        <Field label="Answer" hint={vars.data?.length ? `Available variables: ${vars.data.map((v) => `{${v.key}}`).join(" ")}` : "Use {variable} for values you manage in the Variables panel."}>{(i) => <Textarea id={i} required rows={5} value={f.answer_template} onChange={(e) => setF({ ...f, answer_template: e.target.value })} />}</Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Category">{(i) => <Input id={i} list="cats" value={f.category ?? ""} onChange={(e) => setF({ ...f, category: e.target.value })} />}</Field><datalist id="cats">{categories.map((c) => <option key={c} value={c} />)}</datalist>
          <div className="self-end pb-2"><Switch checked={f.active} onChange={(v) => setF({ ...f, active: v })} label="Active" /></div>
        </div>
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2"><Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={save.isPending}>Save</Button></div>
      </form>
    </Modal>
  );
}

type Var = { key: string; value: string; description: string | null };

function Variables() {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const vars = useQuery({ queryKey: ["vars"], queryFn: () => api<Var[]>("/api/variables") });
  const [draft, setDraft] = useState<Record<string, { value: string; description: string }>>({});
  const [adding, setAdding] = useState(false);
  const [find, setFind] = useState("");
  useEffect(() => { if (vars.data) setDraft(Object.fromEntries(vars.data.map((v) => [v.key, { value: v.value, description: v.description ?? "" }]))); }, [vars.data]);
  const changed = (vars.data ?? []).filter((v) => draft[v.key] && (draft[v.key].value !== v.value || draft[v.key].description !== (v.description ?? "")));
  const saveAll = useMutation({
    mutationFn: async () => { for (const v of changed) await api(`/api/variables/${v.key}`, { method: "PUT", body: { value: draft[v.key].value, description: draft[v.key].description } }); return changed.length; },
    onSuccess: (n) => { toast(`${n} variable(s) saved`); qc.invalidateQueries({ queryKey: ["vars"] }); },
  });
  const del = useMutation({ mutationFn: (k: string) => api(`/api/variables/${k}`, { method: "DELETE" }), onSuccess: () => { toast("Variable deleted"); qc.invalidateQueries({ queryKey: ["vars"] }); } });
  const f = find.trim().toLowerCase();
  const rows = (vars.data ?? []).filter((v) => !f || v.key.toLowerCase().includes(f) || v.value.toLowerCase().includes(f) || (v.description ?? "").toLowerCase().includes(f));
  const set = (k: string, patch: Partial<{ value: string; description: string }>) => setDraft({ ...draft, [k]: { ...draft[k], ...patch } });
  const cell = "w-full rounded-none border-0 bg-transparent px-2 py-1.5 text-sm focus:bg-surface focus:outline focus:outline-2 focus:outline-accent";
  return (
    <Panel title={`Variables${vars.data ? ` (${vars.data.length})` : ""}`} actions={<div className="flex gap-2">
      {changed.length > 0 && <Button size="sm" variant="primary" loading={saveAll.isPending} onClick={() => saveAll.mutate()}>Save {changed.length} change(s)</Button>}
      <Button size="sm" onClick={() => setAdding(true)}><Plus className="h-4 w-4" aria-hidden />New variable</Button></div>}>
      <p className="mb-3 text-sm text-muted">Edit any cell like a spreadsheet, then save. The assistant answers questions about these values directly (e.g. “course fee koto?” → Course_fee), and every Q&A answer that uses {"{name}"} updates too.</p>
      {vars.isLoading ? <Spinner /> : !vars.data?.length ? <Empty title="No variables yet">Click “New variable” to add one, for example Course_fee = ১১,৫০০ টাকা.</Empty> : <>
        <Input aria-label="Search variables" persistDraft={false} placeholder="Search name, value or description" className="mb-3" value={find} onChange={(e) => setFind(e.target.value)} />
        <div className="max-h-[65vh] overflow-auto rounded-ctl border border-line">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-sunken"><tr>
              <th className="w-10 border-b border-r border-line px-2 py-2 text-right text-xs font-semibold text-muted">#</th>
              <th className="w-[22%] border-b border-r border-line px-2 py-2 text-left text-xs font-semibold text-muted">Name</th>
              <th className="w-[28%] border-b border-r border-line px-2 py-2 text-left text-xs font-semibold text-muted">Value</th>
              <th className="border-b border-r border-line px-2 py-2 text-left text-xs font-semibold text-muted">What it is (how guests ask)</th>
              <th className="w-10 border-b border-line"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{rows.map((v, n) => {
              const d = draft[v.key] ?? { value: v.value, description: v.description ?? "" };
              const dirty = d.value !== v.value || d.description !== (v.description ?? "");
              return (
                <tr key={v.key} className={dirty ? "bg-warn-soft/60" : "odd:bg-surface even:bg-sunken/40"}>
                  <td className="border-b border-r border-line px-2 text-right text-xs tabular-nums text-muted">{n + 1}</td>
                  <td className="border-b border-r border-line px-2 py-1.5"><code className="break-all font-semibold">{v.key}</code></td>
                  <td className="border-b border-r border-line p-0"><input aria-label={`Value of ${v.key}`} className={cell} value={d.value} onChange={(e) => set(v.key, { value: e.target.value })} /></td>
                  <td className="border-b border-r border-line p-0"><input aria-label={`Description of ${v.key}`} className={cell} placeholder="e.g. কোয়ান্টাম মেথড কোর্সের ফি" value={d.description} onChange={(e) => set(v.key, { description: e.target.value })} /></td>
                  <td className="border-b border-line text-center"><IconButton label={`Delete ${v.key}`} className="h-7 w-7" onClick={async () => (await ask({ title: `Delete {${v.key}}?`, message: "Q&A answers that use it stop being sent until it is added again.", confirmLabel: "Delete", danger: true })) && del.mutate(v.key)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></IconButton></td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
        {!rows.length && <p className="mt-2 text-sm text-muted">Nothing matches “{find}”.</p>}
        <ErrorNote error={saveAll.error} />
      </>}
      {adding && <NewVariable existing={vars.data?.map((v) => v.key) ?? []} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); qc.invalidateQueries({ queryKey: ["vars"] }); toast("Variable added"); }} />}
    </Panel>
  );
}

function NewVariable({ existing, onClose, onSaved }: { existing: string[]; onClose: () => void; onSaved: () => void }) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [description, setDescription] = useState("");
  const clean = key.trim().replace(/\s+/g, "_");
  const valid = /^[A-Za-z0-9_]{1,64}$/.test(clean);
  const dup = existing.includes(clean);
  const save = useMutation({ mutationFn: () => api(`/api/variables/${clean}`, { method: "PUT", body: { value, description } }), onSuccess: onSaved });
  return (
    <Modal title="New variable" onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid && !dup) save.mutate(); }}>
        <Field label="Name" hint={clean && !valid ? "Use letters, digits and underscores only." : dup ? "A variable with this name already exists; edit it in the list." : `English words joined by _ (e.g. Course_fee, Yoga_course_fee, Office_phone). Use it in answers as {${clean || "name"}}`}>{(i) => <Input id={i} persistDraft={false} autoFocus required placeholder="course_fee" value={key} onChange={(e) => setKey(e.target.value)} />}</Field>
        <Field label="Value">{(i) => <Textarea id={i} persistDraft={false} required rows={3} placeholder="11500" value={value} onChange={(e) => setValue(e.target.value)} />}</Field>
        <Field label="What it is (how guests ask)" hint="In the guest's words, Bangla or English. The assistant answers “course fee koto?” with this value when the name or this text matches. The reply reads: “<this text>: <value>”.">{(i) => <Input id={i} persistDraft={false} placeholder="কোয়ান্টাম মেথড কোর্সের ফি" value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={save.isPending} disabled={!valid || dup || !value.trim()}>Add variable</Button></div>
      </form>
    </Modal>
  );
}
