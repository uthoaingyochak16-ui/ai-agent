import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, CloudUpload, FileText, Pencil, Plus, RefreshCw, Search, Trash2, Upload, X } from "lucide-react";
import { useRef, useState, type DragEvent, type ReactNode } from "react";
import { Badge, Button, Empty, ErrorNote, Field, IconButton, Input, Modal, PageHeader, Select, Spinner, Switch, TableWrap, Tabs, Td, Textarea, Th, cx, useToast, useConfirm } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago } from "../lib/format";

const TONE: Record<string, "neutral" | "warn" | "ok" | "danger"> = { pending: "neutral", processing: "warn", ready: "ok", failed: "danger", superseded: "neutral" };
const LABEL: Record<string, string> = { pending: "Waiting", processing: "Processing", ready: "Ready", failed: "Failed", superseded: "Old version" };
const FORMATS = ["PDF", "Word", "Excel", "PowerPoint", "CSV", "JSON", "TXT", "HTML", "RTF", "EPUB", "ZIP", "Images (OCR)"];
const MAX_MB = 50;
const size = (b: number) => (b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
type Tab = "documents" | "cache" | "search";

export default function Knowledge() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("documents");
  const [uploading, setUploading] = useState(false);
  const docs = useQuery({ queryKey: ["docs"], queryFn: () => api<any[]>("/api/documents"), refetchInterval: (q) => (q.state.data?.some((d: any) => d.status === "pending" || d.status === "processing") ? 2000 : false) });
  const cache = useQuery({ queryKey: ["cache", "", ""], queryFn: () => api<any>("/api/cache", { params: { search: "", status: "", limit: 100 } }) });

  const all = docs.data ?? [];
  const live = all.filter((d) => d.status !== "superseded");
  const ready = live.filter((d) => d.status === "ready");
  const busy = live.filter((d) => d.status === "pending" || d.status === "processing").length;
  const failed = live.filter((d) => d.status === "failed").length;

  return (
    <>
      <PageHeader title="Knowledge base" subtitle="The documents the assistant answers from, the answers it has saved, and a way to test what it finds."
        actions={admin && <Button variant="primary" onClick={() => setUploading(true)}><Upload className="h-4 w-4" aria-hidden />Upload document</Button>} />

      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Documents" value={docs.data ? ready.length : "–"} note={docs.data && live.length !== ready.length ? `of ${live.length}` : undefined} />
        <Stat label="Text pieces" value={docs.data ? ready.reduce((n, d) => n + (d.chunk_count ?? 0), 0) : "–"} />
        <Stat label="Needs attention" value={docs.data ? failed + busy : "–"} tone={failed ? "danger" : busy ? "warn" : undefined}
          note={failed ? `${failed} failed` : busy ? `${busy} processing` : docs.data ? "All good" : undefined} />
        <Stat label="Saved answers" value={cache.data ? cache.data.total : "–"} />
      </div>

      <Tabs<Tab> value={tab} onChange={setTab} tabs={[
        { id: "documents", label: `Documents${docs.data ? ` (${live.length})` : ""}` },
        { id: "cache", label: `Saved answers${cache.data ? ` (${cache.data.total})` : ""}` },
        { id: "search", label: "Search test" },
      ]} />

      {tab === "documents" && <Documents admin={admin} docs={docs} onUpload={() => setUploading(true)} />}
      {tab === "cache" && <SavedAnswers admin={admin} />}
      {tab === "search" && <SearchTest admin={admin} />}

      {uploading && <UploadModal onClose={() => setUploading(false)} onDone={() => { setUploading(false); qc.invalidateQueries({ queryKey: ["docs"] }); }} />}
    </>
  );
}

function Stat({ label, value, note, tone }: { label: string; value: ReactNode; note?: string; tone?: "warn" | "danger" }) {
  return (
    <div className="rounded-panel border border-line bg-surface px-4 py-3">
      <p className="text-xs font-semibold text-muted">{label}</p>
      <p className={cx("mt-1 text-2xl font-bold tabular-nums", tone === "danger" && "text-danger", tone === "warn" && "text-warn")}>{value}</p>
      {note && <p className="text-xs text-muted">{note}</p>}
    </div>
  );
}

