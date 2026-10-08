import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Download, GitMerge, MessageSquare, Pencil, Plus, Trash2, Undo2, UserRound, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Avatar, Badge, Button, ChannelBadge, Empty, ErrorNote, Field, IconButton, Input, Modal, Panel, Select, Spinner, Tabs, useToast, useConfirm } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { when } from "../lib/format";
import { ProfileForm } from "./Profiles";

const CONSENT: Record<string, ["ok" | "warn" | "danger" | "neutral", string]> = { given: ["ok", "Consent given"], asked: ["warn", "Consent asked"], declined: ["danger", "Declined"], unknown: ["neutral", "Consent not asked"] };
// core profile columns, shown inside the same groups as the extra details
const CORE: { key: string; label: string; group: string }[] = [
  { key: "name", label: "Name", group: "basic" }, { key: "age", label: "Age", group: "basic" }, { key: "language", label: "Language", group: "basic" },
  { key: "phone", label: "Phone", group: "contact" }, { key: "email", label: "Email", group: "contact" }, { key: "location", label: "Location", group: "contact" },
  { key: "profession", label: "Profession", group: "work" },
];
type Tab = "overview" | "edit" | "history" | "admin";

export default function ProfileDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("overview");
  const p = useQuery({ queryKey: ["profile", id], queryFn: () => api(`/api/profiles/${id}`) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["profile", id] }); qc.invalidateQueries({ queryKey: ["profile-msgs", id] }); qc.invalidateQueries({ queryKey: ["profiles"] }); };
  const exportJson = async () => {
    const d = await api(`/api/profiles/${id}/export`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }));
    a.download = `profile-${id}.json`; a.click(); URL.revokeObjectURL(a.href);
  };
  if (p.isLoading) return <Spinner />;
  const d = p.data;
  if (!d) return <ErrorNote error={p.error} />;
  const erased = !!d.erased_at;
  const locked = erased || !!d.merged_into_id;
  const title = d.display_name || `Guest ${d.id}`;
  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-2"><Link to="/profiles" className="text-sm font-semibold text-accent-strong underline">All profiles</Link>
        {isAdmin && <Button size="sm" onClick={exportJson}><Download className="h-4 w-4" aria-hidden />Export JSON</Button>}</div>

      {/* who is this, at a glance */}
      <section className="mb-5 flex flex-wrap items-center gap-4 rounded-panel border border-line bg-surface p-4">
        <Avatar name={title} src={d.avatar_url} size={64} />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-2xl font-bold tracking-tight">{title}</h1>
          <p className="text-sm text-muted">{d.name && d.channel_name && d.channel_name !== d.name ? `Chat name: ${d.channel_name} · ` : ""}Profile {d.id} · since {when(d.created_at)}</p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {d.channels.map((c: string) => <ChannelBadge key={c} channel={c} />)}
            <Badge tone={CONSENT[d.consent][0]}>{CONSENT[d.consent][1]}</Badge>
            {d.phone && <Badge>{d.phone}</Badge>}{d.email && <Badge>{d.email}</Badge>}{d.location && <Badge>{d.location}</Badge>}
          </div>
        </div>
        {d.conversations.length > 0 && <Link to={`/inbox?conv=${d.conversations[0].id}`} className="inline-flex items-center gap-2 rounded-ctl border border-line px-3.5 py-2 text-sm font-semibold hover:bg-sunken"><MessageSquare className="h-4 w-4" aria-hidden />Open chat</Link>}
      </section>

      {d.merged_into_id && <p className="mb-4 rounded-ctl bg-warn-soft px-3 py-2 text-sm text-warn">This profile was merged into <Link className="font-semibold underline" to={`/profiles/${d.merged_into_id}`}>profile {d.merged_into_id}</Link>.</p>}
      {erased && <p className="mb-4 rounded-ctl bg-danger-soft px-3 py-2 text-sm text-danger">Personal data was deleted on {when(d.erased_at)}.</p>}
      {d.merge_suggestion && <p className="mb-4 rounded-ctl bg-warn-soft px-3 py-2 text-sm text-warn">This person shared a phone number or email that belongs to <Link className="font-semibold underline" to={`/profiles/${d.merge_suggestion}`}>profile {d.merge_suggestion}</Link>. Review and merge if they are the same person.</p>}

      <Tabs value={tab} onChange={setTab} tabs={[{ id: "overview", label: "Overview" }, { id: "edit", label: "Edit main details" }, { id: "history", label: "Chat history" }, ...(isAdmin ? [{ id: "admin" as Tab, label: "Merge and privacy" }] : [])]} />
      {tab === "overview" && <Overview d={d} locked={locked} onChange={refresh} />}
      {tab === "edit" && <Panel title="Main details">{locked ? <p className="text-sm text-muted">Editing is disabled.</p> : <ProfileForm initial={d} onSaved={() => { toast("Saved"); refresh(); }} />}</Panel>}
      {tab === "history" && <History id={id!} />}
      {tab === "admin" && isAdmin && <Admin d={d} id={id!} onChange={refresh} />}
    </>
  );
}

