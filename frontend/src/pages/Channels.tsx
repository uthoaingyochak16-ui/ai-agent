import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Dices, PlugZap } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, ChannelBadge, CopyButton, ErrorNote, Field, Input, PageHeader, Panel, SecretInput, Spinner, Switch, useToast, useConfirm } from "../components/ui";
import { api } from "../lib/api";
import { useDebouncedAutosave } from "../lib/autosave";
import { ago } from "../lib/format";

const ID_LABEL: Record<string, string> = { messenger: "Facebook Page ID", instagram: "Instagram account ID", whatsapp: "WhatsApp phone number ID" };
const rand = () => Array.from(crypto.getRandomValues(new Uint8Array(18)), (b) => b.toString(16).padStart(2, "0")).join("");

function ChannelCard({ c }: { c: any }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [f, setF] = useState({ enabled: c.enabled, account_id: c.account_id ?? "", waba_id: c.waba_id ?? "", access_token: "", app_secret: "", verify_token: c.verify_token ?? "" });
  useEffect(() => setF((x) => ({ ...x, enabled: c.enabled, account_id: c.account_id ?? "", waba_id: c.waba_id ?? "", verify_token: c.verify_token ?? "" })), [c]);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api(`/api/channels/${c.channel}`, { method: "PUT", body }),
    onSuccess: (_data, body) => { toast("Saved"); if (body.access_token || body.app_secret) setF((x) => ({ ...x, access_token: "", app_secret: "" })); qc.invalidateQueries({ queryKey: ["channels"] }); },
  });
  const publicFields = { enabled: f.enabled, account_id: f.account_id, waba_id: f.waba_id || null, verify_token: f.verify_token || null };
  const allFields = { ...publicFields, access_token: f.access_token || undefined, app_secret: f.app_secret || undefined };
  useDebouncedAutosave(JSON.stringify(publicFields), true, () => save.mutate(publicFields));
  const test = useMutation({ mutationFn: () => api(`/api/channels/${c.channel}/test`, { method: "POST" }), onSuccess: (r: any) => { toast(r.status === "ok" ? "Connection works" : "Connection failed", r.status === "ok" ? "ok" : "danger"); qc.invalidateQueries({ queryKey: ["channels"] }); } });
  const tone = c.status === "ok" ? "ok" : c.status === "error" ? "danger" : c.status === "configured" ? "warn" : "neutral";
  return (
    <Panel title={c.channel === "messenger" ? "Messenger (Facebook Page)" : c.channel === "instagram" ? "Instagram" : "WhatsApp"}
      actions={<div className="flex items-center gap-2"><ChannelBadge channel={c.channel} /><Badge tone={tone}>{c.status === "ok" ? "Connected" : c.status === "error" ? "Error" : c.status === "configured" ? "Saved, not tested" : "Not set up"}</Badge></div>}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(allFields); }}>
        <Field label="Webhook URL" hint="Paste this into the Meta app's webhook settings (it must be public HTTPS).">{(i) => <div className="flex gap-2"><Input id={i} persistDraft={false} readOnly value={c.webhook_url} onFocus={(e) => e.target.select()} /><CopyButton text={c.webhook_url} /></div>}</Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={ID_LABEL[c.channel]}>{(i) => <Input id={i} persistDraft={false} value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })} />}</Field>
          {c.channel === "whatsapp" && <Field label="WhatsApp Business Account ID">{(i) => <Input id={i} persistDraft={false} value={f.waba_id} onChange={(e) => setF({ ...f, waba_id: e.target.value })} />}</Field>}
          <Field label="Access token" hint={c.has_token ? "Saved securely. Focus to replace it; the saved value is never returned." : "Page, Instagram or System User token"}>{(i) => <SecretInput id={i} configured={c.has_token} value={f.access_token} onChange={(e) => setF({ ...f, access_token: e.target.value })} onBlur={() => f.access_token && save.mutate({ access_token: f.access_token })} />}</Field>
          <Field label={c.server_has_app_secret ? "App secret (optional)" : "App secret (required)"} hint={c.has_app_secret ? "Saved securely. Focus to replace it." : c.server_has_app_secret ? "Leave empty to use the default from the server settings." : "Meta app → App settings → Basic → App secret. Without it every incoming message is rejected."}>{(i) => <SecretInput id={i} configured={c.has_app_secret} value={f.app_secret} onChange={(e) => setF({ ...f, app_secret: e.target.value })} onBlur={() => f.app_secret && save.mutate({ app_secret: f.app_secret })} />}</Field>
          <Field label="Verify token" hint="Copy this exact value into Meta's webhook setup. If you change it here, change it in Meta too.">{(i) => <div className="flex gap-2"><Input id={i} persistDraft={false} value={f.verify_token} onChange={(e) => setF({ ...f, verify_token: e.target.value })} /><CopyButton text={f.verify_token} />
            <Button type="button" onClick={async () => (!f.verify_token || await ask({ title: "Replace the verify token?", message: "The token already entered in the Meta app stops working until you paste the new one there too.", confirmLabel: "Replace" })) && setF({ ...f, verify_token: rand() })}><Dices className="h-4 w-4" aria-hidden />Generate</Button></div>}</Field>
        </div>
        {!c.has_app_secret && !c.server_has_app_secret && c.status !== "unconfigured" && <p className="text-sm text-danger">No app secret is set, so incoming webhooks fail the signature check and no messages are received.</p>}
        <Switch checked={f.enabled} onChange={(v) => setF({ ...f, enabled: v })} label="Replies enabled" />
        {c.status_detail && <p className={`text-sm ${c.status === "error" ? "text-danger" : "text-muted"}`}>{c.status === "ok" && <CheckCircle2 className="mr-1 inline h-4 w-4 text-ok" aria-hidden />}{c.status_detail}{c.last_checked_at && ` (checked ${ago(c.last_checked_at)})`}</p>}
        <ErrorNote error={save.error || test.error} />
        <div className="flex flex-wrap justify-end gap-2"><Button type="button" onClick={() => test.mutate()} loading={test.isPending} disabled={!c.has_token}><PlugZap className="h-4 w-4" aria-hidden />Test connection</Button><Button type="submit" variant="primary" loading={save.isPending}>Save</Button></div>
      </form>
    </Panel>
  );
}

export default function Channels() {
  const q = useQuery({ queryKey: ["channels"], queryFn: () => api<any[]>("/api/channels") });
  return (
    <>
      <PageHeader title="Channels" subtitle="Connect each Meta channel, then test it. Tokens and secrets are encrypted; configured fields show a safe masked value." />
      {q.isLoading ? <Spinner /> : <div className="space-y-5">{q.data?.map((c) => <ChannelCard key={c.channel} c={c} />)}</div>}
    </>
  );
}