function Toolbar({ children }: { children: ReactNode }) {
  return <div className="mb-3 flex flex-wrap items-center gap-2">{children}</div>;
}

function SearchBox({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="relative min-w-[200px] flex-1">
      <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" aria-hidden />
      <Input aria-label={label} persistDraft={false} placeholder={label} className="pl-9" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

/* ---------- documents ---------- */
function Documents({ admin, docs, onUpload }: { admin: boolean; docs: { data?: any[]; isLoading: boolean }; onUpload: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [find, setFind] = useState("");
  const [showOld, setShowOld] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["docs"] });
  const re = useMutation({ mutationFn: (id: number) => api(`/api/documents/${id}/reprocess`, { method: "POST" }), onSuccess: () => { toast("Processing again"); refresh(); } });
  const reAll = useMutation({ mutationFn: () => api<{ queued: number }>("/api/documents/reprocess-all", { method: "POST" }), onSuccess: (r) => { toast(`${r.queued} document(s) are being processed again`); refresh(); } });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/documents/${id}`, { method: "DELETE" }), onSuccess: () => { toast("Document deleted"); refresh(); } });

  if (docs.isLoading) return <Spinner />;
  const all = docs.data ?? [];
  if (!all.length) return (
    <Empty title="No documents yet">
      Upload your price list, policies or FAQ so the assistant has something to answer from.
      {admin && <span className="mt-4 flex justify-center"><Button variant="primary" onClick={onUpload}><Upload className="h-4 w-4" aria-hidden />Upload document</Button></span>}
    </Empty>
  );
  const oldCount = all.filter((d) => d.status === "superseded").length;
  const f = find.trim().toLowerCase();
  const rows = all.filter((d) => (showOld || d.status !== "superseded") && (!f || d.title.toLowerCase().includes(f)));

  return (
    <>
      <Toolbar>
        <SearchBox label="Search documents" value={find} onChange={setFind} />
        {oldCount > 0 && <Switch checked={showOld} onChange={setShowOld} label={`Show old versions (${oldCount})`} />}
        {admin && <Button variant="ghost" onClick={async () => (await ask({ title: "Process all documents again?", message: "Every document is cut into pieces again with the current settings. Manual edits to the text are replaced.", confirmLabel: "Process again" })) && reAll.mutate()} loading={reAll.isPending}>
          <RefreshCw className="h-4 w-4" aria-hidden />Process all again</Button>}
      </Toolbar>
      {!rows.length ? <Empty title="No matching documents">Try another title.</Empty> : (
        <TableWrap>
          <thead><tr><Th>Document</Th><Th>Status</Th><Th className="text-right">Text pieces</Th><Th>Added</Th><Th><span className="sr-only">Actions</span></Th></tr></thead>
          <tbody>{rows.map((d) => (
            <tr key={d.id} className={d.status === "superseded" ? "opacity-60" : ""}>
              <Td>
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-ctl bg-accent-soft text-accent-strong"><FileText className="h-4 w-4" aria-hidden /></span>
                  <div className="min-w-0">
                    <p className="font-semibold">{d.title}</p>
                    <p className="text-xs text-muted">{d.type.toUpperCase()} · version {d.version}</p>
                  </div>
                </div>
              </Td>
              <Td><Badge tone={TONE[d.status]}>{LABEL[d.status] ?? d.status}</Badge>{d.error && <p className="mt-1 max-w-xs text-xs text-danger">{d.error}</p>}</Td>
              <Td className="text-right tabular-nums">{d.chunk_count}</Td>
              <Td className="whitespace-nowrap text-muted">{ago(d.created_at)}</Td>
              <Td>
                <div className="flex justify-end gap-1">
                  <Button size="sm" disabled={d.status !== "ready"} onClick={() => setOpen(d.id)}>{admin ? <><Pencil className="h-3.5 w-3.5" aria-hidden />Open</> : <><FileText className="h-3.5 w-3.5" aria-hidden />View</>}</Button>
                  {admin && <IconButton label="Process again" onClick={() => re.mutate(d.id)}><RefreshCw className="h-4 w-4" aria-hidden /></IconButton>}
                  {admin && <IconButton label={`Delete ${d.title}`} onClick={async () => (await ask({ title: `Delete "${d.title}"?`, message: "The file and all its text are removed. The assistant will no longer answer from it. This cannot be undone.", confirmLabel: "Delete", danger: true })) && del.mutate(d.id)}><Trash2 className="h-4 w-4" aria-hidden /></IconButton>}
                </div>
              </Td>
            </tr>
          ))}</tbody>
        </TableWrap>
      )}
      {open && <ChunkEditor id={open} editable={admin} onClose={() => { setOpen(null); refresh(); }} />}
    </>
  );
}

function UploadModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [over, setOver] = useState(false);
  const tooBig = !!picked && picked.size > MAX_MB * 1024 * 1024;
  const upload = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append("file", picked!); if (title.trim()) fd.append("title", title.trim()); return api("/api/documents", { form: fd }); },
    onSuccess: () => { toast("Uploaded. Processing in the background."); onDone(); },
  });
  const choose = (f: File | undefined) => { if (f) { setPicked(f); upload.reset(); } };
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); choose(e.dataTransfer.files?.[0]); };
  return (
    <Modal title="Upload a document" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (picked && !tooBig) upload.mutate(); }}>
        <input ref={input} type="file" className="sr-only" aria-label="Choose a file" onChange={(e) => choose(e.target.files?.[0])} />
        {!picked ? (
          <button type="button" onClick={() => input.current?.click()} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}
            className={cx("flex w-full flex-col items-center gap-2 rounded-panel border-2 border-dashed px-4 py-8 text-center transition-colors", over ? "border-accent bg-accent-soft" : "border-line hover:border-accent hover:bg-sunken/60")}>
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-accent-soft text-accent-strong"><CloudUpload className="h-5 w-5" aria-hidden /></span>
            <span className="font-semibold">Drop a file here or click to browse</span>
            <span className="text-xs text-muted">{FORMATS.join(", ")} · up to {MAX_MB} MB</span>
          </button>
        ) : (
          <div className={cx("flex items-center gap-3 rounded-ctl border px-3 py-2.5", tooBig ? "border-danger bg-danger-soft" : "border-line bg-sunken/50")}>
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-ctl bg-accent-soft text-accent-strong"><FileText className="h-4 w-4" aria-hidden /></span>
            <div className="min-w-0 flex-1"><p className="truncate font-semibold">{picked.name}</p><p className={cx("text-sm", tooBig ? "text-danger" : "text-muted")}>{size(picked.size)}{tooBig && ` · too large (max ${MAX_MB} MB)`}</p></div>
            <IconButton label="Choose another file" onClick={() => { setPicked(null); if (input.current) input.current.value = ""; }}><X className="h-4 w-4" aria-hidden /></IconButton>
          </div>
        )}
        <Field label="Title (optional)" hint="Defaults to the file name. Uploading with an existing title replaces that document.">
          {(i) => <Input id={i} persistDraft={false} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={picked ? picked.name.replace(/\.[^.]+$/, "") : ""} />}
        </Field>
        <p className="text-xs text-muted">Q&A files (CSV, Excel or JSON with "question" and "answer" columns) keep every pair as its own piece, so answers stay precise.</p>
        <ErrorNote error={upload.error} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!picked || tooBig} loading={upload.isPending}><Upload className="h-4 w-4" aria-hidden />Upload</Button>
        </div>
      </form>
    </Modal>
  );
}

function ChunkEditor({ id, editable, onClose }: { id: number; editable: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [find, setFind] = useState("");
  const [edit, setEdit] = useState<{ id?: number; question: string; content: string } | null>(null);
  const q = useQuery({ queryKey: ["chunks", id], queryFn: () => api(`/api/documents/${id}/chunks`, { params: { limit: 200 } }) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["chunks", id] });
  const save = useMutation({
    mutationFn: (c: { id?: number; question: string; content: string }) => api(c.id ? `/api/documents/${id}/chunks/${c.id}` : `/api/documents/${id}/chunks`, { method: c.id ? "PUT" : "POST", body: { content: c.content, question: c.question || null } }),
    onSuccess: () => { toast("Saved. The assistant uses the new text right away."); setEdit(null); refresh(); },
  });
  const del = useMutation({ mutationFn: (cid: number) => api(`/api/documents/${id}/chunks/${cid}`, { method: "DELETE" }), onSuccess: () => { toast("Text piece removed"); refresh(); } });
  const f = find.trim().toLowerCase();
  const chunks = (q.data?.chunks ?? []).filter((c: any) => !f || c.content.toLowerCase().includes(f) || (c.question ?? "").toLowerCase().includes(f));
  return (
    <Modal wide title={q.data ? q.data.document.title : "Document"} onClose={onClose}>
      {q.isLoading ? <Spinner /> : (
        <div className="space-y-3">
          <Toolbar>
            <SearchBox label="Search in this document" value={find} onChange={setFind} />
            {editable && <Button onClick={() => setEdit({ question: "", content: "" })}><Plus className="h-4 w-4" aria-hidden />Add text</Button>}
          </Toolbar>
          <p className="text-xs text-muted">{q.data.total} piece(s){q.data.total > q.data.chunks.length ? `, showing the first ${q.data.chunks.length}` : ""}. Edits take effect immediately; “Process again” re-reads the file and replaces them.</p>
          {edit && !edit.id && <ChunkForm value={edit} onChange={setEdit} onCancel={() => setEdit(null)} onSave={() => save.mutate(edit)} saving={save.isPending} error={save.error} />}
          <ol className="max-h-[58vh] space-y-2 overflow-y-auto pr-1">{chunks.map((c: any) => (
            <li key={c.id} className="rounded-ctl border border-line p-3">
              {edit && edit.id === c.id ? <ChunkForm value={edit} onChange={setEdit} onCancel={() => setEdit(null)} onSave={() => save.mutate(edit)} saving={save.isPending} error={save.error} /> : <>
                <div className="mb-1 flex items-start justify-between gap-2">
                  <p className="text-xs text-muted">#{c.position + 1}{c.page ? ` · page ${c.page}` : ""} · {c.words} words</p>
                  {editable && <div className="flex gap-1">
                    <IconButton label="Edit this text" className="h-7 w-7" onClick={() => setEdit({ id: c.id, question: c.question ?? "", content: c.content })}><Pencil className="h-3.5 w-3.5" aria-hidden /></IconButton>
                    <IconButton label="Delete this text" className="h-7 w-7" onClick={async () => (await ask({ title: "Delete this text?", message: "The assistant will no longer use it.", confirmLabel: "Delete", danger: true })) && del.mutate(c.id)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></IconButton>
                  </div>}
                </div>
                {c.question && <p className="mb-1 text-sm font-semibold">{c.question}</p>}
                <p className="whitespace-pre-wrap break-words text-sm">{c.content}</p>
              </>}
            </li>
          ))}</ol>
          {!chunks.length && <Empty title="Nothing found">Try other words.</Empty>}
        </div>
      )}
    </Modal>
  );
}

function ChunkForm({ value, onChange, onSave, onCancel, saving, error }: { value: { question: string; content: string }; onChange: (v: any) => void; onSave: () => void; onCancel: () => void; saving: boolean; error: unknown }) {
  return (
    <form className="space-y-2 rounded-ctl bg-sunken/50 p-3" onSubmit={(e) => { e.preventDefault(); onSave(); }}>
      <Field label="Question it answers (optional)" hint="Helps the assistant match the right question.">{(i) => <Input id={i} persistDraft={false} value={value.question} onChange={(e) => onChange({ ...value, question: e.target.value })} />}</Field>
      <Field label="Text">{(i) => <Textarea id={i} persistDraft={false} required rows={7} value={value.content} onChange={(e) => onChange({ ...value, content: e.target.value })} />}</Field>
      <ErrorNote error={error} />
      <div className="flex justify-end gap-2"><Button onClick={onCancel}>Cancel</Button><Button type="submit" variant="primary" loading={saving} disabled={!value.content.trim()}>Save</Button></div>
    </form>
  );
}

/* ---------- saved AI answers (cache) ---------- */
const CACHE_TONE: Record<string, "neutral" | "ok" | "danger"> = { auto: "neutral", verified: "ok", blocked: "danger" };
const CACHE_LABEL: Record<string, string> = { auto: "Auto", verified: "Verified", blocked: "Blocked" };

function SavedAnswers({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<{ id: number; question: string; answer: string } | null>(null);
  const list = useQuery({ queryKey: ["cache", search, status], queryFn: () => api<any>("/api/cache", { params: { search, status, limit: 100 } }) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["cache"] });
  const put = useMutation({ mutationFn: (b: { id: number; status?: string; answer?: string }) => api(`/api/cache/${b.id}`, { method: "PUT", body: { status: b.status, answer: b.answer } }), onSuccess: () => { toast("Saved"); setEditing(null); refresh(); } });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/cache/${id}`, { method: "DELETE" }), onSuccess: () => { toast("Removed"); refresh(); } });
  const clear = useMutation({ mutationFn: () => api("/api/cache/clear", { method: "POST" }), onSuccess: () => { toast("Cleared. Verified answers are kept."); refresh(); } });
  return (
    <>
      <p className="mb-3 max-w-3xl text-sm text-muted">AI answers are reused when the same question comes again. <b>Verify</b> a good one to keep it; <b>Block</b> a wrong one so it is never sent again.</p>
      <Toolbar>
        <SearchBox label="Search question or answer" value={search} onChange={setSearch} />
        <Select aria-label="Status" persistDraft={false} className="w-auto" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option><option value="auto">Auto</option><option value="verified">Verified</option><option value="blocked">Blocked</option></Select>
        {admin && <Button variant="ghost" onClick={async () => (await ask({ title: "Clear saved answers?", message: "Every automatic saved answer stops being used and is rebuilt from the knowledge base. Verified answers are kept.", confirmLabel: "Clear", danger: true })) && clear.mutate()} loading={clear.isPending}><RefreshCw className="h-4 w-4" aria-hidden />Clear all</Button>}
      </Toolbar>
      {list.isLoading ? <Spinner /> : !list.data?.items.length ? <Empty title="No saved answers">Answers written by the AI appear here once they are reused.</Empty> : (
        <TableWrap><thead><tr><Th>Question</Th><Th>Answer</Th><Th className="text-right">Used</Th><Th>Status</Th><Th><span className="sr-only">Actions</span></Th></tr></thead>
          <tbody>{list.data.items.map((c: any) => (
            <tr key={c.id} className={c.status === "blocked" ? "opacity-60" : ""}>
              <Td className="max-w-[220px] font-semibold">{c.question}</Td>
              <Td className="max-w-[380px] whitespace-pre-wrap break-words text-muted">{c.answer}</Td>
              <Td className="text-right tabular-nums">{c.hits}</Td>
              <Td><Badge tone={CACHE_TONE[c.status]}>{CACHE_LABEL[c.status]}</Badge>{c.stale && <p className="mt-1 text-xs text-muted">Not used: knowledge changed</p>}</Td>
              <Td>{admin && <div className="flex justify-end gap-1">
                {c.status !== "verified" && <IconButton label="Verify this answer" onClick={() => put.mutate({ id: c.id, status: "verified" })}><CheckCircle2 className="h-4 w-4" aria-hidden /></IconButton>}
                {c.status !== "blocked" && <IconButton label="Block this answer" onClick={() => put.mutate({ id: c.id, status: "blocked" })}><Ban className="h-4 w-4" aria-hidden /></IconButton>}
                <IconButton label="Edit this answer" onClick={() => setEditing({ id: c.id, question: c.question, answer: c.answer })}><Pencil className="h-4 w-4" aria-hidden /></IconButton>
                <IconButton label="Delete this answer" onClick={async () => (await ask({ title: "Delete this saved answer?", message: c.question, confirmLabel: "Delete", danger: true })) && del.mutate(c.id)}><Trash2 className="h-4 w-4" aria-hidden /></IconButton>
              </div>}</Td>
            </tr>
          ))}</tbody></TableWrap>
      )}
      {editing && <Modal wide title="Edit saved answer" onClose={() => setEditing(null)}>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); put.mutate({ id: editing.id, answer: editing.answer }); }}>
          <p className="rounded-ctl bg-sunken px-3 py-2 text-sm"><span className="font-semibold">Question: </span>{editing.question}</p>
          <Field label="Answer" hint="Saving marks it Verified.">{(i) => <Textarea id={i} persistDraft={false} required rows={6} value={editing.answer} onChange={(e) => setEditing({ ...editing, answer: e.target.value })} />}</Field>
          <ErrorNote error={put.error} />
          <div className="flex justify-end gap-2"><Button onClick={() => setEditing(null)}>Cancel</Button><Button type="submit" variant="primary" loading={put.isPending}>Save and verify</Button></div>
        </form>
      </Modal>}
    </>
  );
}

