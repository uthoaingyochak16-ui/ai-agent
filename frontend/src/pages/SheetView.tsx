import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Columns3, Download, Maximize2, Minimize2, Plus, Search, Trash2, Upload, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Badge, Button, Empty, ErrorNote, Field, IconButton, Input, Modal, Spinner, Switch, Textarea, useConfirm, useToast } from "../components/ui";
import { api, download } from "../lib/api";
import { useAuth } from "../lib/auth";

export type Sheet = { id: number; name: string; description: string | null; columns: { key: string; label: string }[]; active: boolean; rows: number };
type Row = { id: number; values: Record<string, string> };

export const TEMPLATES: { name: string; description: string; columns: string[] }[] = [
  { name: "Books", description: "Books published by Quantum Foundation: name, edition, author, publication, price and what each book is about.", columns: ["Name", "Edition", "Author", "Publication", "Price", "Description"] },
  { name: "Branches", description: "Our branches and centres: where they are, their phone numbers and opening hours.", columns: ["Branch", "District", "Address", "Phone", "Opening hours"] },
  { name: "Products", description: "Items for sale (CDs, posters, ...) with their price and where to get them.", columns: ["Name", "Type", "Price", "Where to get it", "Description"] },
  { name: "People", description: "Contact people for services (only what may be shared with guests).", columns: ["Name", "Role", "Service", "Phone"] },
];