function Overview({ d, locked, onChange }: { d: any; locked: boolean; onChange: () => void }) {
  const toast = useToast();
  const ask = useConfirm();
  const catalog = useQuery({ queryKey: ["profile-fields"], queryFn: () => api<any>("/api/profiles/fields"), staleTime: Infinity });
  const [edit, setEdit] = useState<{ key: string; custom: string; value: string } | null>(null);
  const save = useMutation({
    mutationFn: (b: { key: string; value: string }) => api(`/api/profiles/${d.id}/facts`, { method: "PUT", body: b }),
    onSuccess: () => { toast("Saved"); setEdit(null); onChange(); },
  });
  const del = useMutation({ mutationFn: (key: string) => api(`/api/profiles/${d.id}/facts/${key}`, { method: "DELETE" }), onSuccess: () => { toast("Removed"); onChange(); } });
  const groups: { id: string; label: string }[] = catalog.data?.groups ?? [];
  const fields: any[] = catalog.data?.fields ?? [];
  const rows = useMemo(() => {
    const out: Record<string, { key: string; label: string; value: string; source?: string; core?: boolean; updated_at?: string }[]> = {};
    for (const c of CORE) if (d[c.key] != null && d[c.key] !== "") (out[c.group] ??= []).push({ key: c.key, label: c.label, value: String(d[c.key]), core: true });
    for (const f of d.facts) (out[f.group] ??= []).push({ key: f.key, label: f.label, value: f.value, source: f.source, updated_at: f.updated_at });
    return out;
  }, [d]);
  const filled = groups.filter((g) => rows[g.id]?.length);
  const key = edit ? (edit.key === "__custom" ? edit.custom : edit.key) : "";
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-5">
        {!locked && (
          <Panel title={edit && d.facts.some((f: any) => f.key === edit.key) ? "Edit information" : "Add information"}>
            <form className="grid items-end gap-3 sm:grid-cols-[220px_1fr_auto]" onSubmit={(e) => { e.preventDefault(); if (edit && key && edit.value.trim()) save.mutate({ key, value: edit.value.trim() }); }}>
              <Field label="What">{(i) => (
                <Select id={i} persistDraft={false} value={edit?.key ?? ""} onChange={(e) => setEdit({ key: e.target.value, custom: "", value: d.facts.find((f: any) => f.key === e.target.value)?.value ?? "" })}>
                  <option value="" disabled>Choose…</option>
                  {groups.filter((g) => fields.some((f) => f.group === g.id)).map((g) => <optgroup key={g.id} label={g.label}>{fields.filter((f) => f.group === g.id).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}</optgroup>)}
                  <option value="__custom">Something else…</option>
                </Select>)}</Field>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,180px)_1fr]">
                {edit?.key === "__custom" ? <Field label="Name of the detail">{(i) => <Input id={i} persistDraft={false} required placeholder="e.g. blood group" value={edit.custom} onChange={(e) => setEdit({ ...edit, custom: e.target.value })} />}</Field> : <span className="hidden sm:block" />}
                <Field label="Value" className={edit?.key === "__custom" ? "" : "sm:col-span-2"}>{(i) => <Input id={i} persistDraft={false} required disabled={!edit} value={edit?.value ?? ""} onChange={(e) => edit && setEdit({ ...edit, value: e.target.value })} />}</Field>
              </div>
              <div className="flex gap-2">{edit && <Button onClick={() => setEdit(null)}>Cancel</Button>}<Button type="submit" variant="primary" disabled={!edit || !key || !edit.value.trim()} loading={save.isPending}><Plus className="h-4 w-4" aria-hidden />Save</Button></div>
            </form>
            <div className="mt-2"><ErrorNote error={save.error} /></div>
            <p className="mt-2 text-xs text-muted">The AI also fills these in from the chat (marked <Bot className="inline h-3 w-3" aria-hidden /> AI). Anything staff enters is kept: the AI never overwrites it.</p>
          </Panel>
        )}
        {catalog.isLoading ? <Spinner /> : !filled.length ? <Empty title="Nothing known yet">Details appear here as the guest shares them in chat, or when you add them above.</Empty> : (
          <div className="grid gap-4 md:grid-cols-2">
            {filled.map((g) => (
              <Panel key={g.id} title={g.label}>
                <dl className="divide-y divide-line">
                  {rows[g.id].map((r) => (
                    <div key={r.key} className="group flex items-start gap-2 py-2 first:pt-0 last:pb-0">
                      <div className="min-w-0 flex-1">
                        <dt className="text-xs font-semibold text-muted">{r.label}</dt>
                        <dd className="whitespace-pre-wrap break-words">{r.value}</dd>
                      </div>
                      {r.source === "ai" && <Badge tone="accent" className="mt-0.5"><Bot className="h-3 w-3" aria-hidden />AI</Badge>}
                      {r.source === "staff" && <Badge tone="blue" className="mt-0.5"><UserRound className="h-3 w-3" aria-hidden />Staff</Badge>}
                      {!locked && !r.core && <>
                        <IconButton label={`Edit ${r.label}`} className="h-7 w-7" onClick={() => setEdit({ key: r.key, custom: "", value: r.value })}><Pencil className="h-3.5 w-3.5" aria-hidden /></IconButton>
                        <IconButton label={`Remove ${r.label}`} className="h-7 w-7" onClick={async () => (await ask({ title: `Remove "${r.label}"?`, message: "This detail is deleted from the profile.", confirmLabel: "Remove", danger: true })) && del.mutate(r.key)}><X className="h-3.5 w-3.5" aria-hidden /></IconButton>
                      </>}
                    </div>
                  ))}
                </dl>
                {g.id !== "other" && rows[g.id].some((r) => r.core) && <p className="mt-2 text-xs text-muted">Name, age, language, phone, email, location and profession are edited in “Edit main details”.</p>}
              </Panel>
            ))}
          </div>
        )}
      </div>
      <div className="space-y-5">
        <Panel title="What the AI knows">
          {d.summary ? <p className="text-sm">{d.summary}</p> : <p className="text-sm text-muted">Nothing yet.</p>}
          <p className="mt-2 text-xs text-muted">This short summary is all the AI sees of the profile. Phone, email and staff notes are never sent.</p>
          {d.pending_fields.length > 0 && <p className="mt-3 rounded-ctl bg-warn-soft px-3 py-2 text-sm text-warn">Waiting for consent before saving: {d.pending_fields.join(", ")} (held encrypted).</p>}
        </Panel>
        <Panel title="Chat accounts">
          {d.identities.length === 0 ? <p className="text-sm text-muted">None.</p> : <ul className="space-y-3">{d.identities.map((i: any) => (
            <li key={i.id} className="flex items-center gap-2.5"><Avatar name={i.display_name} src={i.avatar_url} size={32} />
              <div className="min-w-0 flex-1"><p className="truncate font-semibold">{i.display_name || "Unknown name"}{i.is_test && <Badge className="ml-1">Test</Badge>}</p><p className="text-xs text-muted">last wrote {when(i.last_user_message_at)}</p></div>
              <ChannelBadge channel={i.channel} /></li>))}</ul>}
        </Panel>
      </div>
    </div>
  );
}

