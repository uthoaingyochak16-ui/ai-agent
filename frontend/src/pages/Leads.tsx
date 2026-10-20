import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Send } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, ChannelBadge, Empty, ErrorNote, PageHeader, Panel, Select, Spinner, Td, Th, TableWrap, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago } from "../lib/format";

const STATUS: Record<string, ["ok" | "warn" | "danger" | "neutral", string]> = {
  sent: ["ok", "In the main app"], pending: ["warn", "Waiting"], failed: ["danger", "Failed"], off: ["neutral", "Main app not connected"],
};

/** Guests the assistant found interested in a program or in knowing more. Each one also becomes a guest + task in the
 *  main application once a phone number is known (a lead still waiting is tried again every few minutes). */
export default function Leads() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["leads", status], queryFn: () => api("/api/leads", { params: { status } }) });
  const send = useMutation({
    mutationFn: (id: number) => api(`/api/leads/${id}/send`, { method: "POST" }),
    onSuccess: (r: any) => { toast(r.status === "sent" ? "Sent to the main app" : `Not sent: ${r.error}`); qc.invalidateQueries({ queryKey: ["leads"] }); },
  });
  const sync = useMutation({
    mutationFn: () => api("/api/main-app/sync-programs", { method: "POST" }),
    onSuccess: (r: any) => { toast(r.status === "ok" ? `${r.programs} program(s) from the main app` : r.error); qc.invalidateQueries({ queryKey: ["leads"] }); },
  });
  const d: any = q.data;
  const ps = d?.main_app?.programs_sync;
  return (
    <>
      <PageHeader title="Leads" subtitle="Guests interested in attending a program or in knowing more. Each becomes a guest and a task in the main application once their phone number is known." />
      {d && (
        <Panel className="mb-4">
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <div>
              <b>Main application:</b>{" "}
              {d.main_app.connected ? <Badge tone="ok">connected</Badge> : <Badge tone="neutral">not connected (MAIN_APP_API_URL / MAIN_APP_API_KEY)</Badge>}
              {ps && <span className="ml-2 text-muted">programs: {ps.status === "ok" ? `${ps.programs} open, synced ${ago(ps.at)}` : `sync failed: ${ps.error}`}</span>}
            </div>
            {user?.role === "admin" && d.main_app.connected && (
              <Button size="sm" loading={sync.isPending} onClick={() => sync.mutate()}><RefreshCw className="h-4 w-4" aria-hidden />Sync programs now</Button>
            )}
          </div>
          <ErrorNote error={sync.error} />
        </Panel>
      )}
      <div className="mb-3 flex items-center gap-2">
        <Select aria-label="Filter by status" persistDraft={false} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All ({d ? Object.values(d.counts as Record<string, number>).reduce((a, b) => a + b, 0) : "…"})</option>
          {Object.entries(STATUS).map(([k, [, label]]) => <option key={k} value={k}>{label} ({d?.counts?.[k] ?? 0})</option>)}
        </Select>
      </div>
      {q.isLoading ? <Spinner /> : q.error ? <ErrorNote error={q.error} /> : !d.items.length ? (
        <Empty title="No leads yet">When a guest shows interest in a program or in knowing more, the assistant adds them here.</Empty>
      ) : (
        <TableWrap>
          <thead><tr><Th>Guest</Th><Th>Interested in</Th><Th>Channel</Th><Th>Status</Th><Th>When</Th><Th><span className="sr-only">Actions</span></Th></tr></thead>
          <tbody>{d.items.map((l: any) => (
            <tr key={l.id} className="hover:bg-sunken/60">
              <Td><Link className="font-semibold text-accent-strong underline" to={`/profiles/${l.profile_id}`}>{l.guest}</Link>
                {l.conversation_id && <Link className="block text-xs text-muted underline" to={`/inbox?conv=${l.conversation_id}`}>open chat</Link>}</Td>
              <Td>{l.interest}{l.note && <span className="block max-w-md truncate text-xs text-muted">{l.note}</span>}</Td>
              <Td>{l.channel ? <ChannelBadge channel={l.channel} /> : "-"}</Td>
              <Td><Badge tone={STATUS[l.status]?.[0] ?? "neutral"}>{STATUS[l.status]?.[1] ?? l.status}</Badge>
                {l.status === "sent" && l.main_app_task_id && <span className="block text-xs text-muted">task {l.main_app_task_id.slice(0, 8)}</span>}
                {l.status !== "sent" && l.error && <span className="block max-w-xs text-xs text-muted">{l.error}</span>}</Td>
              <Td className="text-muted">{ago(l.created_at)}</Td>
              <Td>{l.status !== "sent" && d.main_app.connected && (
                <Button size="sm" loading={send.isPending && send.variables === l.id} onClick={() => send.mutate(l.id)}><Send className="h-4 w-4" aria-hidden />Send now</Button>
              )}</Td>
            </tr>
          ))}</tbody>
        </TableWrap>
      )}
    </>
  );
}
