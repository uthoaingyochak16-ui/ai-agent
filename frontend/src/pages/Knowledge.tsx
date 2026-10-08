import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CloudUpload, FileText, Pencil, Plus, RefreshCw, Search, Trash2, Upload, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { Badge, Button, Empty, ErrorNote, Field, IconButton, Input, Modal, PageHeader, Panel, Spinner, TableWrap, Td, Textarea, Th, cx, useToast, useConfirm } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago } from "../lib/format";

const TONE: Record<string, "neutral" | "warn" | "ok" | "danger"> = { pending: "neutral", processing: "warn", ready: "ok", failed: "danger", superseded: "neutral" };
const LABEL: Record<string, string> = { pending: "Waiting", processing: "Processing", ready: "Ready", failed: "Failed", superseded: "Replaced by a newer version" };
const FORMATS = ["PDF", "Word", "Excel", "PowerPoint", "CSV", "JSON", "TXT", "HTML", "RTF", "EPUB", "ZIP", "Images (OCR)"];
const MAX_MB = 50;
const size = (b: number) => (b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

export default function Knowledge() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [preview, setPreview] = useState<number | null>(null);
  const docs = useQuery({ queryKey: ["docs"], queryFn: () => api<any[]>("/api/documents"), refetchInterval: (q) => (q.state.data?.some((d: any) => d.status === "pending" || d.status === "processing") ? 2000 : false) });
  const re = useMutation({ mutationFn: (id: number) => api(`/api/documents/${id}/reprocess`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["docs"] }) });
  const reAll = useMutation({ mutationFn: () => api<{ queued: number }>("/api/documents/reprocess-all", { method: "POST" }), onSuccess: (r) => { toast(`${r.queued} document(s) are being processed again`); qc.invalidateQueries({ queryKey: ["docs"] }); } });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/documents/${id}`, { method: "DELETE" }), onSuccess: () => { toast("Document deleted"); qc.invalidateQueries({ queryKey: ["docs"] }); } });
  return (
    <>
      <PageHeader title="Knowledge base" subtitle="The assistant answers from these documents, the Q&A pairs and your variables. Re-uploading a document with the same title replaces the old version."
        actions={admin && !!docs.data?.length && <Button onClick={async () => (await ask({ title: "Process all documents again?", message: "Every document is cut into chunks again with the current chunk size. Manual chunk edits are replaced.", confirmLabel: "Process again" })) && reAll.mutate()} loading={reAll.isPending}><RefreshCw className="h-4 w-4" aria-hidden />Process all again</Button>} />
      {admin && <UploadPanel onDone={() => qc.invalidateQueries({ queryKey: ["docs"] })} />}
      {docs.isLoading ? <Spinner /> : !docs.data?.length ? <Empty title="No documents yet">Upload your price list, policies or FAQ so the assistant has something to answer from.</Empty> : (
        <TableWrap><thead><tr><Th>Title</Th><Th>Type</Th><Th>Version</Th><Th>Status</Th><Th className="text-right">Chunks</Th><Th>Added</Th><Th><span className="sr-only">Actions</span></Th></tr></thead>
          <tbody>{docs.data.map((d) => (
            <tr key={d.id}><Td className="font-semibold">{d.title}</Td><Td>{d.type.toUpperCase()}</Td><Td>{d.version}</Td>
              <Td><Badge tone={TONE[d.status]}>{LABEL[d.status]}</Badge>{d.error && <p className="mt-1 max-w-xs text-xs text-danger">{d.error}</p>}</Td><Td className="text-right tabular-nums">{d.chunk_count}</Td><Td className="text-muted">{ago(d.created_at)}</Td>
              <Td><div className="flex justify-end gap-1">
                <Button size="sm" disabled={d.status !== "ready"} onClick={() => setPreview(d.id)}>{admin ? <><Pencil className="h-3.5 w-3.5" aria-hidden />View and edit</> : <><FileText className="h-3.5 w-3.5" aria-hidden />Chunks</>}</Button>
                {admin && <IconButton label="Process again" onClick={() => re.mutate(d.id)}><RefreshCw className="h-4 w-4" aria-hidden /></IconButton>}
                {admin && <IconButton label={`Delete ${d.title}`} onClick={async () => (await ask({ title: `Delete "${d.title}"?`, message: "The file and all its chunks are removed. The assistant will no longer answer from it. This cannot be undone.", confirmLabel: "Delete", danger: true })) && del.mutate(d.id)}><Trash2 className="h-4 w-4" aria-hidden /></IconButton>}</div></Td></tr>
          ))}</tbody></TableWrap>
      )}
      {preview && <ChunkEditor id={preview} editable={admin} onClose={() => { setPreview(null); qc.invalidateQueries({ queryKey: ["docs"] }); }} />}
    </>
  );
}

function UploadPanel({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [over, setOver] = useState(false);
  const tooBig = !!picked && picked.size > MAX_MB * 1024 * 1024;
  const upload = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append("file", picked!); if (title.trim()) fd.append("title", title.trim()); return api("/api/documents", { form: fd }); },
    onSuccess: () => { toast("Uploaded. Processing in the background."); setPicked(null); setTitle(""); if (input.current) input.current.value = ""; onDone(); },
  });
  const choose = (f: File | undefined) => { if (f) { setPicked(f); upload.reset(); } };
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); choose(e.dataTransfer.files?.[0]); };
  return (
    <Panel title="Upload a document" className="mb-5">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (picked && !tooBig) upload.mutate(); }}>
        <input ref={input} type="file" className="sr-only" aria-label="Choose a file" onChange={(e) => choose(e.target.files?.[0])} />
        {!picked ? (
          <button type="button" onClick={() => input.current?.click()} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}
            className={cx("flex w-full items-center gap-3 rounded-ctl border border-dashed px-3 py-2 text-left transition-colors", over ? "border-accent bg-accent-soft" : "border-line hover:border-accent hover:bg-sunken/60")}> 
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-ctl bg-accent-soft text-accent-strong"><CloudUpload className="h-4 w-4" aria-hidden /></span>
            <span className="min-w-0 flex-1"><span className="block text-sm font-semibold">Drop a file or browse</span><span className="block truncate text-xs text-muted">{FORMATS.join(", ")} · max {MAX_MB} MB</span></span>
            <span className="shrink-0 rounded-ctl border border-line bg-surface px-2.5 py-1 text-xs font-semibold text-accent-strong">Browse</span>
          </button>
        ) : (
          <div className={cx("flex items-center gap-2 rounded-ctl border px-3 py-2", tooBig ? "border-danger bg-danger-soft" : "border-line bg-sunken/50")}>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-ctl bg-accent-soft text-accent-strong"><FileText className="h-4 w-4" aria-hidden /></span>
            <div className="min-w-0 flex-1"><p className="truncate font-semibold">{picked.name}</p><p className={cx("text-sm", tooBig ? "text-danger" : "text-muted")}>{size(picked.size)}{tooBig && ` - too large (max ${MAX_MB} MB)`}</p></div>
            <IconButton label="Choose another file" onClick={() => { setPicked(null); if (input.current) input.current.value = ""; }}><X className="h-4 w-4" aria-hidden /></IconButton>
          </div>
        )}
        <div className="grid items-end gap-2 sm:grid-cols-[minmax(0,420px)_auto]">
          <Field label="Title (optional)" hint="Defaults to the file name. Same title = new version that replaces the old one.">{(i) => <Input id={i} persistDraft={false} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={picked ? picked.name.replace(/\.[^.]+$/, "") : ""} />}</Field>
          <Button size="sm" type="submit" variant="primary" disabled={!picked || tooBig} loading={upload.isPending} className="sm:mb-5"><Upload className="h-3.5 w-3.5" aria-hidden />Upload</Button>
        </div>
        <ErrorNote error={upload.error} />
        <p className="text-xs text-muted">Tip: for question-and-answer files (CSV, Excel or JSON with "question" and "answer" columns), every pair becomes its own small chunk, so answers stay accurate. Audio and video are not supported.</p>
      </form>
    </Panel>
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
  const del = useMutation({ mutationFn: (cid: number) => api(`/api/documents/${id}/chunks/${cid}`, { method: "DELETE" }), onSuccess: () => { toast("Chunk removed"); refresh(); } });
  const f = find.trim().toLowerCase();
  const chunks = (q.data?.chunks ?? []).filter((c: any) => !f || c.content.toLowerCase().includes(f) || (c.question ?? "").toLowerCase().includes(f));
  return (
    <Modal wide title={q.data ? `"${q.data.document.title}"` : "Document"} onClose={onClose}>
      {q.isLoading ? <Spinner /> : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[200px] flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" aria-hidden /><Input aria-label="Search in this document" persistDraft={false} placeholder="Search in this document" className="pl-9" value={find} onChange={(e) => setFind(e.target.value)} /></div>
            {editable && <Button onClick={() => setEdit({ question: "", content: "" })}><Plus className="h-4 w-4" aria-hidden />Add text</Button>}
          </div>
          <p className="text-xs text-muted">{q.data.total} chunk(s){q.data.total > q.data.chunks.length ? `, showing the first ${q.data.chunks.length}` : ""}. Edits take effect immediately. “Process again” re-reads the original file and replaces manual edits.</p>
          {edit && !edit.id && <ChunkForm value={edit} onChange={setEdit} onCancel={() => setEdit(null)} onSave={() => save.mutate(edit)} saving={save.isPending} error={save.error} />}
          <ol className="max-h-[58vh] space-y-3 overflow-y-auto pr-1">{chunks.map((c: any) => (
            <li key={c.id} className="rounded-ctl border border-line p-3">
              {edit && edit.id === c.id ? <ChunkForm value={edit} onChange={setEdit} onCancel={() => setEdit(null)} onSave={() => save.mutate(edit)} saving={save.isPending} error={save.error} /> : <>
                <div className="mb-1 flex items-start justify-between gap-2">
                  <p className="text-xs text-muted">Chunk {c.position + 1}{c.page ? ` · page ${c.page}` : ""} · {c.words} words</p>
                  {editable && <div className="flex gap-1"><IconButton label="Edit this chunk" className="h-7 w-7" onClick={() => setEdit({ id: c.id, question: c.question ?? "", content: c.content })}><Pencil className="h-3.5 w-3.5" aria-hidden /></IconButton>
                    <IconButton label="Delete this chunk" className="h-7 w-7" onClick={async () => (await ask({ title: "Delete this chunk?", message: "The assistant will no longer use this text.", confirmLabel: "Delete", danger: true })) && del.mutate(c.id)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></IconButton></div>}
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
