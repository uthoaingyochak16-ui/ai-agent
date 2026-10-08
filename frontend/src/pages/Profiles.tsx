import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Search } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Avatar, Badge, Button, ChannelBadge, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, Td, Th, TableWrap, Switch, useToast } from "../components/ui";
import { api, download } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago } from "../lib/format";

const CONSENT: Record<string, ["ok" | "warn" | "danger" | "neutral", string]> = { given: ["ok", "Consent given"], asked: ["warn", "Consent asked"], declined: ["danger", "Declined"], unknown: ["neutral", "Not asked"] };

export function ProfileForm({ initial, onSaved, onCancel, create }: { initial?: any; onSaved: (p: any) => void; onCancel?: () => void; create?: boolean }) {
  const [f, setF] = useState({ name: initial?.name ?? "", phone: "", email: "", location: initial?.location ?? "", age: initial?.age ?? "", profession: initial?.profession ?? "", language: initial?.language ?? "", consent: initial?.consent === "given" });
  const { user } = useAuth();
  const m = useMutation({
    mutationFn: () => {
      const body: any = { name: f.name || null, location: f.location || null, age: f.age === "" ? null : Number(f.age), profession: f.profession || null, language: f.language || null };
      if (f.phone) body.phone = f.phone;
      if (f.email) body.email = f.email;
      if (create || (initial && f.consent !== (initial.consent === "given"))) body.consent_given = f.consent;
      return create ? api("/api/profiles", { body }) : api(`/api/profiles/${initial.id}`, { method: "PATCH", body });
    },
    onSuccess: onSaved,
  });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const err: any = m.error;
  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); m.mutate(); }} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">{(i) => <Input id={i} value={f.name} onChange={set("name")} />}</Field>
        <Field label="Preferred language" hint="bn, en or banglish">{(i) => <Input id={i} value={f.language} onChange={set("language")} />}</Field>
        <Field label="Phone" hint={initial?.phone ? `Saved: ${initial.phone}. Type a new number to replace it.` : "Any format; saved as +880..."}>{(i) => <Input id={i} inputMode="tel" value={f.phone} onChange={set("phone")} />}</Field>
        <Field label="Email" hint={initial?.email ? `Saved: ${initial.email}` : undefined}>{(i) => <Input id={i} type="email" value={f.email} onChange={set("email")} />}</Field>
        <Field label="Location">{(i) => <Input id={i} value={f.location} onChange={set("location")} />}</Field>
        <Field label="Age">{(i) => <Input id={i} type="number" min={5} max={110} value={f.age} onChange={set("age")} />}</Field>
        <Field label="Profession" className="sm:col-span-2">{(i) => <Input id={i} value={f.profession} onChange={set("profession")} />}</Field>
      </div>
      <Switch checked={f.consent} onChange={(v) => setF({ ...f, consent: v })} label="This person agreed to us saving their details" />
      {err?.detail?.error === "duplicate" && <p role="alert" className="rounded-ctl bg-warn-soft px-3 py-2 text-sm text-warn">That {err.detail.field} already belongs to <Link className="font-semibold underline" to={`/profiles/${err.detail.owner_profile_id}`}>profile {err.detail.owner_profile_id}</Link>. Merge the profiles instead of duplicating it.</p>}
      {err && err.detail?.error !== "duplicate" && <ErrorNote error={err} />}
      <div className="flex justify-end gap-2">{onCancel && <Button type="button" onClick={onCancel}>Cancel</Button>}<Button type="submit" variant="primary" loading={m.isPending}>{create ? "Create profile" : "Save changes"}</Button></div>
      {user?.role === "agent" && <p className="text-xs text-muted">Phone and email are masked for agents; typing a new value replaces the stored one.</p>}
    </form>
  );
}

