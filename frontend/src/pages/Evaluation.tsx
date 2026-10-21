import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Pencil, Play, Plus, Sparkles, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { Badge, Button, Empty, ErrorNote, Field, IconButton, Input, Modal, Panel, Select, Spinner, Switch, TableWrap, Tabs, Td, Textarea, Th, useConfirm, useToast } from "../components/ui";
import { api } from "../lib/api";
import { usd } from "../lib/format";

type EvalCase = { id?: number; question: string; expected_answer: string; expected_path: string | null; language: string; tags: string[]; history: { role: "user" | "assistant"; content: string }[]; active: boolean };
const fresh = (question = ""): EvalCase => ({ question, expected_answer: "", expected_path: null, language: /[\u0980-\u09ff]/.test(question) ? "bn" : "banglish", tags: [], history: [], active: true });
const pct = (n = 0) => `${(n * 100).toFixed(1)}%`;

/** The test set (was the Evaluation page), the third tab of Train the assistant. */
export default function Evaluation() {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const loc = useLocation();
  const initial = (loc.state as { question?: string } | null)?.question;
  const [tab, setTab] = useState<"cases" | "results">(initial ? "cases" : "results");
  const [editing, setEditing] = useState<EvalCase | null>(initial ? fresh(initial) : null);
  const [runId, setRunId] = useState<number | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const cases = useQuery({ queryKey: ["eval-cases"], queryFn: () => api<EvalCase[]>("/api/eval/cases") });
  const runs = useQuery({ queryKey: ["eval-runs"], queryFn: () => api<any[]>("/api/eval/runs"), refetchInterval: 3000 });
  useEffect(() => { if (!runId && runs.data?.length) setRunId(runs.data[0].id); }, [runs.data, runId]);
  const detail = useQuery({ queryKey: ["eval-run", runId], queryFn: () => api<any>(`/api/eval/runs/${runId}`), enabled: !!runId,
    refetchInterval: (q) => ["queued", "running"].includes(q.state.data?.status) ? 1500 : false });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/eval/cases/${id}`, { method: "DELETE" }), onSuccess: () => { toast("Evaluation case deleted"); qc.invalidateQueries({ queryKey: ["eval-cases"] }); } });
  const start = useMutation({ mutationFn: () => api<any>("/api/eval/runs", { body: {} }), onSuccess: (r) => { setRunId(r.id); setTab("results"); toast("Evaluation run started"); qc.invalidateQueries({ queryKey: ["eval-runs"] }); }, onError: (e) => toast(e instanceof Error ? e.message : "Could not start run", "danger") });
  const importCsv = useMutation({ mutationFn: (file: File) => { const form = new FormData(); form.append("file", file); return api<any>("/api/eval/import", { form }); },
    onSuccess: (r) => { toast(`Imported ${r.created} evaluation case(s)`); qc.invalidateQueries({ queryKey: ["eval-cases"] }); if (upload.current) upload.current.value = ""; },
    onError: (e) => toast(e instanceof Error ? e.message : "Import failed", "danger") });
  const starter = useMutation({ mutationFn: () => api<{ added: number; available: number }>("/api/eval/cases/starter", { method: "POST" }),
    onSuccess: (r) => { toast(r.added ? `${r.added} ready-made test(s) added` : "The ready-made tests are already in your test set"); setTab("cases"); qc.invalidateQueries({ queryKey: ["eval-cases"] }); } });
  const d = detail.data;
  return <>
    <ol className="mb-4 grid gap-2 text-sm sm:grid-cols-3">
      {[["Add tests", "Each test is a guest question and what the answer must say. Start with the ready-made ones, add your own, or “Add to test set” after finding a reason. No document upload is needed."],
        ["Run all tests", "The assistant answers every test question for real (nothing is sent to guests) and an AI judge marks each answer right or wrong."],
        ["Read the result", "The score and each wrong answer, compared with the last run. Run again after every rule or knowledge change."]].map(([t, x], i) =>
        <li key={t} className="rounded-panel border border-line bg-surface p-3"><p className="font-semibold">{i + 1}. {t}</p><p className="mt-1 text-muted">{x}</p></li>)}
    </ol>
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="max-w-2xl text-sm text-muted">{cases.data?.length ? `${cases.data.filter((c) => c.active).length} active test(s).` : "No tests yet."} A CSV import needs the columns question, expected_answer, language.</p>
      <div className="flex flex-wrap gap-2"><input ref={upload} type="file" accept=".csv" className="sr-only" onChange={(e) => e.target.files?.[0] && importCsv.mutate(e.target.files[0])} />
        <Button size="sm" onClick={() => upload.current?.click()} loading={importCsv.isPending} title="CSV columns: question, expected_answer, language"><Upload className="h-4 w-4" />Import CSV</Button>
        <Button size="sm" onClick={() => starter.mutate()} loading={starter.isPending} title="About 30 checked questions about courses, programs, offices, fees, payments and handover"><Sparkles className="h-4 w-4" />Load ready-made tests</Button>
        <Button size="sm" onClick={() => setEditing(fresh())}><Plus className="h-4 w-4" />Add a test</Button>
        <Button variant="primary" onClick={() => start.mutate()} loading={start.isPending} disabled={!cases.data?.some((c) => c.active)}><Play className="h-4 w-4" />Run all tests</Button></div></div>
    <Tabs value={tab} onChange={setTab} tabs={[{ id: "results", label: `Results${runs.data ? ` (${runs.data.length})` : ""}` }, { id: "cases", label: `Tests${cases.data ? ` (${cases.data.length})` : ""}` }]} />
    {tab === "cases" ? <>
      {cases.isLoading ? <Spinner /> : !cases.data?.length ? <Empty title="No tests yet">Click “Load ready-made tests” to start, or add your own.</Empty> :
        <TableWrap><thead><tr><Th>Question</Th><Th>Expected answer</Th><Th>Language / tags</Th><Th>Status</Th><Th /></tr></thead><tbody>{cases.data.map((c) =>
          <tr key={c.id} className={c.active ? "" : "opacity-60"}><Td className="max-w-[300px] font-semibold">{c.question}</Td><Td className="max-w-[420px] whitespace-pre-wrap">{c.expected_answer}</Td>
            <Td><Badge tone="blue">{c.language}</Badge>{c.tags.map((tag) => <Badge key={tag} className="ml-1">{tag}</Badge>)}</Td><Td><Badge tone={c.active ? "ok" : "neutral"}>{c.active ? "Active" : "Off"}</Badge></Td>
            <Td><div className="flex"><IconButton label="Edit case" onClick={() => setEditing(c)}><Pencil className="h-4 w-4" /></IconButton><IconButton label="Delete case" onClick={async () => (await ask({ title: "Delete this evaluation case?", message: c.question, confirmLabel: "Delete", danger: true })) && del.mutate(c.id!)}><Trash2 className="h-4 w-4" /></IconButton></div></Td></tr>)}</tbody></TableWrap>}
    </> : <>
      {!runs.data?.length ? (runs.isLoading ? <Spinner /> : <Empty title="No test runs yet">Add tests (here, or "Add to test set" after finding a reason), then click “Run all tests”.</Empty>) : <>
        <div className="mb-4 max-w-sm"><Select aria-label="Evaluation run" persistDraft={false} value={runId ?? ""} onChange={(e) => setRunId(+e.target.value)}>{runs.data.map((r) => <option key={r.id} value={r.id}>Run #{r.id} · {r.status} · {new Date(r.created_at).toLocaleString()}</option>)}</Select></div>
        {!d ? <Spinner /> : <RunDetails run={d} />}
      </>}
    </>}
    {editing && <CaseEditor value={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["eval-cases"] }); toast("Evaluation case saved"); }} />}
  </>;
}

function Metric({ label, value, delta, danger }: { label: string; value: string; delta?: number | null; danger?: boolean }) {
  return <div className="rounded-panel border border-line bg-surface p-4"><p className="text-xs font-semibold text-muted">{label}</p><p className={danger ? "mt-1 text-2xl font-bold text-danger" : "mt-1 text-2xl font-bold"}>{value}</p>{delta != null && <p className="mt-1 text-xs text-muted">{delta >= 0 ? "+" : ""}{label === "Cost" ? usd(delta) : pct(delta)} vs previous</p>}</div>;
}

function RunDetails({ run }: { run: any }) {
  const m = run.metrics;
  if (["queued", "running"].includes(run.status)) return <Panel title={`Run #${run.id}`} actions={<Badge tone="blue">{run.status}</Badge>}><Spinner label={`Evaluating ${run.completed} of ${run.total} cases`} /><div className="h-2 overflow-hidden rounded-full bg-sunken"><div className="h-full bg-accent" style={{ width: `${run.total ? run.completed / run.total * 100 : 0}%` }} /></div></Panel>;
  return <div className="space-y-5">
    {run.error && <ErrorNote error={run.error} />}
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric label="Accuracy" value={pct(m.accuracy)} delta={run.delta?.accuracy} /><Metric label="Missed rate" value={pct(m.missed_rate)} delta={run.delta?.missed_rate} danger={m.missed_rate > 0} /><Metric label="Hallucination rate" value={pct(m.hallucination_rate)} delta={run.delta?.hallucination_rate} danger={m.hallucination_rate > 0} /><Metric label="Cost" value={usd(m.cost_usd)} delta={run.delta?.cost_usd} /></div>
    <Panel title="By language"><TableWrap compact><thead><tr><Th>Language</Th><Th>Cases</Th><Th>Accuracy</Th><Th>Missed</Th><Th>Not grounded</Th></tr></thead><tbody>{Object.entries(m.by_language).map(([lang, x]: any) => <tr key={lang}><Td><Badge tone="blue">{lang}</Badge></Td><Td>{x.total}</Td><Td>{pct(x.accuracy)}</Td><Td>{pct(x.missed_rate)}</Td><Td>{pct(x.hallucination_rate)}</Td></tr>)}</tbody></TableWrap></Panel>
    <Panel title="Case results" actions={run.previous_run_id ? <Badge>Compared with run #{run.previous_run_id}</Badge> : undefined}>
      {!run.results.length ? <Empty title="No case results" /> : <div className="space-y-3">{run.results.map((r: any) => <details key={r.id} className="rounded-ctl border border-line p-3" open={!r.correct}>
        <summary className="cursor-pointer list-none"><div className="flex flex-wrap items-center gap-2"><Badge tone={r.correct ? "ok" : "danger"}>{r.correct ? "Correct" : "Wrong"}</Badge><Badge tone={r.grounded ? "ok" : "warn"}>{r.grounded ? "Grounded" : "Not grounded"}</Badge>{r.missed && <Badge tone="danger">Missed</Badge>}<Badge>{r.language}</Badge><span className="min-w-0 flex-1 font-semibold">{r.question}</span><span className="text-xs text-muted">{r.path} · {r.latency_ms} ms · {usd(r.cost_usd)}</span>{r.previous && r.previous.correct !== r.correct && <Badge tone={r.correct ? "ok" : "danger"}>{r.correct ? "Improved" : "Regressed"}</Badge>}</div></summary>
        <div className="mt-3 grid gap-3 lg:grid-cols-2"><div><p className="text-xs font-semibold text-muted">Expected</p><p className="mt-1 whitespace-pre-wrap rounded-ctl bg-sunken p-3 text-sm">{r.expected_answer}</p></div><div><p className="text-xs font-semibold text-muted">Actual</p><p className="mt-1 whitespace-pre-wrap rounded-ctl bg-sunken p-3 text-sm">{r.actual_answer || "(none)"}</p></div></div>
        <p className="mt-3 text-sm"><strong>Judge:</strong> {r.reason}</p>{r.retrieved_chunk_ids.length > 0 && <p className="mt-1 text-xs text-muted">Retrieved chunks: {r.retrieved_chunk_ids.join(", ")}</p>}{r.previous && <details className="mt-2"><summary className="cursor-pointer text-xs font-semibold text-muted">Previous answer</summary><p className="mt-1 whitespace-pre-wrap rounded-ctl bg-sunken p-2 text-sm">{r.previous.actual_answer}</p></details>}
      </details>)}</div>}
    </Panel>
    <details className="rounded-panel border border-line bg-surface p-4"><summary className="cursor-pointer font-semibold">Settings snapshot</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-ctl bg-sunken p-3 text-xs">{JSON.stringify(run.settings_snapshot, null, 2)}</pre></details>
  </div>;
}

function CaseEditor({ value, onClose, onSaved }: { value: EvalCase; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ ...value });
  const [tagText, setTagText] = useState(value.tags.join(", "));
  const [historyText, setHistoryText] = useState(value.history.map((t) => `${t.role}: ${t.content}`).join("\n"));
  const history = () => historyText.split("\n").map((line) => { const m = line.match(/^\s*(user|assistant)\s*:\s*(.+)$/i); return m ? { role: m[1].toLowerCase(), content: m[2] } : null; }).filter(Boolean);
  const save = useMutation({ mutationFn: () => api(value.id ? `/api/eval/cases/${value.id}` : "/api/eval/cases", { method: value.id ? "PUT" : "POST", body: { ...form, tags: tagText.split(",").map((x) => x.trim()).filter(Boolean), history: history() } }), onSuccess: onSaved });
  return <Modal wide title={value.id ? "Edit evaluation case" : "Add evaluation case"} onClose={onClose}><form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
    <Field label="Question">{(id) => <Textarea id={id} rows={3} required value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} />}</Field>
    <Field label="Expected answer">{(id) => <Textarea id={id} rows={5} required value={form.expected_answer} onChange={(e) => setForm({ ...form, expected_answer: e.target.value })} />}</Field>
    <Field label="Prior conversation (optional)" hint="For follow-up cases, one turn per line: user: ... or assistant: ...">{(id) => <Textarea id={id} rows={4} value={historyText} onChange={(e) => setHistoryText(e.target.value)} placeholder={"user: Quantum Method course fee koto?\nassistant: The course fee is 11,500 taka.\nuser: Oita ki installment-e deya jay?"} />}</Field>
    <div className="grid gap-3 sm:grid-cols-3"><Field label="Language">{(id) => <Select id={id} value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}><option value="bn">Bangla</option><option value="banglish">Banglish</option><option value="en">English</option><option value="unknown">Unknown</option></Select>}</Field>
      <Field label="Expected path (optional)">{(id) => <Input id={id} placeholder="qa_shortcut" value={form.expected_path ?? ""} onChange={(e) => setForm({ ...form, expected_path: e.target.value || null })} />}</Field>
      <Field label="Tags" hint="Comma separated">{(id) => <Input id={id} value={tagText} onChange={(e) => setTagText(e.target.value)} />}</Field></div>
    <Switch checked={form.active} onChange={(active) => setForm({ ...form, active })} label="Active in evaluation runs" /><ErrorNote error={save.error} />
    <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={save.isPending}><FlaskConical className="h-4 w-4" />Save case</Button></div>
  </form></Modal>;
}