/* ---------- search test (keyword index) ---------- */
const TYPES: Record<string, string> = { chunk: "File text", qa: "Q&A", variable: "Variables", program: "Programs" };

function SearchTest({ admin }: { admin: boolean }) {
  const stats = useQuery({ queryKey: ["kw-stats"], queryFn: () => api<any>("/api/keywords/stats") });
  const [q, setQ] = useState("");
  const [useAi, setUseAi] = useState(false);
  const test = useMutation({ mutationFn: () => api<any>("/api/keywords/search", { params: { q, ai: useAi } }) });
  const st = stats.data;
  return (
    <>
      <p className="mb-3 max-w-3xl text-sm text-muted">Type a question the way a guest would. You will see which records the assistant finds for it. If nothing is found, add a Q&A or a document that covers it.</p>
      {admin ? (
        <form className="mb-5 space-y-2" onSubmit={(e) => { e.preventDefault(); if (q.trim()) test.mutate(); }}>
          <Toolbar>
            <Input aria-label="Try a question" persistDraft={false} placeholder="e.g. Course e apply korte ki lagbe?" className="min-w-[240px] flex-1" value={q} onChange={(e) => setQ(e.target.value)} />
            <Button type="submit" variant="primary" loading={test.isPending} disabled={!q.trim()}><Search className="h-4 w-4" aria-hidden />Test</Button>
          </Toolbar>
          <Switch checked={useAi} onChange={setUseAi} label="Also ask the AI for keywords (uses a few tokens)" />
          <ErrorNote error={test.error} />
        </form>
      ) : <p className="mb-5 text-sm text-muted">Only admins can run a test.</p>}

      {test.data && (
        <section className="mb-5 space-y-2">
          <p className="text-sm"><span className="font-semibold">Keywords:</span> {test.data.debug.keywords?.join(" · ") || "–"} <span className="text-muted">· coverage {Math.round((test.data.debug.coverage ?? 0) * 100)}%</span></p>
          {test.data.records.map((r: any, i: number) => (
            <div key={i} className="rounded-ctl border border-line bg-surface p-3 text-sm">
              <div className="mb-1 flex flex-wrap items-center gap-2"><Badge tone="accent">{Math.round(r.coverage * 100)}%</Badge><span className="font-semibold">{r.document}</span>{r.question && <span className="text-muted">· {r.question}</span>}</div>
              <p className="text-muted">{r.text.slice(0, 240)}</p>
            </div>
          ))}
          {test.data.extra.map((e: string, i: number) => <div key={`e${i}`} className="rounded-ctl border border-line bg-surface p-3 text-sm text-muted">{e.slice(0, 240)}</div>)}
          {!test.data.records.length && !test.data.extra.length && <Empty title="Nothing found">The assistant would say it does not know. Add a Q&A for this question.</Empty>}
        </section>
      )}

      {st && (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {[...Object.entries(TYPES).map(([k, label]) => [label, st.records?.[k] ?? 0]), ["Keywords", st.keywords]].map(([k, v]) => (
            <div key={String(k)} className="rounded-ctl bg-sunken/60 px-3 py-2"><dt className="text-xs text-muted">{k}</dt><dd className="text-lg font-bold tabular-nums">{v}</dd></div>
          ))}
        </dl>
      )}
    </>
  );
}