export default function Profiles() {
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [channel, setChannel] = useState("");
  const [creating, setCreating] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const { user } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  useEffect(() => { const t = setTimeout(() => setDq(q), 300); return () => clearTimeout(t); }, [q]);
  const list = useQuery({ queryKey: ["profiles", dq, channel, showTest], queryFn: () => api("/api/profiles", { params: { q: dq, channel, include_test: showTest } }) });
  const sugg = useQuery({ queryKey: ["suggestions"], queryFn: () => api<any[]>("/api/profiles/merge-suggestions"), enabled: user?.role === "admin" });
  const refreshAll = () => { qc.invalidateQueries({ queryKey: ["suggestions"] }); qc.invalidateQueries({ queryKey: ["profiles"] }); };
  const merge = useMutation({ mutationFn: (x: { keep: number; other: number }) => api("/api/profiles/merge", { body: { keep_id: x.keep, merge_id: x.other } }), onSuccess: () => { toast("Merged into one profile. You can undo it on the profile page."); refreshAll(); }, onError: (e) => toast(e instanceof Error ? e.message : "Merge failed", "danger") });
  const dismiss = useMutation({ mutationFn: (pid: number) => api(`/api/profiles/merge-suggestions/${pid}`, { method: "DELETE" }), onSuccess: refreshAll });
  return (
    <>
      <PageHeader title="Profiles" subtitle="One profile per person, across Messenger, Instagram and WhatsApp."
        actions={<>{user?.role === "admin" && <Button onClick={() => download("/api/profiles/export.csv", "profiles.csv").catch(() => toast("Export failed", "danger"))}><Download className="h-4 w-4" aria-hidden />Export CSV</Button>}<Button variant="primary" onClick={() => setCreating(true)}><Plus className="h-4 w-4" aria-hidden />New profile</Button></>} />
      {sugg.data && sugg.data.length > 0 && (
        <div className="mb-4 rounded-panel border border-warn bg-warn-soft p-3 text-sm">
          <p className="font-semibold text-warn">{sugg.data.length} guest(s) may have more than one profile</p>
          <p className="mb-2 text-warn">Same full name on another Page, or the same phone/email with a different name. Merge if it is the same person; nothing is lost and it can be undone.</p>
          <ul className="space-y-2">{sugg.data.map((x) => (
            <li key={x.profile_id} className="flex flex-wrap items-center gap-2 rounded-ctl bg-surface px-3 py-2">
              <span className="min-w-0 flex-1 text-ink"><Link className="font-semibold underline" to={`/profiles/${x.suggested_with}`}>{x.names?.[1] || `Profile ${x.suggested_with}`}</Link> <span className="text-muted">and</span> <Link className="font-semibold underline" to={`/profiles/${x.profile_id}`}>{x.names?.[0] || `Profile ${x.profile_id}`}</Link></span>
              <Button size="sm" variant="primary" loading={merge.isPending} onClick={() => merge.mutate({ keep: x.suggested_with, other: x.profile_id })}>Merge into one</Button>
              <Button size="sm" onClick={() => dismiss.mutate(x.profile_id)}>Not the same person</Button>
            </li>))}</ul>
        </div>
      )}
      <div className="mb-3 flex flex-wrap gap-2">
        <div className="relative min-w-[220px] flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" aria-hidden /><Input aria-label="Search by name, phone or email" placeholder="Search by name (saved or chat name), phone or email" className="pl-9" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <Select aria-label="Channel" className="w-auto" value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">All channels</option><option value="messenger">Messenger</option><option value="instagram">Instagram</option><option value="whatsapp">WhatsApp</option></Select>
        <div className="self-center"><Switch checked={showTest} onChange={setShowTest} label="Show Test Chat profiles" /></div>
      </div>
      {list.isLoading ? <Spinner /> : !list.data?.items.length ? <Empty title={dq ? "No matching profiles" : "No profiles yet"}>{dq ? "Try a different name, a full phone number or an exact email." : "Profiles are created automatically from a guest's first message. The same guest on another Page or on WhatsApp is linked to the same profile when they can be recognised."}</Empty> : (
        <>
          <TableWrap><thead><tr><Th>Name</Th><Th>Phone</Th><Th>Email</Th><Th>Channels</Th><Th>Consent</Th><Th>Updated</Th></tr></thead>
            <tbody>{list.data.items.map((p: any) => (
              <tr key={p.id} className="hover:bg-sunken/60"><Td><Link className="flex items-center gap-2.5 font-semibold text-accent-strong" to={`/profiles/${p.id}`}><Avatar name={p.display_name} src={p.avatar_url} size={32} />
                <span className="min-w-0"><span className="block truncate underline">{p.display_name || `Guest ${p.id}`}</span>{p.name && p.channel_name && p.channel_name !== p.name && <span className="block truncate text-xs font-normal text-muted">on chat: {p.channel_name}</span>}</span></Link></Td><Td className="tabular-nums">{p.phone ?? "-"}</Td><Td>{p.email ?? "-"}</Td>
                <Td><div className="flex flex-wrap gap-1">{p.channels.map((c: string) => <ChannelBadge key={c} channel={c} />)}</div></Td><Td><Badge tone={CONSENT[p.consent][0]}>{CONSENT[p.consent][1]}</Badge></Td><Td className="text-muted">{ago(p.updated_at)}</Td></tr>
            ))}</tbody></TableWrap>
          <p className="mt-2 text-sm text-muted">{list.data.total} profile(s)</p>
        </>
      )}
      {creating && <Modal title="New profile" onClose={() => setCreating(false)}><ProfileForm create onCancel={() => setCreating(false)} onSaved={(p) => { qc.invalidateQueries({ queryKey: ["profiles"] }); setCreating(false); toast("Profile created"); nav(`/profiles/${p.id}`); }} /></Modal>}
    </>
  );
}