/** One data sheet as an editable spreadsheet: the AI searches its rows when guests ask about what it holds. */
export default function SheetView({ sheet, onDeleted }: { sheet: Sheet; onDeleted: () => void }) {
  const admin = useAuth().user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [find, setFind] = useState("");
  const [q, setQ] = useState("");
  const [editingCols, setEditingCols] = useState(false);
  const [full, setFull] = useState(false);
  useEffect(() => {
    if (!full) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setFull(false);
    window.addEventListener("keydown", esc);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", esc); document.body.style.overflow = ""; };
  }, [full]);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { const t = setTimeout(() => setQ(find.trim()), 300); return () => clearTimeout(t); }, [find]);
  const rows = useQuery({ queryKey: ["sheet-rows", sheet.id, q], queryFn: () => api<{ items: Row[]; total: number }>(`/api/sheets/${sheet.id}/rows`, { params: { q, limit: 1000 } }) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["sheet-rows", sheet.id] }); qc.invalidateQueries({ queryKey: ["knowledge-overview"] }); };
  const saveSheet = useMutation({ mutationFn: (body: Record<string, unknown>) => api(`/api/sheets/${sheet.id}`, { method: "PUT", body }), onSuccess: () => { toast("Saved"); refresh(); } });
  const addRow = useMutation({ mutationFn: () => api(`/api/sheets/${sheet.id}/rows`, { body: { values: {} } }), onSuccess: refresh });
  const delRow = useMutation({ mutationFn: (id: number) => api(`/api/sheets/${sheet.id}/rows/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const del = useMutation({ mutationFn: () => api(`/api/sheets/${sheet.id}`, { method: "DELETE" }), onSuccess: () => { toast("Sheet deleted"); qc.invalidateQueries({ queryKey: ["knowledge-overview"] }); onDeleted(); } });
  const importFile = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); fd.append("sheet_id", String(sheet.id)); return api<any>("/api/sheets/import", { form: fd }); },
    onSuccess: (r) => { toast(`${r.added} row(s) added`); refresh(); if (file.current) file.current.value = ""; },
  });
  const [desc, setDesc] = useState(sheet.description ?? "");
  useEffect(() => setDesc(sheet.description ?? ""), [sheet.id, sheet.description]);

  return (
    <div className="space-y-4">
      <div className="rounded-panel border border-line bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-bold">{sheet.name}</h2>
            <p className="text-sm text-muted">{sheet.rows} row(s) · {sheet.columns.length} column(s)</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Switch label={sheet.active ? "The AI uses this sheet" : "The AI does not use it"} checked={sheet.active} disabled={!admin} onChange={(v) => saveSheet.mutate({ active: v })} />
          </div>
        </div>
        <Field label="What is this sheet for?" hint="The AI reads this to know when to look here. Say what one row is and what guests ask about it." className="mt-3">
          {(i) => <Textarea id={i} persistDraft={false} rows={2} disabled={!admin} value={desc} onChange={(e) => setDesc(e.target.value)}
                            onBlur={() => desc !== (sheet.description ?? "") && saveSheet.mutate({ description: desc })} />}
        </Field>
      </div>

      <div className={full ? "fixed inset-0 z-50 flex flex-col gap-3 overflow-hidden bg-bg p-4" : "space-y-4"}>
      <div className="flex flex-wrap items-center gap-2">
        {full && <h2 className="mr-2 text-lg font-bold">{sheet.name}</h2>}
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" aria-hidden />
          <Input aria-label="Search this sheet" persistDraft={false} className="pl-9" placeholder={`Search ${sheet.name}`} value={find} onChange={(e) => setFind(e.target.value)} />
        </div>
        <Button variant="primary" loading={addRow.isPending} onClick={() => addRow.mutate()}><Plus className="h-4 w-4" aria-hidden />Add row</Button>
        {admin && <><input ref={file} type="file" className="sr-only" accept=".csv,.tsv,.xlsx,.xlsm,.json" aria-label="Import rows from a file" onChange={(e) => e.target.files?.[0] && importFile.mutate(e.target.files[0])} />
          <Button onClick={() => file.current?.click()} loading={importFile.isPending} title="Excel or CSV whose first row holds the column names"><Upload className="h-4 w-4" aria-hidden />Import rows</Button>
          <Button onClick={() => setEditingCols(true)}><Columns3 className="h-4 w-4" aria-hidden />Columns</Button></>}
        <Button onClick={() => download(`/api/sheets/${sheet.id}/export`, `${sheet.name}.csv`).catch(() => toast("Export failed", "danger"))}><Download className="h-4 w-4" aria-hidden />Export</Button>
        <Button onClick={() => setFull(!full)} title={full ? "Back to the page (Esc)" : "Show the sheet on the whole screen"}>
          {full ? <><Minimize2 className="h-4 w-4" aria-hidden />Exit full screen</> : <><Maximize2 className="h-4 w-4" aria-hidden />Full screen</>}</Button>
      </div>
      <ErrorNote error={saveSheet.error || addRow.error || delRow.error || importFile.error || del.error} />

      {rows.isLoading ? <Spinner /> : !rows.data?.items.length ? (
        <Empty title={q ? "No row matches" : "No rows yet"}>{q ? "Try other words." : "Add rows one by one, or import an Excel / CSV file whose first row holds the column names."}</Empty>
      ) : (
        <div className={`overflow-auto rounded-panel border border-line bg-surface ${full ? "min-h-0 flex-1" : "max-h-[75vh]"}`}>
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-10 border-b border-line bg-sunken"><tr>
              <th scope="col" className="w-10 px-2 py-2 text-left text-xs font-semibold text-muted">#</th>
              {sheet.columns.map((c) => <th key={c.key} scope="col" className={`px-2 py-2 text-left text-xs font-semibold text-muted ${wide(c.label) ? "min-w-[320px]" : "min-w-[150px]"}`}>{c.label}</th>)}
              <th scope="col" className="w-10"><span className="sr-only">Delete</span></th>
            </tr></thead>
            <tbody>{rows.data.items.map((r, n) => (
              <RowEditor key={r.id} sheet={sheet} row={r} n={n + 1} onSaved={refresh}
                onDelete={async () => (await ask({ title: "Delete this row?", message: Object.values(r.values).slice(0, 3).join(" · ") || "(empty row)", confirmLabel: "Delete", danger: true })) && delRow.mutate(r.id)} />
            ))}</tbody>
          </table>
          {rows.data.total > rows.data.items.length && <p className="px-3 py-2 text-xs text-muted">Showing {rows.data.items.length} of {rows.data.total}: search to find the others.</p>}
        </div>
      )}
      </div>

      {admin && <div className="flex justify-end"><Button variant="ghost" onClick={async () => (await ask({ title: `Delete the sheet "${sheet.name}"?`, message: `All ${sheet.rows} row(s) are deleted and the AI stops using them. Export it first if you may need it.`, confirmLabel: "Delete sheet", danger: true })) && del.mutate()}>
        <Trash2 className="h-4 w-4" aria-hidden />Delete sheet</Button></div>}
      {editingCols && <ColumnsEditor sheet={sheet} onClose={() => setEditingCols(false)} onSaved={() => { setEditingCols(false); refresh(); }} />}
    </div>
  );
}

function RowEditor({ sheet, row, n, onSaved, onDelete }: { sheet: Sheet; row: Row; n: number; onSaved: () => void; onDelete: () => void }) {
  const [vals, setVals] = useState(row.values);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setVals(row.values); setDirty(false); }, [row]);
  const save = useMutation({ mutationFn: () => api(`/api/sheets/${sheet.id}/rows/${row.id}`, { method: "PUT", body: { values: vals } }), onSuccess: () => { setDirty(false); onSaved(); } });
  return (
    <tr className="border-t border-line align-top hover:bg-sunken/40">
      <td className="px-2 py-1.5 text-xs tabular-nums text-muted">{n}{save.isPending && "…"}</td>
      {sheet.columns.map((c) => (
        <td key={c.key} className="border-l border-line/60 px-1 py-1 first:border-l-0">
          <AutoText label={`${c.label}, row ${n}`} value={vals[c.key] ?? ""} error={save.isError}
            onChange={(v) => { setVals({ ...vals, [c.key]: v }); setDirty(true); }} onBlur={() => dirty && save.mutate()} />
        </td>
      ))}
      <td className="px-1 py-1"><IconButton label={`Delete row ${n}`} className="h-8 w-8" onClick={onDelete}><Trash2 className="h-3.5 w-3.5" aria-hidden /></IconButton></td>
    </tr>
  );
}

const wide = (label: string) => /desc|detail|about|note|address|summary|বিবরণ|ঠিকানা/i.test(label);

/** A cell that always shows all of its text: it grows with what is written, no clicking needed to read it. */
function AutoText({ label, value, error, onChange, onBlur }: { label: string; value: string; error: boolean; onChange: (v: string) => void; onBlur: () => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fit = () => { const t = ref.current; if (t) { t.style.height = "auto"; t.style.height = `${t.scrollHeight + 2}px`; } };
  useLayoutEffect(fit, [value]);
  useEffect(() => { window.addEventListener("resize", fit); return () => window.removeEventListener("resize", fit); }, []);
  return <textarea ref={ref} aria-label={label} rows={1} value={value} onChange={(e) => onChange(e.target.value)} onBlur={onBlur}
    className={`block w-full resize-none overflow-hidden whitespace-pre-wrap break-words rounded-ctl border bg-transparent px-2 py-1 text-sm leading-snug focus:border-accent focus:bg-surface focus:outline-none ${error ? "border-danger" : "border-transparent hover:border-line"}`} />;
}

function ColumnsEditor({ sheet, onClose, onSaved }: { sheet: Sheet; onClose: () => void; onSaved: () => void }) {
  const [cols, setCols] = useState(sheet.columns.map((c) => ({ ...c })));
  const [name, setName] = useState(sheet.name);
  const save = useMutation({ mutationFn: () => api(`/api/sheets/${sheet.id}`, { method: "PUT", body: { name, columns: cols.filter((c) => c.label.trim()) } }), onSuccess: onSaved });
  const removed = sheet.columns.filter((c) => !cols.some((x) => x.key === c.key));
  return (
    <Modal title="Sheet name and columns" onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Sheet name">{(i) => <Input id={i} persistDraft={false} required value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <div className="space-y-2">{cols.map((c, i) => (
          <div key={c.key || `new-${i}`} className="flex gap-2">
            <Input aria-label={`Column ${i + 1}`} persistDraft={false} value={c.label} onChange={(e) => setCols(cols.map((x, k) => k === i ? { ...x, label: e.target.value } : x))} />
            <IconButton label={`Remove column ${c.label}`} onClick={() => setCols(cols.filter((_, k) => k !== i))}><X className="h-4 w-4" aria-hidden /></IconButton>
          </div>
        ))}</div>
        <Button size="sm" onClick={() => setCols([...cols, { key: "", label: "" }])}><Plus className="h-4 w-4" aria-hidden />Add column</Button>
        <p className="text-xs text-muted">The first column is what one row is called (e.g. the book's name). Renaming a column keeps its values.</p>
        {removed.length > 0 && <p className="rounded-ctl bg-danger-soft px-3 py-2 text-sm text-danger">Removing {removed.map((c) => `"${c.label}"`).join(", ")} deletes its values in every row.</p>}
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={save.isPending}>Save</Button></div>
      </form>
    </Modal>
  );
}

/** A new sheet: from a template, from scratch, or from an Excel / CSV file. */
export function NewSheet({ onClose, onCreated }: { onClose: () => void; onCreated: (s: Sheet) => void }) {
  const [tpl, setTpl] = useState(0);
  const [name, setName] = useState(TEMPLATES[0].name);
  const [description, setDescription] = useState(TEMPLATES[0].description);
  const [cols, setCols] = useState(TEMPLATES[0].columns.join(", "));
  const [fileName, setFileName] = useState<File | null>(null);
  const pick = (i: number) => {
    setTpl(i);
    const t = TEMPLATES[i];
    setName(t?.name ?? ""); setDescription(t?.description ?? ""); setCols(t?.columns.join(", ") ?? "");
  };
  const create = useMutation({
    mutationFn: () => {
      if (fileName) {
        const fd = new FormData(); fd.append("file", fileName); if (name.trim()) fd.append("name", name.trim()); if (description.trim()) fd.append("description", description.trim());
        return api<any>("/api/sheets/import", { form: fd }).then((r) => r.sheet as Sheet);
      }
      return api<Sheet>("/api/sheets", { body: { name, description, columns: cols.split(",").map((x) => x.trim()).filter(Boolean).map((label) => ({ label })) } });
    },
    onSuccess: onCreated,
  });
  return (
    <Modal wide title="New data sheet" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <div>
          <p className="mb-2 text-sm font-semibold">Start from</p>
          <div className="grid gap-2 sm:grid-cols-3">
            {[...TEMPLATES.map((t) => t.name), "Blank sheet"].map((label, i) => (
              <button key={label} type="button" onClick={() => pick(i)} aria-pressed={tpl === i}
                className={`rounded-ctl border px-3 py-2 text-left text-sm ${tpl === i ? "border-accent bg-accent-soft font-semibold" : "border-line hover:bg-sunken"}`}>{label}</button>
            ))}
          </div>
        </div>
        <Field label="Name">{(i) => <Input id={i} persistDraft={false} required={!fileName} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Books" />}</Field>
        <Field label="What is it for?" hint="The AI reads this to know when to look in the sheet.">{(i) => <Textarea id={i} persistDraft={false} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
        {!fileName && <Field label="Columns" hint="Separated by commas. The first one names a row (e.g. the book's name).">{(i) => <Input id={i} persistDraft={false} required value={cols} onChange={(e) => setCols(e.target.value)} />}</Field>}
        <div className="rounded-ctl border border-dashed border-line p-3 text-sm">
          <label className="flex flex-wrap items-center gap-2"><Upload className="h-4 w-4 text-muted" aria-hidden /><span>Or import an Excel / CSV file (its first row = the columns):</span>
            <input type="file" accept=".csv,.tsv,.xlsx,.xlsm,.json" onChange={(e) => setFileName(e.target.files?.[0] ?? null)} /></label>
          {fileName && <p className="mt-1 flex items-center gap-1 text-muted"><Badge>{fileName.name}</Badge> the columns come from the file<button type="button" className="ml-1 underline" onClick={() => setFileName(null)}>remove</button></p>}
        </div>
        <p className="flex items-center gap-1 text-xs text-muted"><Bot className="h-3.5 w-3.5" aria-hidden />As soon as it has rows, the AI can answer from it (e.g. "Sukhi Paribar boi er dam koto?").</p>
        <ErrorNote error={create.error} />
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={create.isPending}>Create sheet</Button></div>
      </form>
    </Modal>
  );
}
