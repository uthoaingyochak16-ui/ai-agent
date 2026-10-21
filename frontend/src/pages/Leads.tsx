import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Send, Undo2, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, ChannelBadge, Empty, ErrorNote, PageHeader, Panel, Spinner, Td, Th, TableWrap, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago } from "../lib/format";

const TABS: { key: string; label: string }[] = [
  { key: "new", label: "New" }, { key: "failed", label: "Failed" }, { key: "sent", label: "Sent" }, { key: "dismissed", label: "Dismissed" },
];
const TONE: Record<string, "ok" | "warn" | "danger" | "neutral"> = { new: "warn", sent: "ok", failed: "danger", dismissed: "neutral" };

/** Guests the AI found interested in a program or in knowing more. Staff choose which ones go to the main
 *  application, where each becomes a guest and a task (assigned by its lead routing). */
export default function Leads() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState("new");
  const [picked, setPicked] = useState<number[]>([]);
  const q = useQuery({ queryKey: ["leads", tab], queryFn: () => api("/api/leads", { params: { status: tab } }) });
  const refresh = () => { setPicked([]); qc.invalidateQueries({ queryKey: ["leads"] }); };
  const send = useMutation({
    mutationFn: (ids: number[]) => api("/api/leads/send", { body: { ids } }),
    onSuccess: (r: any) => {
      const notSent = r.results.filter((x: any) => x.status !== "sent");
      toast(`${r.sent} sent to the main app` + (notSent.length ? ` · ${notSent.length} not sent: ${notSent[0].error}` : ""));
      refresh();
    },
  });
  const patch = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api(`/api/leads/${id}`, { method: "PATCH", body: { status } }),
    onSuccess: refresh,
  });
  const sync = useMutation({
    mutationFn: () => api("/api/main-app/sync-programs", { method: "POST" }),
    onSuccess: (r: any) => { toast(r.status === "ok" ? `${r.programs} program(s) from the main app` : r.error); qc.invalidateQueries({ queryKey: ["leads"] }); },
  });
  const d: any = q.data;
  const ps = d?.main_app?.programs_sync;
  const sendable = (l: any) => (l.status === "new" || l.status === "failed") && !!l.phone && d?.main_app?.connected;
  const items: any[] = d?.items ?? [];
  const pickable = items.filter(sendable).map((l) => l.id);
  const toggle = (id: number) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);

  return (
    <>
      <PageHeader title="Leads" subtitle="Guests the assistant found interested in a program or in knowing more. Choose the ones to send to the main application: each becomes a guest and a task there." />
      {d && (
        <Panel className="mb-4">
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <div>
              <b>Main application:</b>{" "}
              {d.main_app.connected ? <Badge tone="ok">connected</Badge> : <Badge tone="neutral">not connected</Badge>}
              {ps && <span className="ml-2 text-muted">programs: {ps.status === "ok" ? `${ps.programs} open, synced ${ago(ps.at)}` : `sync failed: ${ps.error}`}</span>}
            </div>
            {user?.role === "admin" && d.main_app.connected && (
              <Button size="sm" loading={sync.isPending} onClick={() => sync.mutate()}><RefreshCw className="h-4 w-4" aria-hidden />Sync programs now</Button>
            )}
          </div>
          <ErrorNote error={sync.error} />
        </Panel>
      )}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex rounded-ctl border border-line p-0.5" role="tablist">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} onClick={() => { setTab(t.key); setPicked([]); }}
                    className={`rounded-ctl px-3 py-1.5 text-sm font-semibold ${tab === t.key ? "bg-accent text-on-accent" : "text-muted hover:text-ink"}`}>
              {t.label} ({d?.counts?.[t.key] ?? 0})
            </button>
          ))}
        </div>
        {(tab === "new" || tab === "failed") && d?.main_app?.connected && (
          <Button variant="primary" size="sm" disabled={!picked.length} loading={send.isPending} onClick={() => send.mutate(picked)}>
            <Send className="h-4 w-4" aria-hidden />Send {picked.length || ""} to the main app
          </Button>
        )}
      </div>
      <ErrorNote error={send.error || patch.error} />
      {q.isLoading ? <Spinner /> : q.error ? <ErrorNote error={q.error} /> : !items.length ? (
        <Empty title="Nothing here">{tab === "new" ? "When a guest shows interest in a program or in knowing more, the assistant adds them here." : "No leads with this status."}</Empty>
      ) : (
        <TableWrap>
          <thead><tr>
            <Th>{pickable.length > 0 && <input type="checkbox" aria-label="Select all that can be sent" checked={picked.length === pickable.length}
                                              onChange={(e) => setPicked(e.target.checked ? pickable : [])} />}</Th>
            <Th>Guest</Th><Th>Phone</Th><Th>Interested in</Th><Th>Channel</Th><Th>Status</Th><Th>When</Th><Th><span className="sr-only">Actions</span></Th>
          </tr></thead>
          <tbody>{items.map((l) => (
            <tr key={l.id} className="hover:bg-sunken/60">
              <Td>{sendable(l) && <input type="checkbox" aria-label={`Select ${l.guest}`} checked={picked.includes(l.id)} onChange={() => toggle(l.id)} />}</Td>
              <Td><Link className="font-semibold text-accent-strong underline" to={`/profiles/${l.profile_id}`}>{l.guest}</Link>
                {l.conversation_id && <Link className="block text-xs text-muted underline" to={`/inbox?conv=${l.conversation_id}`}>open chat</Link>}</Td>
              <Td className="tabular-nums">{l.phone ?? <span className="text-xs text-muted">no phone: add it on the profile</span>}</Td>
              <Td>{l.interest}{l.note && <span className="block max-w-md text-xs text-muted">{l.note}</span>}</Td>
              <Td>{l.channel ? <ChannelBadge channel={l.channel} /> : "-"}</Td>
              <Td><Badge tone={TONE[l.status] ?? "neutral"}>{l.status}</Badge>
                {l.status === "sent" && <span className="block text-xs text-muted">{ago(l.sent_at)}</span>}
                {l.status === "failed" && l.error && <span className="block max-w-xs text-xs text-muted">{l.error}</span>}</Td>
              <Td className="text-muted">{ago(l.created_at)}</Td>
              <Td><div className="flex gap-1">
                {sendable(l) && <Button size="sm" loading={send.isPending && send.variables?.length === 1 && send.variables[0] === l.id} onClick={() => send.mutate([l.id])}><Send className="h-4 w-4" aria-hidden />Send</Button>}
                {(l.status === "new" || l.status === "failed") && <Button size="sm" onClick={() => patch.mutate({ id: l.id, status: "dismissed" })}><X className="h-4 w-4" aria-hidden />Dismiss</Button>}
                {l.status === "dismissed" && <Button size="sm" onClick={() => patch.mutate({ id: l.id, status: "new" })}><Undo2 className="h-4 w-4" aria-hidden />Restore</Button>}
              </div></Td>
            </tr>
          ))}</tbody>
        </TableWrap>
      )}
    </>
  );
}