function History({ id }: { id: string }) {
  const hist = useQuery({ queryKey: ["profile-msgs", id], queryFn: () => api<any[]>(`/api/profiles/${id}/messages`) });
  return (
    <Panel title="Chat history across channels">
      {hist.isLoading ? <Spinner /> : !hist.data?.length ? <Empty title="No messages">Chats older than the retention period are deleted automatically; the details above are kept.</Empty> : (
        <ol className="max-h-[640px] space-y-2 overflow-y-auto pr-1">{hist.data.map((m) => (
          <li key={m.id} className="rounded-ctl bg-sunken/60 px-3 py-2"><p className="mb-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted"><ChannelBadge channel={m.channel} /><span className="font-semibold">{m.role === "user" ? "Guest" : m.role === "agent" ? "Staff" : "AI"}</span>{when(m.created_at)}{m.delivery_status === "blocked" && <Badge tone="danger">Not sent</Badge>}</p><p className="whitespace-pre-wrap break-words">{m.content}</p></li>
        ))}</ol>
      )}
    </Panel>
  );
}

function Admin({ d, id, onChange }: { d: any; id: string; onChange: () => void }) {
  const toast = useToast();
  const nav = useNavigate();
  const [otherId, setOtherId] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const erased = !!d.erased_at;
  const merge = useMutation({ mutationFn: () => api("/api/profiles/merge", { body: { keep_id: Number(id), merge_id: Number(otherId) } }), onSuccess: () => { toast("Profiles merged"); setOtherId(""); onChange(); } });
  const unmerge = useMutation({ mutationFn: (lid: number) => api<{ warnings: string[] }>(`/api/profiles/merge-log/${lid}/unmerge`, { method: "POST" }), onSuccess: (r) => { toast(r.warnings.length ? `Unmerged with notes: ${r.warnings.join("; ")}` : "Unmerged"); onChange(); } });
  const del = useMutation({ mutationFn: () => api(`/api/profiles/${id}`, { method: "DELETE" }), onSuccess: () => { toast("Personal data deleted"); setConfirmDel(false); onChange(); } });
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {!erased && !d.merged_into_id && (
        <Panel title="Merge duplicates">
          <p className="mb-3 text-sm text-muted">Merge another profile into this one. Newest values win; nothing is deleted, so you can undo it below.</p>
          <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); merge.mutate(); }}>
            <Field label="Other profile ID" className="flex-1">{(i) => <Input id={i} inputMode="numeric" required value={otherId} onChange={(e) => setOtherId(e.target.value.replace(/\D/g, ""))} />}</Field>
            <Button type="submit" loading={merge.isPending}><GitMerge className="h-4 w-4" aria-hidden />Merge in</Button>
          </form>
          <div className="mt-2"><ErrorNote error={merge.error} /></div>
          {d.merges.length > 0 && <ul className="mt-4 divide-y divide-line text-sm">{d.merges.map((m: any) => (
            <li key={m.id} className="flex items-center justify-between gap-2 py-2"><span>Profile {m.merged_profile_id} merged by {m.actor} ({m.reason}), {when(m.created_at)}</span>
              {m.reverted_at ? <Badge>Undone</Badge> : <Button size="sm" loading={unmerge.isPending} onClick={() => unmerge.mutate(m.id)}><Undo2 className="h-3.5 w-3.5" aria-hidden />Unmerge</Button>}</li>))}</ul>}
        </Panel>
      )}
      {!erased && (
        <Panel title="Delete personal data">
          <p className="mb-3 text-sm text-muted">Removes the name, contact details, all saved information, merge history and message text for this person and everyone merged into them. The chat can continue anonymously.</p>
          <Button variant="danger" onClick={() => setConfirmDel(true)}><Trash2 className="h-4 w-4" aria-hidden />Delete personal data</Button>
        </Panel>
      )}
      {confirmDel && <Modal title="Delete personal data?" onClose={() => setConfirmDel(false)}>
        <p className="mb-4 text-sm">This cannot be undone. Message text for this person will be replaced with a placeholder.</p><ErrorNote error={del.error} />
        <div className="mt-3 flex justify-end gap-2"><Button onClick={() => setConfirmDel(false)}>Cancel</Button><Button variant="danger" loading={del.isPending} onClick={() => del.mutate(undefined, { onSuccess: () => nav("/profiles") })}>Delete</Button></div></Modal>}
    </div>
  );
}
