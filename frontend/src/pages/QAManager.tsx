import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Badge, Button, Empty, ErrorNote, Field, IconButton, Input, Modal, Panel, Select, Spinner, Switch, TableWrap, Td, Textarea, Th, useConfirm, useToast } from "../components/ui";
import { api, download } from "../lib/api";
import { useAuth } from "../lib/auth";
import KeywordsTab from "./KeywordsTab";

type QA = { id?: number; question: string; answer_template: string; category: string | null; data_class: "fixed" | "variable"; active: boolean; resolve_unanswered_id?: number };

export type QAView = "fixed" | "unknown" | "variables" | "programs" | "keywords";

/** One part of the Q&A data, chosen by the Knowledge page's side bar (no tab bar of its own). */
export default function QAManager({ view = "fixed" }: { view?: QAView }) {
  const loc = useLocation();
  const navigate = useNavigate();
  const pre = loc.state as { question?: string; unansweredId?: number } | null;
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const isAdmin = useAuth().user?.role === "admin";
  const tab = view;
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [editing, setEditing] = useState<QA | null>(pre?.question ? { question: pre.question, answer_template: "", category: null, data_class: "fixed", active: true, resolve_unanswered_id: pre.unansweredId } : null);
  const imp = useRef<HTMLInputElement>(null);
  const list = useQuery({ queryKey: ["qa", "fixed", search, category], queryFn: () => api("/api/qa", { params: { search, category, data_kind: "fixed", limit: 500 } }), enabled: tab === "fixed" });
  const unanswered = useQuery({ queryKey: ["unanswered"], queryFn: () => api<any[]>("/api/unanswered") });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/qa/${id}`, { method: "DELETE" }), onSuccess: () => { toast("Deleted"); qc.invalidateQueries({ queryKey: ["qa"] }); } });
  const setUnknownStatus = useMutation({ mutationFn: ({ id, status }: { id: number; status: string }) => api(`/api/unanswered/${id}`, { method: "PATCH", body: { status } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["unanswered"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); } });
  const draft = useMutation({ mutationFn: (id: number) => api<{ draft_answer: string | null }>(`/api/unanswered/${id}/draft`, { method: "POST" }),
    onSuccess: (r) => { toast(r.draft_answer ? "Draft suggested: review it, then Add answer" : "The knowledge base has nothing on this yet: write the answer yourself", r.draft_answer ? undefined : "danger"); qc.invalidateQueries({ queryKey: ["unanswered"] }); },
    onError: (e) => toast(e instanceof Error ? e.message : "Draft failed", "danger") });
  const importCsv = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); return api<{ created: number; updated: number; unchanged: number; embedding_deferred: boolean; missing_variables: string[] }>("/api/qa-import", { form: fd }); },
    onSuccess: (r) => {
      toast(`Imported: ${r.created} new, ${r.updated} updated, ${r.unchanged} already the same.`
        + (r.embedding_deferred ? " Some questions are still being prepared for meaning search (the AI quota is busy); exact and close wording already matches." : "")
        + (r.missing_variables?.length ? ` Answers using these variables are skipped until you give them a value (Variables tab): ${r.missing_variables.join(", ")}.` : ""),
        r.missing_variables?.length ? "danger" : undefined);
      qc.invalidateQueries({ queryKey: ["qa"] }); if (imp.current) imp.current.value = "";
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Import failed", "danger"),
  });
  return (
    <>
      {tab === "fixed" && <div className="mb-4 flex flex-wrap items-start justify-between gap-3"><p className="max-w-3xl text-sm text-muted">Exact answers for common questions. Fixed data never changes; variable data uses {"{variable}"} values (fees, phones, dates) you update in one place. Program dates and places are managed in Programs.</p><div className="flex flex-wrap gap-2">{<>
          <input ref={imp} type="file" accept=".csv,.tsv,.json,.jsonl,.xlsx,.xlsm" className="sr-only" aria-label="Import a Q&A file" onChange={(e) => e.target.files?.[0] && importCsv.mutate(e.target.files[0])} />
          <Button size="sm" onClick={() => imp.current?.click()} loading={importCsv.isPending} title="CSV, Excel or JSON with question and answer columns"><Upload className="h-3.5 w-3.5" aria-hidden />Import file</Button>
          <Button size="sm" onClick={() => download("/api/qa-export", "qa_pairs.csv").catch(() => toast("Export failed", "danger"))}><Download className="h-3.5 w-3.5" aria-hidden />Export CSV</Button>
          <Button variant="primary" onClick={() => setEditing({ question: "", answer_template: "", category: null, data_class: "fixed", active: true })}><Plus className="h-4 w-4" aria-hidden />Add Q&A</Button></>}</div></div>}
      {tab === "unknown" && <div>
          {!!unanswered.data?.length ? <Panel title="Unknown questions">
            <p className="mb-3 text-sm text-muted">Questions the assistant could not answer, and replies marked wrong, most asked first. A repeat of the same question raises its count instead of adding a row. Suggestions and drafts are never used until you save them with “Add answer”.</p>
            <ul className="divide-y divide-line">{unanswered.data.map((u) => <UnknownItem key={u.id} u={u} drafting={draft.isPending && draft.variables === u.id}
              onDraft={() => draft.mutate(u.id)} onIgnore={() => setUnknownStatus.mutate({ id: u.id, status: "ignored" })}
              onTest={() => navigate("/train?tab=tests", { state: { question: u.question } })}
              onAnswer={(answer, category) => { if (u.status === "new") setUnknownStatus.mutate({ id: u.id, status: "reviewing" }); setEditing({ question: u.question, answer_template: answer, category: category || null, data_class: "fixed", active: true, resolve_unanswered_id: u.id }); }} />)}</ul>
          </Panel> : unanswered.isLoading ? <Spinner /> : <Empty title="No unknown questions">Questions the assistant cannot answer will appear here.</Empty>}
        </div>}
      {tab === "fixed" && <div>
          <div className="mb-3 flex flex-wrap gap-2">
            <Input aria-label="Search questions and answers" placeholder="Search questions and answers" className="min-w-[200px] flex-1" value={search} onChange={(e) => setSearch(e.target.value)} />
            <Select aria-label="Category" className="w-auto" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>{list.data?.categories.map((c: string) => <option key={c}>{c}</option>)}</Select>
          </div>
          {list.isLoading ? <Spinner /> : !list.data?.items.length ? <Empty title="No fixed Q&A">Add information that always stays the same.</Empty> : (
            <TableWrap><thead><tr><Th>Question</Th><Th>Answer</Th><Th>Category</Th><Th>Keywords</Th><Th><span className="sr-only">Actions</span></Th></tr></thead>
              <tbody>{list.data.items.map((q: any) => (
                <tr key={q.id} className={q.active ? "" : "opacity-60"}><Td className="max-w-[260px] font-semibold">{q.question}{!q.active && <Badge className="ml-2">Off</Badge>}</Td><Td className="max-w-[360px] whitespace-pre-wrap break-words text-muted">{q.answer_template}</Td><Td>{q.category && <Badge tone="blue">{q.category}</Badge>}</Td>
                  <Td><div className="flex justify-end"><IconButton label={`Edit "${q.question}"`} onClick={() => setEditing(q)}><Pencil className="h-4 w-4" aria-hidden /></IconButton><IconButton label={`Delete "${q.question}"`} onClick={async () => (await ask({ title: "Delete this Q&A pair?", message: q.question, confirmLabel: "Delete", danger: true })) && del.mutate(q.id)}><Trash2 className="h-4 w-4" aria-hidden /></IconButton></div></Td></tr>
              ))}</tbody></TableWrap>
          )}
          {list.data && <p className="mt-2 text-sm text-muted">{list.data.total} pair(s)</p>}
        </div>}
      {tab === "variables" && <Variables />}
      {tab === "programs" && <Programs />}
      {tab === "keywords" && <KeywordsTab isAdmin={isAdmin} />}
      {editing && <QAEditor qa={editing} categories={list.data?.categories ?? []} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["qa"] }); qc.invalidateQueries({ queryKey: ["unanswered"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); toast("Saved"); }} />}
    </>
  );
}

/** One unknown question. The AI's suggestion (answer, category, keywords) can be edited and kept as a draft, then
 *  turned into a Q&A with "Add answer"; nothing is live before that. */
function UnknownItem({ u, drafting, onDraft, onIgnore, onTest, onAnswer }: { u: any; drafting: boolean; onDraft: () => void; onIgnore: () => void; onTest: () => void; onAnswer: (answer: string, category: string) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [answer, setAnswer] = useState(u.draft_answer ?? "");
  const [category, setCategory] = useState(u.suggested_category ?? "");
  const [keywords, setKeywords] = useState((u.suggested_keywords ?? []).join(", "));
  useEffect(() => { setAnswer(u.draft_answer ?? ""); setCategory(u.suggested_category ?? ""); setKeywords((u.suggested_keywords ?? []).join(", ")); }, [u.draft_answer, u.suggested_category, u.suggested_keywords]);
  const changed = answer !== (u.draft_answer ?? "") || category !== (u.suggested_category ?? "") || keywords !== (u.suggested_keywords ?? []).join(", ");
  const save = useMutation({
    mutationFn: () => api(`/api/unanswered/${u.id}`, { method: "PATCH", body: { draft_answer: answer, suggested_category: category, suggested_keywords: keywords.split(",").map((k: string) => k.trim()).filter(Boolean) } }),
    onSuccess: () => { toast("Suggestion saved (still a draft)"); qc.invalidateQueries({ queryKey: ["unanswered"] }); },
  });
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <span className="block break-words font-medium">{u.question}</span>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <Badge tone={u.occurrences > 1 ? "warn" : "neutral"}>asked {u.occurrences}×</Badge>
            <Badge tone={u.status === "reviewing" ? "blue" : "neutral"}>{u.status}</Badge>
            {u.reason === "wrong_answer" && <Badge tone="danger">answer marked wrong</Badge>}
            {u.reason === "needs_review" && <Badge tone="warn">needs review</Badge>}
            <span>· {u.channel || "unknown channel"}</span>
          </div>
          {u.wrong_answer && <p className="mt-1 break-words text-xs text-muted"><span className="font-semibold">Wrong reply:</span> {u.wrong_answer}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button size="sm" loading={drafting} onClick={onDraft}>{u.draft_answer ? "Suggest again" : "Suggest an answer"}</Button>
          <Button size="sm" onClick={onTest}>Add to test set</Button>
          <Button size="sm" onClick={onIgnore}>Ignore</Button>
        </div>
      </div>
      <div className="mt-2 grid gap-2 rounded-ctl border border-line bg-sunken/50 p-2.5 lg:grid-cols-[1fr_220px]">
        <div>
          <p className="mb-1 text-xs font-semibold text-muted">{u.draft_answer ? "AI suggestion: edit it as you like" : "Answer (write it, or click Suggest an answer)"}</p>
          <Textarea persistDraft={false} rows={3} aria-label={`Answer for ${u.question}`} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="The answer guests should get" />
        </div>
        <div className="space-y-2">
          <div><p className="mb-1 text-xs font-semibold text-muted">Category</p><Input persistDraft={false} aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. Courses" /></div>
          <div><p className="mb-1 text-xs font-semibold text-muted">Keywords</p><Input persistDraft={false} aria-label="Keywords, comma separated" value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="comma separated" /></div>
        </div>
        <div className="flex flex-wrap justify-end gap-2 lg:col-span-2">
          <Button size="sm" disabled={!changed} loading={save.isPending} onClick={() => save.mutate()}>Save suggestion</Button>
          <Button size="sm" variant="primary" disabled={!answer.trim()} onClick={() => onAnswer(answer, category)}>Add answer</Button>
        </div>
        <ErrorNote error={save.error} />
      </div>
    </li>
  );
}

function QAEditor({ qa, categories, onClose, onSaved }: { qa: QA; categories: string[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState(qa);
  const vars = useQuery({ queryKey: ["vars"], queryFn: () => api<{ key: string }[]>("/api/variables") });
  const [preview, setPreview] = useState<{ term: string; origin: string; shared: number }[]>([]);
  useEffect(() => {
    if (!f.question.trim()) { setPreview([]); return; }
    const t = setTimeout(() => api<{ keywords: { term: string; origin: string; shared: number }[] }>("/api/keywords/preview", { body: { question: f.question, answer: f.answer_template, category: f.category } }).then((r) => setPreview(r.keywords.filter((k) => k.origin !== "phrase").slice(0, 24))).catch(() => setPreview([])), 600);
    return () => clearTimeout(t);
  }, [f.question, f.answer_template, f.category]);
  const save = useMutation({ mutationFn: () => (qa.id ? api(`/api/qa/${qa.id}`, { method: "PUT", body: f }) : api("/api/qa", { body: f })), onSuccess: onSaved });
  return (
    <Modal wide title={qa.id ? "Edit Q&A" : "Add Q&A"} onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Question" hint="Write it the way customers ask it. Add a few variants as separate pairs.">{(i) => <Input id={i} required value={f.question} onChange={(e) => setF({ ...f, question: e.target.value })} />}</Field>
        <Field label="Answer" hint={vars.data?.length ? `Available variables: ${vars.data.map((v) => `{${v.key}}`).join(" ")}` : "Use {variable} for values you manage in the Variables panel."}>{(i) => <Textarea id={i} required rows={5} value={f.answer_template} onChange={(e) => setF({ ...f, answer_template: e.target.value })} />}</Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Category">{(i) => <Select id={i} value={f.category ?? ""} onChange={(e) => setF({ ...f, category: e.target.value || null })}><option value="">— none —</option>{(categories.includes(f.category ?? "") || !f.category ? categories : [...categories, f.category!]).map((c) => <option key={c} value={c}>{c}</option>)}</Select>}</Field>
          <Field label="Data type" hint="Fixed stays the same; Variable may change occasionally.">{(i) => <Select id={i} value={f.data_class} onChange={(e) => setF({ ...f, data_class: e.target.value as QA["data_class"] })}><option value="fixed">Fixed data</option><option value="variable">Variable data</option></Select>}</Field>
          <div className="self-end pb-2"><Switch checked={f.active} onChange={(v) => setF({ ...f, active: v })} label="Active" /></div>
        </div>
        {preview.length > 0 && <div><p className="mb-1 text-xs font-semibold text-muted">Keywords created automatically (added when you save; ×N = other records using it)</p><div className="flex flex-wrap gap-1.5">{preview.map((k) => <Badge key={k.term} tone={k.shared === 0 ? "ok" : "neutral"}>{k.term}{k.shared > 0 && <span className="opacity-60"> ×{k.shared}</span>}</Badge>)}</div></div>}
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
  const usage = useQuery({ queryKey: ["vars-usage"], queryFn: () => api<Record<string, { count: number; examples: string[] }>>("/api/variables/usage") });
  const [draft, setDraft] = useState<Record<string, { value: string; description: string }>>({});
  const [adding, setAdding] = useState(false);
  const [find, setFind] = useState("");
  const [onlyEmpty, setOnlyEmpty] = useState(false);
  // saved variables + the ones Q&A answers use but that were never added; a variable without a value blocks its answers
  const all: Var[] = [...(vars.data ?? []), ...Object.keys(usage.data ?? {}).filter((k) => !vars.data?.some((v) => v.key === k)).map((key) => ({ key, value: "", description: null }))];
  const used = (k: string) => usage.data?.[k]?.count ?? 0;
  const isEmpty = (v: Var) => !v.value.trim();
  const blocked = all.filter((v) => isEmpty(v) && used(v.key) > 0);
  const allKeys = all.map((v) => v.key).join("|");
  useEffect(() => { if (vars.data) setDraft(Object.fromEntries(all.map((v) => [v.key, { value: v.value, description: v.description ?? "" }]))); }, [vars.data, allKeys]);  // eslint-disable-line react-hooks/exhaustive-deps
  const changed = all.filter((v) => draft[v.key] && (draft[v.key].value !== v.value || draft[v.key].description !== (v.description ?? "")));
  const saveAll = useMutation({
    mutationFn: async () => { for (const v of changed) await api(`/api/variables/${v.key}`, { method: "PUT", body: { value: draft[v.key].value, description: draft[v.key].description } }); return changed.length; },
    onSuccess: (n) => { toast(`${n} variable(s) saved`); qc.invalidateQueries({ queryKey: ["vars"] }); qc.invalidateQueries({ queryKey: ["vars-usage"] }); },
  });
  const del = useMutation({ mutationFn: (k: string) => api(`/api/variables/${k}`, { method: "DELETE" }), onSuccess: () => { toast("Variable deleted"); qc.invalidateQueries({ queryKey: ["vars"] }); } });
  const f = find.trim().toLowerCase();
  const rows = all
    .filter((v) => !onlyEmpty || (isEmpty(v) && used(v.key) > 0))
    .filter((v) => !f || v.key.toLowerCase().includes(f) || v.value.toLowerCase().includes(f) || (v.description ?? "").toLowerCase().includes(f))
    .sort((a, b) => Number(isEmpty(b) && used(b.key) > 0) - Number(isEmpty(a) && used(a.key) > 0) || used(b.key) - used(a.key) || a.key.localeCompare(b.key));
  const set = (k: string, patch: Partial<{ value: string; description: string }>) => setDraft({ ...draft, [k]: { ...draft[k], ...patch } });
  const cell = "w-full rounded-none border-0 bg-transparent px-2 py-1.5 text-sm focus:bg-surface focus:outline focus:outline-2 focus:outline-accent";
  return (
    <Panel title={`Variable values${vars.data ? ` (${all.length})` : ""}`} actions={<div className="flex gap-2">
      {changed.length > 0 && <Button size="sm" variant="primary" loading={saveAll.isPending} onClick={() => saveAll.mutate()}>Save {changed.length} change(s)</Button>}
      <Button size="sm" onClick={() => setAdding(true)}><Plus className="h-4 w-4" aria-hidden />New variable</Button></div>}>
      <p className="mb-3 text-sm text-muted">Use these for values that change occasionally, such as a course fee, phone number or registration link. Edit any cell and save; every Q&A answer using {"{name}"} updates too. Do not add program dates or places here—use Programs.</p>
      {blocked.length > 0 && <div className="mb-3 flex flex-wrap items-center gap-3 rounded-ctl border border-danger/40 bg-danger-soft px-3 py-2 text-sm">
        <span><b>{blocked.length} variable(s) have no value</b>, so {blocked.reduce((n, v) => n + used(v.key), 0)} Q&A answer(s) that use them are never sent. Fill in the values below and save.</span>
        <Button size="sm" onClick={() => setOnlyEmpty(!onlyEmpty)}>{onlyEmpty ? "Show all" : "Show only these"}</Button>
      </div>}
      {vars.isLoading ? <Spinner /> : !all.length ? <Empty title="No variables yet">Click “New variable” to add one, for example Course_fee = ১১,৫০০ টাকা.</Empty> : <>
        <Input aria-label="Search variables" persistDraft={false} placeholder="Search name, value or description" className="mb-3" value={find} onChange={(e) => setFind(e.target.value)} />
        <div className="max-h-[65vh] overflow-auto rounded-ctl border border-line">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-sunken"><tr>
              <th className="w-10 border-b border-r border-line px-2 py-2 text-right text-xs font-semibold text-muted">#</th>
              <th className="w-[22%] border-b border-r border-line px-2 py-2 text-left text-xs font-semibold text-muted">Name</th>
              <th className="w-[28%] border-b border-r border-line px-2 py-2 text-left text-xs font-semibold text-muted">Value</th>
              <th className="border-b border-r border-line px-2 py-2 text-left text-xs font-semibold text-muted">What it is (how guests ask)</th>
              <th className="w-24 border-b border-r border-line px-2 py-2 text-left text-xs font-semibold text-muted">Used in</th>
              <th className="w-10 border-b border-line"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{rows.map((v, n) => {
              const d = draft[v.key] ?? { value: v.value, description: v.description ?? "" };
              const dirty = d.value !== v.value || d.description !== (v.description ?? "");
              const saved = vars.data?.some((x) => x.key === v.key);
              const missing = isEmpty(v) && used(v.key) > 0;
              const ex = usage.data?.[v.key]?.examples ?? [];
              return (
                <tr key={v.key} className={dirty ? "bg-warn-soft/60" : missing ? "bg-danger-soft/50" : "odd:bg-surface even:bg-sunken/40"}>
                  <td className="border-b border-r border-line px-2 text-right text-xs tabular-nums text-muted">{n + 1}</td>
                  <td className="border-b border-r border-line px-2 py-1.5"><code className="break-all font-semibold">{v.key}</code>{missing && <span className="ml-1 text-xs font-semibold text-danger">no value</span>}</td>
                  <td className="border-b border-r border-line p-0"><input aria-label={`Value of ${v.key}`} className={cell} placeholder={missing ? "Type the value here" : undefined} value={d.value} onChange={(e) => set(v.key, { value: e.target.value })} /></td>
                  <td className="border-b border-r border-line p-0"><input aria-label={`Description of ${v.key}`} className={cell} placeholder="e.g. কোয়ান্টাম মেথড কোর্সের ফি" value={d.description} onChange={(e) => set(v.key, { description: e.target.value })} /></td>
                  <td className="border-b border-r border-line px-2 text-xs tabular-nums text-muted" title={ex.length ? `For example:\n${ex.join("\n")}` : undefined}>{used(v.key) ? `${used(v.key)} Q&A` : "–"}</td>
                  <td className="border-b border-line text-center">{saved && <IconButton label={`Delete ${v.key}`} className="h-7 w-7" onClick={async () => (await ask({ title: `Delete {${v.key}}?`, message: "Q&A answers that use it stop being sent until it is added again.", confirmLabel: "Delete", danger: true })) && del.mutate(v.key)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></IconButton>}</td>
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

type Prog = { id: number; name: string; date_text: string; start_date: string; end_date: string | null; fee: string | null; place: string | null; notes: string | null; days_left: number; running: boolean };
type ProgDraft = { name: string; date_text: string; fee: string; place: string; notes: string };
const toDraft = (p: Prog): ProgDraft => ({ name: p.name, date_text: p.date_text || p.start_date, fee: p.fee ?? "", place: p.place ?? "", notes: p.notes ?? "" });
const draftBody = (d: ProgDraft) => ({ name: d.name.trim(), date_text: d.date_text.trim(), fee: d.fee.trim() || null, place: d.place.trim() || null, notes: d.notes.trim() || null });

function Programs() {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const list = useQuery({ queryKey: ["programs"], queryFn: () => api<{ today: string; items: Prog[] }>("/api/programs") });
  const syncStatus = useQuery({ queryKey: ["program-sync"], queryFn: () => api<{ enabled: boolean; source_url: string; last: { status?: string; last_run?: string; found?: number; added?: number; updated?: number; message?: string } }>("/api/programs/sync/status") });
  const sync = useMutation({ mutationFn: () => api<{ status: string; found: number; added: number; updated: number; message?: string }>("/api/programs/sync", { method: "POST" }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["programs"] }); qc.invalidateQueries({ queryKey: ["program-sync"] }); toast(r.status === "done" ? `Website sync complete: ${r.added} added, ${r.updated} updated` : r.message ?? "Website sync needs attention"); } });
  const [draft, setDraft] = useState<Record<number, ProgDraft>>({});
  const [adding, setAdding] = useState(false);
  useEffect(() => { if (list.data) setDraft(Object.fromEntries(list.data.items.map((p) => [p.id, toDraft(p)]))); }, [list.data]);
  const items = list.data?.items ?? [];
  const dirty = items.filter((p) => draft[p.id] && JSON.stringify(draft[p.id]) !== JSON.stringify(toDraft(p)));
  const saveAll = useMutation({
    mutationFn: async () => { for (const p of dirty) await api(`/api/programs/${p.id}`, { method: "PUT", body: draftBody(draft[p.id]) }); return dirty.length; },
    onSuccess: (n) => { toast(`${n} program(s) saved`); qc.invalidateQueries({ queryKey: ["programs"] }); },
  });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/programs/${id}`, { method: "DELETE" }), onSuccess: () => { toast("Program deleted"); qc.invalidateQueries({ queryKey: ["programs"] }); } });
  const set = (id: number, patch: Partial<ProgDraft>) => setDraft({ ...draft, [id]: { ...draft[id], ...patch } });
  const cell = "w-full rounded-none border-0 bg-transparent px-2 py-1.5 text-sm focus:bg-surface focus:outline focus:outline-2 focus:outline-accent";
  return (
    <Panel title={`Programs${items.length ? ` (${items.length})` : ""}`} actions={<div className="flex gap-2">
      {dirty.length > 0 && <Button size="sm" variant="primary" loading={saveAll.isPending} onClick={() => saveAll.mutate()}>Save {dirty.length} change(s)</Button>}
      <Button size="sm" loading={sync.isPending} onClick={() => sync.mutate()}>Sync website</Button>
      <Button size="sm" onClick={() => setAdding(true)}><Plus className="h-4 w-4" aria-hidden />New program</Button></div>}>
      <p className="mb-1 text-sm text-muted">All upcoming courses and events are listed here. The assistant answers program date, fee and place questions directly from this table, not from Q&A.</p>
      <p className="mb-3 text-xs text-muted">Official Quantum Method website schedule. It checks daily at 12:15 AM Bangladesh time. Website sync only adds/updates publishable event records; it never deletes manual programs.{syncStatus.data?.last?.last_run ? ` Last check: ${syncStatus.data.last.last_run} (${syncStatus.data.last.status}).` : ""}</p>
      {list.isLoading ? <Spinner /> : !items.length ? <Empty title="No upcoming programs">Click “New program”. Questions about dates and fees are answered from the knowledge base until you add some.</Empty> : (
        <div className="overflow-auto rounded-ctl border border-line">
          <table className="w-full min-w-[920px] border-collapse text-sm">
            <thead className="bg-sunken"><tr>
              {["#", "Program name", "Date / schedule", "Fee / comments", "Place", "Note (optional)", ""].map((h, i) => <th key={i} scope="col" className="border-b border-r border-line px-2 py-2 text-left text-xs font-semibold text-muted last:border-r-0">{h}</th>)}</tr></thead>
            <tbody>{items.map((p, n) => {
              const dr = draft[p.id] ?? toDraft(p);
              const changed = JSON.stringify(dr) !== JSON.stringify(toDraft(p));
              return <tr key={p.id} className={changed ? "bg-warn-soft/60" : n === 0 ? "bg-accent-soft/50" : "odd:bg-surface even:bg-sunken/40"}>
                <td className="whitespace-nowrap border-b border-r border-line px-2 text-xs text-muted">{n === 0 ? <Badge tone="accent">Next</Badge> : n + 1}</td>
                <td className="border-b border-r border-line p-0"><input aria-label="Program name" className={`${cell} font-semibold`} value={dr.name} onChange={(e) => set(p.id, { name: e.target.value })} /></td>
                <td className="border-b border-r border-line p-0"><input aria-label={`Schedule for ${p.name}`} className={cell} placeholder="2026-11-12" value={dr.date_text} onChange={(e) => set(p.id, { date_text: e.target.value })} /><span className="block px-2 pb-1 text-xs text-muted">{p.running ? "running now" : `in ${p.days_left} day${p.days_left === 1 ? "" : "s"}`}</span></td>
                <td className="border-b border-r border-line p-0"><input aria-label={`Fee and comments for ${p.name}`} className={cell} placeholder="Fee, discounts, registration notes…" value={dr.fee} onChange={(e) => set(p.id, { fee: e.target.value })} /></td>
                <td className="border-b border-r border-line p-0"><input aria-label={`Place of ${p.name}`} className={cell} value={dr.place} onChange={(e) => set(p.id, { place: e.target.value })} /></td>
                <td className="border-b border-r border-line p-0"><input aria-label={`Note for ${p.name}`} className={cell} value={dr.notes} onChange={(e) => set(p.id, { notes: e.target.value })} /></td>
                <td className="border-b border-line text-center"><IconButton label={`Delete ${p.name}`} className="h-7 w-7" onClick={async () => (await ask({ title: `Delete "${p.name}"?`, message: "The assistant stops answering about this program.", confirmLabel: "Delete", danger: true })) && del.mutate(p.id)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></IconButton></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      )}
      <ErrorNote error={saveAll.error} />
      <ErrorNote error={sync.error} />
      {adding && <NewProgram onClose={() => setAdding(false)} onSaved={() => { setAdding(false); qc.invalidateQueries({ queryKey: ["programs"] }); toast("Program added"); }} />}
    </Panel>
  );
}

function NewProgram({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<ProgDraft>({ name: "", date_text: "", fee: "", place: "", notes: "" });
  const save = useMutation({ mutationFn: () => api("/api/programs", { body: draftBody(f) }), onSuccess: onSaved });
  return <Modal title="New program" onClose={onClose}>
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Field label="Program name">{(i) => <Input id={i} persistDraft={false} autoFocus required placeholder="Quantum Method Course" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
      <Field label="Date / schedule" hint="Use a date such as 12 October. For several days, write both dates in this same field.">{(i) => <Input id={i} persistDraft={false} required placeholder="2026-11-12" value={f.date_text} onChange={(e) => setF({ ...f, date_text: e.target.value })} />}</Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Fee / comments" hint="Add fee, discount, registration and payment notes together.">{(i) => <Input id={i} persistDraft={false} placeholder="11,500 taka; early-bird discount..." value={f.fee} onChange={(e) => setF({ ...f, fee: e.target.value })} />}</Field>
        <Field label="Place">{(i) => <Input id={i} persistDraft={false} value={f.place} onChange={(e) => setF({ ...f, place: e.target.value })} />}</Field>
      </div>
      <Field label="Note (optional)">{(i) => <Textarea id={i} persistDraft={false} rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} />}</Field>
      <ErrorNote error={save.error} />
      <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={save.isPending} disabled={!f.name.trim() || !f.date_text.trim()}>Add program</Button></div>
    </form>
  </Modal>;
}
