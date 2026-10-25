import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, ErrorNote, Field, IconButton, Input, Meter, PageHeader, Panel, SecretInput, Select, Spinner, Switch, Tabs, TableWrap, Td, Textarea, Th, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useDebouncedAutosave } from "../lib/autosave";
import { num, usd, when } from "../lib/format";

const PROVIDERS = ["anthropic", "openai", "google"];
const TASKS: Record<string, string> = { answer: "Answer (standard)", answer_large: "Answer (large, for hard questions)", rewrite: "Follow-up question rewrite", keywords: "Keyword extraction (what the guest asks for)", keygen: "Keyword generation (checked against the record)", router: "Intent router (cheap model, JSON)", extraction: "Profile extraction", summarization: "Conversation summary" };
type Tab = "providers" | "routing" | "budget" | "retrieval" | "privacy" | "messages";
const AI_TABS: Tab[] = ["providers", "routing", "budget", "retrieval", "privacy", "messages"];

function useSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["ai-settings"], queryFn: () => api("/api/ai/settings") });
  const save = useMutation({ mutationFn: (body: Record<string, unknown>) => api("/api/ai/settings", { method: "PUT", body }), onSuccess: (d) => { qc.setQueryData(["ai-settings"], d); qc.invalidateQueries({ queryKey: ["dashboard"] }); qc.invalidateQueries({ queryKey: ["embedding-status"] }); toast("Settings saved"); } });
  return { s: q.data, loading: q.isLoading, save };
}

export default function AISettings() {
  const [params, setParams] = useSearchParams();
  const [tab, setTabState] = useState<Tab>(() => {
    const requested = params.get("tab") as Tab | null;
    if (requested && AI_TABS.includes(requested)) return requested;
    const saved = localStorage.getItem("assistant:ai-settings-tab") as Tab | null;
    return saved && AI_TABS.includes(saved) ? saved : "providers";
  });
  const setTab = (next: Tab) => { localStorage.setItem("assistant:ai-settings-tab", next); setTabState(next); setParams({ tab: next }, { replace: true }); };
  return (
    <>
      <PageHeader title="AI settings" subtitle="Which providers and models answer, what they cost, and what is allowed to leave your system." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "providers", label: "Providers and prices" }, { id: "routing", label: "Model routing" }, { id: "budget", label: "Budget and cost" }, { id: "retrieval", label: "Retrieval" }, { id: "privacy", label: "Privacy" }, { id: "messages", label: "Messages and handover" }]} />
      <div hidden={tab !== "providers"}><Providers /></div>
      <div hidden={tab !== "routing"}><Routing /></div>
      <div hidden={tab !== "budget"} className="space-y-5"><Budget /><Alerts /></div>
      <div hidden={tab !== "retrieval"}><Retrieval /></div>
      <div hidden={tab !== "privacy"}><Privacy /></div>
      <div hidden={tab !== "messages"} className="space-y-5"><Messages /><ReplyStyles /><SmallTalk /><Tools /></div>
    </>
  );
}

/* ---------------------------------------------------------------- providers + prices */
function Providers() {
  const qc = useQueryClient();
  const toast = useToast();
  const provs = useQuery({ queryKey: ["providers"], queryFn: () => api<any[]>("/api/ai/providers") });
  const models = useQuery({ queryKey: ["models"], queryFn: () => api<any[]>("/api/ai/models") });
  const [row, setRow] = useState({ provider: "anthropic", model: "", input_price: "", output_price: "", price_verified: false });
  const putModel = useMutation({ mutationFn: (b: any) => api("/api/ai/models", { method: "PUT", body: b }), onSuccess: () => { toast("Price saved"); setRow({ ...row, model: "", input_price: "", output_price: "" }); qc.invalidateQueries({ queryKey: ["models"] }); } });
  const delModel = useMutation({ mutationFn: (id: number) => api(`/api/ai/models/${id}`, { method: "DELETE" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }) });
  return (
    <div className="space-y-5">
      {provs.isLoading ? <Spinner /> : provs.data?.map((p) => <ProviderCard key={p.name} p={p} />)}
      <Panel title="Model prices (USD per 1 million tokens)">
        <p className="mb-3 text-sm text-muted">Cost estimates use this table. Prices change: check your provider's pricing page and tick "Verified" once you have. Models without a price are logged at $0.</p>
        <TableWrap><thead><tr><Th>Provider</Th><Th>Model</Th><Th className="text-right">Input</Th><Th className="text-right">Output</Th><Th>Verified</Th><Th><span className="sr-only">Actions</span></Th></tr></thead>
          <tbody>{models.data?.map((m) => <tr key={m.id}><Td>{m.provider}</Td><Td className="font-mono text-xs">{m.model}</Td><Td className="text-right tabular-nums">{m.input_price}</Td><Td className="text-right tabular-nums">{m.output_price}</Td><Td>{m.price_verified ? <Badge tone="ok">Verified</Badge> : <Badge tone="warn">Check price</Badge>}</Td><Td><IconButton label={`Delete ${m.model}`} onClick={() => delModel.mutate(m.id)}><Trash2 className="h-4 w-4" aria-hidden /></IconButton></Td></tr>)}</tbody></TableWrap>
        <form className="mt-4 grid items-end gap-3 sm:grid-cols-[130px_1fr_110px_110px_auto_auto]" onSubmit={(e) => { e.preventDefault(); putModel.mutate({ ...row, input_price: +row.input_price, output_price: +row.output_price }); }}>
          <Field label="Provider">{(i) => <Select id={i} value={row.provider} onChange={(e) => setRow({ ...row, provider: e.target.value })}>{PROVIDERS.map((p) => <option key={p}>{p}</option>)}</Select>}</Field>
          <Field label="Model name">{(i) => <Input id={i} required value={row.model} onChange={(e) => setRow({ ...row, model: e.target.value })} />}</Field>
          <Field label="Input price">{(i) => <Input id={i} required type="number" step="any" min="0" value={row.input_price} onChange={(e) => setRow({ ...row, input_price: e.target.value })} />}</Field>
          <Field label="Output price">{(i) => <Input id={i} required type="number" step="any" min="0" value={row.output_price} onChange={(e) => setRow({ ...row, output_price: e.target.value })} />}</Field>
          <div className="pb-2"><Switch checked={row.price_verified} onChange={(v) => setRow({ ...row, price_verified: v })} label="Verified" /></div>
          <Button type="submit" loading={putModel.isPending}>Save price</Button>
        </form>
        <div className="mt-2"><ErrorNote error={putModel.error} /></div>
      </Panel>
    </div>
  );
}

function ProviderCard({ p }: { p: any }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [key, setKey] = useState("");
  const [base, setBase] = useState(p.base_url ?? "");
  const [model, setModel] = useState("");
  const [res, setRes] = useState<any>(null);
  const catalog = useQuery({
    queryKey: ["provider-model-catalog", p.name, p.base_url],
    queryFn: () => api<{ models: { id: string; display_name: string }[] }>(`/api/ai/providers/${p.name}/models`),
    enabled: !!p.has_key,
  });
  const save = useMutation({ mutationFn: (b: any) => api(`/api/ai/providers/${p.name}`, { method: "PUT", body: b }), onSuccess: () => { toast("Provider saved"); setKey(""); qc.invalidateQueries({ queryKey: ["providers"] }); } });
  const test = useMutation({ mutationFn: () => api(`/api/ai/providers/${p.name}/test`, { body: { model } }), onSuccess: setRes });
  const useForAnswers = useMutation({
    mutationFn: async () => {
      await api(`/api/ai/providers/${p.name}`, { method: "PUT", body: { enabled: true } });
      return api("/api/ai/routes/answer", { method: "PUT", body: { primary_provider: p.name, primary_model: model, max_tokens: 400, temperature: 0.2 } });
    },
    onSuccess: () => {
      toast("Answer route configured");
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["routes"] });
    },
  });
  useEffect(() => setBase(p.base_url ?? ""), [p.base_url]);
  useDebouncedAutosave(base, true, () => { if (base !== p.base_url) save.mutate({ base_url: base }); });
  return (
    <Panel title={p.name === "anthropic" ? "Anthropic (Claude)" : p.name === "openai" ? "OpenAI (ChatGPT)" : "Google (Gemini)"} actions={<Badge tone={p.enabled && p.has_key ? "ok" : "neutral"}>{p.enabled && p.has_key ? "Enabled" : "Off"}</Badge>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="API key" hint={p.has_key ? "Saved securely. Focus to replace it; the saved value is never returned." : undefined}>{(i) => <div className="flex gap-2"><SecretInput id={i} configured={p.has_key} value={key} onChange={(e) => setKey(e.target.value)} onBlur={() => key && save.mutate({ api_key: key })} /><Button disabled={!key} loading={save.isPending} onClick={() => save.mutate({ api_key: key })}>Save key</Button></div>}</Field>
        <Field label="Base URL" hint="Provider default is prefilled; custom proxies are autosaved.">{(i) => <Input id={i} draftKey={`provider-${p.name}-base-url`} value={base} onChange={(e) => setBase(e.target.value)} />}</Field>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2">
        <Switch checked={p.enabled} onChange={(v) => save.mutate({ enabled: v })} label="Enabled" />
        <Switch checked={p.allow_pii} onChange={(v) => save.mutate({ allow_pii: v })} label="Allow personal data (zero-retention agreement or self-hosted)" />
      </div>
      <p className="mt-2 text-xs text-muted">With "Allow personal data" off, phone numbers, emails, addresses and names are always replaced with placeholders before anything is sent. Cards, national IDs and your custom patterns are redacted either way.</p>
      <div className="mt-3">
        <ErrorNote error={catalog.error} />
        {p.has_key && catalog.data && <p className="text-xs text-muted">{catalog.data.models.length} available model(s) loaded from {p.name}.</p>}
      </div>
      <form className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3" onSubmit={(e) => { e.preventDefault(); setRes(null); test.mutate(); }}>
        <Field label="Test with model" className="min-w-[220px] flex-1">{(i) => catalog.data?.models.length ? (
          <Select id={i} required value={model} onChange={(e) => { setModel(e.target.value); setRes(null); }}>
            <option value="">Select a model</option>
            {model && !catalog.data.models.some((m) => m.id === model) && <option value={model}>Saved model: {model}</option>}
            {catalog.data.models.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
          </Select>
        ) : <Input id={i} required placeholder={catalog.isLoading ? "Loading models…" : "model name"} value={model} onChange={(e) => { setModel(e.target.value); setRes(null); }} list={`provider-models-${p.name}`} />}</Field>
        <datalist id={`provider-models-${p.name}`}>{catalog.data?.models.map((m) => <option key={m.id} value={m.id} />)}</datalist>
        <Button type="submit" loading={test.isPending} disabled={!p.has_key}>Test connection</Button>
        {res && <span className={`text-sm ${res.ok ? "text-ok" : "text-danger"}`}>{res.ok ? `Works (${res.latency_ms} ms)` : res.error}</span>}
      </form>
      {res?.ok && <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" loading={useForAnswers.isPending} onClick={() => useForAnswers.mutate()}>Use this model for answers</Button>
        <span className="text-xs text-muted">Enables this provider and configures the route needed by Teach and customer replies.</span>
      </div>}
      <div className="mt-2"><ErrorNote error={useForAnswers.error} /></div>
    </Panel>
  );
}

/* ---------------------------------------------------------------- routing */
function Routing() {
  const routes = useQuery({ queryKey: ["routes"], queryFn: () => api<any[]>("/api/ai/routes") });
  const models = useQuery({ queryKey: ["models"], queryFn: () => api<any[]>("/api/ai/models") });
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<any[]>("/api/ai/providers") });
  if (routes.isLoading) return <Spinner />;
  return (
    <div className="space-y-5">
      {!routes.data?.find((r) => r.task === "answer")?.configured && <p role="status" className="rounded-ctl border border-warn/50 bg-warn-soft px-3 py-2 text-sm text-warn">
        The Answer route is not configured. Testing a provider key alone is not enough; use “Use this model for answers” after a successful provider test, or set the Answer model below.
      </p>}
      <p className="text-sm text-muted">Use a small, cheap model for routine answers, rewriting, extraction and summaries. Follow-up rewriting only runs for short or referential messages; use about 80 output tokens at temperature 0. The large model is used only when the knowledge match is weak or the question is complex.</p>
      <datalist id="model-names">{models.data?.map((m) => <option key={m.id} value={m.model} />)}</datalist>
      {routes.data?.map((r) => <RouteRow key={r.task} r={r} providers={providers.data ?? []} />)}
    </div>
  );
}
function RouteRow({ r, providers }: { r: any; providers: any[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ primary_provider: r.primary_provider ?? "anthropic", primary_model: r.primary_model ?? "", fallback_provider: r.fallback_provider ?? "", fallback_model: r.fallback_model ?? "", max_tokens: r.max_tokens ?? 400, temperature: r.temperature ?? 0.2 });
  const save = useMutation({ mutationFn: () => api(`/api/ai/routes/${r.task}`, { method: "PUT", body: f }), onSuccess: () => { toast("Route saved"); qc.invalidateQueries({ queryKey: ["routes"] }); } });
  useDebouncedAutosave(JSON.stringify(f), true, () => { if (f.primary_model) save.mutate(); });
  return (
    <Panel title={TASKS[r.task]} actions={<Badge tone={r.configured ? "ok" : "warn"}>{r.configured ? "Configured" : "Not set"}</Badge>}>
      <form className="grid items-end gap-3 md:grid-cols-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Primary provider">{(i) => <Select id={i} value={f.primary_provider} onChange={(e) => setF({ ...f, primary_provider: e.target.value, primary_model: "" })}>{PROVIDERS.map((p) => <option key={p}>{p}</option>)}</Select>}</Field>
        <ProviderModelField label="Primary model" provider={f.primary_provider} providers={providers} value={f.primary_model}
          onChange={(primary_model) => setF({ ...f, primary_model })} />
        <Field label="Fallback provider">{(i) => <Select id={i} value={f.fallback_provider} onChange={(e) => setF({ ...f, fallback_provider: e.target.value, fallback_model: "" })}><option value="">None</option>{PROVIDERS.map((p) => <option key={p}>{p}</option>)}</Select>}</Field>
        <ProviderModelField label="Fallback model" provider={f.fallback_provider} providers={providers} value={f.fallback_model}
          onChange={(fallback_model) => setF({ ...f, fallback_model })} optional />
        <Field label="Max output tokens" hint="Keep answers short to save cost.">{(i) => <Input id={i} type="number" min={16} max={8000} value={f.max_tokens} onChange={(e) => setF({ ...f, max_tokens: +e.target.value })} />}</Field>
        <Field label="Temperature">{(i) => <Input id={i} type="number" step="0.1" min={0} max={2} value={f.temperature} onChange={(e) => setF({ ...f, temperature: +e.target.value })} />}</Field>
        <div className="md:col-span-2 md:text-right"><Button type="submit" variant="primary" loading={save.isPending}>Save route</Button></div>
      </form>
      <div className="mt-2"><ErrorNote error={save.error} /></div>
    </Panel>
  );
}

function ProviderModelField({ label, provider, providers, value, onChange, optional = false }: {
  label: string; provider: string; providers: any[]; value: string; onChange: (model: string) => void; optional?: boolean;
}) {
  const config = providers.find((p) => p.name === provider);
  const catalog = useQuery({
    queryKey: ["provider-model-catalog", provider, config?.base_url],
    queryFn: () => api<{ models: { id: string; display_name: string }[] }>(`/api/ai/providers/${provider}/models`),
    enabled: !!provider && !!config?.has_key,
  });
  const available = catalog.data?.models ?? [];
  return (
    <Field label={label}>{(id) => available.length ? (
      <Select id={id} required={!optional} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{catalog.isLoading ? "Loading models…" : "Select a model"}</option>
        {value && !available.some((m) => m.id === value) && <option value={value}>Saved model (not listed): {value}</option>}
        {available.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
      </Select>
    ) : <Input id={id} list="model-names" required={!optional} placeholder={catalog.isLoading ? "Loading models…" : "Enter model name"} value={value}
      onChange={(e) => onChange(e.target.value)} />}</Field>
  );
}

/* ---------------------------------------------------------------- budget + cost */
function Budget() {
  const { s, loading, save } = useSettings();
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (s) setF({ monthly_budget_usd: s.monthly_budget_usd, budget_alert_percent: s.budget_alert_percent, budget_fallback_enabled: s.budget_fallback_enabled, budget_hard_stop: s.budget_hard_stop, fp: s.budget_fallback_route?.provider ?? "", fm: s.budget_fallback_route?.model ?? "" }); }, [s]);
  const b = useQuery({ queryKey: ["budget"], queryFn: () => api("/api/ai/budget") });
  const cost = useQuery({ queryKey: ["cost"], queryFn: () => api("/api/stats/cost", { params: { days: 30 } }) });
  const calls = useQuery({ queryKey: ["calls"], queryFn: () => api<any[]>("/api/stats/calls", { params: { limit: 25 } }) });
  const [open, setOpen] = useState<number | null>(null);
  const payload = f ? { monthly_budget_usd: +f.monthly_budget_usd, budget_alert_percent: +f.budget_alert_percent, budget_fallback_enabled: f.budget_fallback_enabled, budget_hard_stop: f.budget_hard_stop, budget_fallback_route: f.fp && f.fm ? { provider: f.fp, model: f.fm } : null } : null;
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload), () => save.mutate(payload!));
  if (loading || !f) return <Spinner />;
  const tbl = (title: string, rows: any[], head: string) => (
    <Panel title={title}><TableWrap><thead><tr><Th>{head}</Th><Th className="text-right">Calls</Th><Th className="text-right">Tokens in</Th><Th className="text-right">Tokens out</Th><Th className="text-right">Cost</Th><Th className="text-right">Avg ms</Th></tr></thead>
      <tbody>{rows?.length ? rows.map((r, i) => <tr key={i}><Td>{r.key.map((k: any) => k ?? "none").join(" / ")}</Td><Td className="text-right tabular-nums">{num(r.calls)}</Td><Td className="text-right tabular-nums">{num(r.tokens_in)}</Td><Td className="text-right tabular-nums">{num(r.tokens_out)}</Td><Td className="text-right tabular-nums">{usd(r.cost)}</Td><Td className="text-right tabular-nums">{r.avg_latency_ms}</Td></tr>) : <tr><Td colSpan={6} className="text-muted">No calls in the last 30 days.</Td></tr>}</tbody></TableWrap></Panel>
  );
  return (
    <div className="space-y-5">
      <Panel title="Monthly budget">
        {b.data && b.data.level !== "unlimited" && <div className="mb-4"><Meter value={b.data.percent / 100} tone={b.data.level === "ok" ? "accent" : b.data.level === "alert" ? "warn" : "danger"} label="Budget used" /><p className="mt-1 text-sm text-muted">{usd(b.data.spent)} of {usd(b.data.budget)} this month ({b.data.percent}%)</p></div>}
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(payload!); }}>
          <Field label="Monthly limit (USD)" hint="0 means no limit.">{(i) => <Input id={i} type="number" step="any" min={0} value={f.monthly_budget_usd} onChange={(e) => setF({ ...f, monthly_budget_usd: e.target.value })} />}</Field>
          <Field label="Alert at (% of limit)">{(i) => <Input id={i} type="number" min={1} max={100} value={f.budget_alert_percent} onChange={(e) => setF({ ...f, budget_alert_percent: e.target.value })} />}</Field>
          <Field label="Cheaper model: provider">{(i) => <Select id={i} value={f.fp} onChange={(e) => setF({ ...f, fp: e.target.value })}><option value="">None</option>{PROVIDERS.map((p) => <option key={p}>{p}</option>)}</Select>}</Field>
          <Field label="Cheaper model: name">{(i) => <Input id={i} value={f.fm} onChange={(e) => setF({ ...f, fm: e.target.value })} />}</Field>
          <Switch checked={f.budget_fallback_enabled} onChange={(v) => setF({ ...f, budget_fallback_enabled: v })} label="Switch to the cheaper model when the limit is reached" />
          <Switch checked={f.budget_hard_stop} onChange={(v) => setF({ ...f, budget_hard_stop: v })} label="Stop calling the AI entirely when the limit is reached" />
          <div className="sm:col-span-2 sm:text-right"><ErrorNote error={save.error} /><Button type="submit" variant="primary" loading={save.isPending}>Save budget</Button></div>
        </form>
      </Panel>
      {cost.data && <>
        {tbl("By model, last 30 days", cost.data.by_model, "Provider / model")}
        {tbl("By channel", cost.data.by_channel, "Channel")}
        {tbl("By task", cost.data.by_task, "Task")}
        {tbl("By day", cost.data.by_day, "Day")}
      </>}
      <Panel title="What was sent to the AI providers">
        <p className="mb-3 text-sm text-muted">The exact request after privacy filtering, for the latest calls. Use this to verify nothing personal leaves your system.</p>
        {calls.isLoading ? <Spinner /> : <ul className="divide-y divide-line">{calls.data?.map((c) => (
          <li key={c.id} className="py-2"><button className="flex w-full flex-wrap items-center gap-2 text-left text-sm" aria-expanded={open === c.id} onClick={() => setOpen(open === c.id ? null : c.id)}>
            <span className="text-muted">{when(c.created_at)}</span><Badge>{c.task}</Badge><span className="font-mono text-xs">{c.provider}/{c.model}</span>{c.status === "error" ? <Badge tone="danger">Error</Badge> : <span className="tabular-nums text-muted">{c.tokens_in}/{c.tokens_out} tokens, {usd(c.cost)}</span>}{c.is_test && <Badge tone="blue">Test</Badge>}{c.fallback_used && <Badge tone="warn">Fallback</Badge>}{c.budget_fallback && <Badge tone="warn">Budget fallback</Badge>}</button>
            {open === c.id && <div className="mt-2 space-y-2">{c.error && <ErrorNote error={c.error} />}<p className="text-xs text-muted">Redacted before sending: {c.redactions?.length ? c.redactions.map((r: any) => r.placeholder).join(", ") : "nothing needed redaction"}</p><pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-ctl bg-sunken p-3 text-xs">{JSON.stringify(c.request, null, 2)}</pre></div>}</li>
        ))}</ul>}
      </Panel>
    </div>
  );
}

/* ---------------------------------------------------------------- generic settings forms */
function NumField({ label, hint, k, s, f, setF, step }: { label: string; hint?: string; k: string; s: any; f: any; setF: (x: any) => void; step?: string }) {
  return <Field label={label} hint={hint ?? `Default ${s._defaults[k]}`}>{(i) => <Input id={i} type="number" step={step ?? "any"} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}</Field>;
}
function Retrieval() {
  const { s, loading, save } = useSettings();
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["embedding-status"], queryFn: () => api<any>("/api/ai/embeddings/status"), refetchInterval: (q) => q.state.data?.progress?.status === "running" ? 1500 : 10000 });
  const rerankProviders = useQuery({ queryKey: ["rerank-providers"], queryFn: () => api<any[]>("/api/ai/rerank/providers") });
  const [f, setF] = useState<any>(null);
  const [local, setLocal] = useState({ base_url: "", api_key: "" });
  const [rerank, setRerank] = useState<Record<string, any>>({});
  useEffect(() => { if (s) setF(s); }, [s]);
  useEffect(() => { if (status.data?.local) setLocal((x) => ({ ...x, base_url: status.data.local.base_url ?? "" })); }, [status.data?.local?.base_url]);
  useEffect(() => { if (rerankProviders.data) setRerank(Object.fromEntries(rerankProviders.data.map((p) => [p.name, { ...p, api_key: "" }]))); }, [rerankProviders.data]);
  const saveLocal = useMutation({ mutationFn: () => api("/api/ai/embeddings/local", { method: "PUT", body: local }), onSuccess: () => { setLocal((x) => ({ ...x, api_key: "" })); qc.invalidateQueries({ queryKey: ["embedding-status"] }); toast("Local embedding endpoint saved"); } });
  const reembed = useMutation({ mutationFn: () => api<any>("/api/ai/embeddings/reembed", { method: "POST" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["embedding-status"] }); toast("Re-embedding queued"); } });
  const saveRerank = useMutation({ mutationFn: (p: any) => api(`/api/ai/rerank/providers/${p.name}`, { method: "PUT", body: p }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["rerank-providers"] }); toast("Reranker endpoint saved"); } });
  const ks = ["top_k", "history_len", "chunk_token_budget", "qa_threshold", "cache_threshold", "cache_ttl_days", "retrieval_min_similarity", "escalate_below", "extraction_every_n_messages", "summarize_every_n_messages", "chunk_words", "chunk_overlap_words", "max_answer_chars", "qa_text_threshold", "rerank_candidates", "keyword_ai_skip_above", "keyword_min_coverage", "keyword_top_k"];
  const payload = f ? { ...Object.fromEntries(ks.map((k) => [k, +f[k]])), embedding_provider: f.embedding_provider, embedding_model: f.embedding_model,
    keyword_search_enabled: !!f.keyword_search_enabled, keyword_ai_enabled: !!f.keyword_ai_enabled,
    rerank_provider: f.rerank_provider, rerank_model: f.rerank_model, rerank_min_score: f.rerank_min_score === "" || f.rerank_min_score == null ? null : +f.rerank_min_score } : null;
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload), () => save.mutate(payload!));
  if (loading || !f) return <Spinner />;
  return (
    <Panel title="Retrieval and token savings">
      {status.data?.hash_warning && <div className="mb-4 rounded-ctl border border-warn bg-warn-soft px-3 py-2 text-sm text-warn"><strong>Production warning:</strong> a real channel is configured while hash embeddings are active. Bangla/Banglish semantic matching will be weak.</div>}
      {status.data?.mismatch_total > 0 && <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-ctl border border-warn bg-warn-soft px-3 py-2 text-sm text-warn">
        <span><strong>{status.data.mismatch_total} items need re-embedding.</strong> Chunks: {status.data.mismatch.chunks}, Q&A: {status.data.mismatch.qa}, cache: {status.data.mismatch.cache}.</span>
        <Button size="sm" onClick={() => reembed.mutate()} loading={reembed.isPending || status.data.progress?.status === "running"}>{status.data.progress?.status === "running" ? `Re-embedding ${status.data.progress.done}/${status.data.progress.total}` : "Re-embed all"}</Button>
      </div>}
      {status.data?.progress?.status === "failed" && <div className="mb-4"><ErrorNote error={new Error(status.data.progress.error || "Re-embedding failed")} /></div>}
      <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={(e) => { e.preventDefault(); save.mutate(payload!); }}>
        <div className="space-y-3 rounded-ctl border border-line p-3 sm:col-span-2 lg:col-span-3">
          <Switch checked={!!f.keyword_search_enabled} onChange={(v) => setF({ ...f, keyword_search_enabled: v })} label="Keyword index search: find the answer through keywords, send only the matching records to the AI" />
          <p className="text-xs text-muted">The question is turned into keywords, looked up in the keyword index (Knowledge base page), and only the records that match go to the answer step: fewer tokens, a faster and more precise answer. The old vector + keyword search is the fallback when nothing matches.</p>
          {f.keyword_search_enabled && <>
            <Switch checked={!!f.keyword_ai_enabled} onChange={(v) => setF({ ...f, keyword_ai_enabled: v })} label="Let the AI work out the keywords (set the “Keyword extraction” route under Model routing; without it the free word rules are used)" />
            <div className="grid gap-3 sm:grid-cols-3">
              <NumField label="Skip the AI above coverage" hint="If the free keywords already cover this much of the question (0 to 1), no AI call is made. 0 = always ask the AI." k="keyword_ai_skip_above" s={s} f={f} setF={setF} step="0.05" />
              <NumField label="Minimum keyword coverage" hint="A record must cover at least this share of the question's keywords." k="keyword_min_coverage" s={s} f={f} setF={setF} step="0.05" />
              <NumField label="Records sent to the answer step" k="keyword_top_k" s={s} f={f} setF={setF} />
            </div></>}
        </div>
        <NumField label="Chunk size (words)" hint="Smaller chunks give more precise answers. Click “Process all again” in the Knowledge base after changing it." k="chunk_words" s={s} f={f} setF={setF} />
        <NumField label="Chunk overlap (words)" hint="Words repeated between neighbouring chunks so no sentence is cut off." k="chunk_overlap_words" s={s} f={f} setF={setF} />
        <NumField label="Maximum answer length (characters)" hint="Replies are kept short. The AI is told this limit; file and Q&A text is trimmed to whole sentences. 0 = no limit." k="max_answer_chars" s={s} f={f} setF={setF} />
        <NumField label="Q&A wording similarity" hint="A question this similar in wording (typos, spelling) gets the Q&A answer directly (0.5 to 1)." k="qa_text_threshold" s={s} f={f} setF={setF} step="0.01" />
        <NumField label="Chunks per question (top-k)" k="top_k" s={s} f={f} setF={setF} />
        <NumField label="Recent messages sent" hint="Older turns are replaced by a summary." k="history_len" s={s} f={f} setF={setF} />
        <NumField label="Knowledge token budget" hint="Maximum tokens of retrieved text per question." k="chunk_token_budget" s={s} f={f} setF={setF} />
        <NumField label="Q&A shortcut similarity" hint="Answer without AI at or above this score (0.5 to 1)." k="qa_threshold" s={s} f={f} setF={setF} step="0.01" />
        <NumField label="Cache similarity" k="cache_threshold" s={s} f={f} setF={setF} step="0.01" />
        <NumField label="Cache lifetime (days)" k="cache_ttl_days" s={s} f={f} setF={setF} />
        <NumField label="Minimum knowledge match" hint="Below this, the assistant says it does not know and offers a person." k="retrieval_min_similarity" s={s} f={f} setF={setF} step="0.01" />
        <NumField label="Use the large model below" hint="Match score under which the large model is used." k="escalate_below" s={s} f={f} setF={setF} step="0.01" />
        <Field label="Cross-encoder reranker" hint="Optional second-pass ordering after cheap hybrid retrieval.">{(i) => <Select id={i} value={f.rerank_provider} onChange={(e) => setF({ ...f, rerank_provider: e.target.value })}><option value="none">none (hybrid score only)</option><option value="cohere">Cohere</option><option value="local">local / self-hosted</option></Select>}</Field>
        <Field label="Rerank model">{(i) => <Input id={i} value={f.rerank_model} onChange={(e) => setF({ ...f, rerank_model: e.target.value })} />}</Field>
        <NumField label="Rerank candidates" hint="How many pre-filtered chunks are sent to the reranker (default 20)." k="rerank_candidates" s={s} f={f} setF={setF} />
        <Field label="Minimum rerank score" hint="Blank uses provider default: Cohere 0.2, local 0.0.">{(i) => <Input id={i} type="number" min="0" max="1" step="0.01" value={f.rerank_min_score ?? ""} placeholder="auto" onChange={(e) => setF({ ...f, rerank_min_score: e.target.value })} />}</Field>
        {f.rerank_provider !== "none" && rerank[f.rerank_provider] && (() => { const p = rerank[f.rerank_provider]; return <>
          <Field label={`${f.rerank_provider === "cohere" ? "Cohere" : "Local"} rerank base URL`} hint={f.rerank_provider === "local" ? "/rerank is appended." : "/v2/rerank is appended."}>{(i) => <Input id={i} value={p.base_url} onChange={(e) => setRerank({ ...rerank, [p.name]: { ...p, base_url: e.target.value } })} />}</Field>
          <Field label="Rerank API key" hint={p.has_key ? "Saved securely; leave blank to keep it." : f.rerank_provider === "local" ? "Optional for local endpoints." : "Required for Cohere."}>{(i) => <SecretInput id={i} configured={p.has_key} value={p.api_key} onChange={(e) => setRerank({ ...rerank, [p.name]: { ...p, api_key: e.target.value } })} />}</Field>
          <div className="self-end pb-2"><Button size="sm" onClick={() => saveRerank.mutate(p)} loading={saveRerank.isPending}>Save reranker endpoint</Button></div>
        </>; })()}
        <NumField label="Extract profile details every N messages" hint="0 turns extraction off." k="extraction_every_n_messages" s={s} f={f} setF={setF} />
        <NumField label="Summarize after N older messages" k="summarize_every_n_messages" s={s} f={f} setF={setF} />
        <Field label="Embedding provider" hint="Use local for a private OpenAI-compatible bge-m3 endpoint.">{(i) => <Select id={i} value={f.embedding_provider} onChange={(e) => setF({ ...f, embedding_provider: e.target.value })}><option value="hash">hash (testing only)</option><option value="openai">openai</option><option value="google">google</option><option value="local">local (self-hosted)</option></Select>}</Field>
        <Field label="Embedding model" hint="After changing provider/model, use Re-embed all.">{(i) => <Input id={i} value={f.embedding_model} onChange={(e) => setF({ ...f, embedding_model: e.target.value })} />}</Field>
        {f.embedding_provider === "local" && <>
          <Field label="Local base URL" hint="OpenAI-compatible server; /v1/embeddings is appended.">{(i) => <Input id={i} placeholder="http://host.docker.internal:7997" value={local.base_url} onChange={(e) => setLocal({ ...local, base_url: e.target.value })} />}</Field>
          <Field label="Local API key" hint={status.data?.local?.has_key ? "Saved securely; leave blank to keep it." : "Optional if your local endpoint has no authentication."}>{(i) => <SecretInput id={i} configured={status.data?.local?.has_key} value={local.api_key} onChange={(e) => setLocal({ ...local, api_key: e.target.value })} />}</Field>
          <div className="self-end pb-2"><Button size="sm" onClick={() => saveLocal.mutate()} loading={saveLocal.isPending} disabled={!local.base_url.trim()}>Save local endpoint</Button></div>
        </>}
        <div className="sm:col-span-2 lg:col-span-3"><ErrorNote error={save.error} /><div className="text-right"><Button type="submit" variant="primary" loading={save.isPending}>Save</Button></div></div>
      </form>
    </Panel>
  );
}

function Privacy() {
  const { s, loading, save } = useSettings();
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (s) setF({ name_mode: s.name_mode, merge_policy: s.merge_policy, human_agent_tag_enabled: s.human_agent_tag_enabled, never: s.privacy_never_send, patterns: (s.privacy_custom_patterns ?? []).join("\n") }); }, [s]);
  const payload = f ? { name_mode: f.name_mode, merge_policy: f.merge_policy, human_agent_tag_enabled: f.human_agent_tag_enabled, privacy_never_send: f.never, privacy_custom_patterns: f.patterns.split("\n").map((x: string) => x.trim()).filter(Boolean) } : null;
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload), () => save.mutate(payload!));
  if (loading || !f) return <Spinner />;
  const fields = ["name", "location", "age", "profession", "language"];
  return (
    <Panel title="Privacy rules">
      <p className="mb-4 rounded-ctl bg-warn-soft px-3 py-2 text-sm text-warn">Redaction reduces what leaves your system but cannot guarantee zero leakage of personal context. For sensitive use, choose a provider with a zero-retention agreement or a self-hosted model, and mark only that provider as allowed to receive personal data.</p>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(payload!); }}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="How names appear to providers that may receive personal data" hint="Providers without that permission always get a placeholder.">{(i) => <Select id={i} value={f.name_mode} onChange={(e) => setF({ ...f, name_mode: e.target.value })}><option value="placeholder">Placeholder ([NAME_1])</option><option value="first_name">First name only</option><option value="none">Name not used</option></Select>}</Field>
          <Field label="When a shared phone or email matches another profile" hint="'Ask me' stops people from linking themselves to someone else's profile by typing their number.">{(i) => <Select id={i} value={f.merge_policy} onChange={(e) => setF({ ...f, merge_policy: e.target.value })}><option value="auto">Merge automatically</option><option value="suggest">Ask me to approve</option></Select>}</Field>
        </div>
        <fieldset><legend className="mb-1 text-sm font-semibold">Never send these profile fields to any provider</legend>
          <div className="flex flex-wrap gap-4">{fields.map((x) => <label key={x} className="flex items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-[rgb(var(--accent))]" checked={f.never.includes(x)} onChange={(e) => setF({ ...f, never: e.target.checked ? [...f.never, x] : f.never.filter((y: string) => y !== x) })} />{x}</label>)}</div>
          <p className="mt-1 text-xs text-muted">Phone and email are never sent in any case.</p></fieldset>
        <Field label="Custom patterns to always redact" hint="One regular expression per line, for example ORD-\d{6} for order numbers.">{(i) => <Textarea id={i} className="font-mono text-xs" value={f.patterns} onChange={(e) => setF({ ...f, patterns: e.target.value })} />}</Field>
        <Switch checked={f.human_agent_tag_enabled} onChange={(v) => setF({ ...f, human_agent_tag_enabled: v })} label="Allow staff replies up to 7 days on Messenger and Instagram (needs Meta's Human Agent permission)" />
        <ErrorNote error={save.error} />
        <div className="text-right"><Button type="submit" variant="primary" loading={save.isPending}>Save</Button></div>
      </form>
    </Panel>
  );
}

const QTYPES: [string, string, string][] = [
  ["greeting", "Greeting", "hi / salam (canned welcome)"], ["general", "General question", "not about the organisation"],
  ["org_info", "Organization information", "about us, offices, services"], ["program_date", "Program date / schedule", "kobe, schedule"],
  ["program_fee", "Program fee", "fee koto"], ["program_location", "Program location", "kothay hobe"],
  ["enrolment", "Enrolment / callback", "bhorti, call korben"], ["unknown", "Unknown question", "nothing in the knowledge"],
  ["handover", "Human handover", "the guest asked for a person"],
];
const FOLLOW_TEXTS = ["program_district_prompt", "program_district_prompt_en", "program_nearby_reply", "program_nearby_reply_en", "program_nearby_none", "program_nearby_none_en", "handover_offer", "handover_offer_en"];

function ReplyStyles() {
  const { s, loading, save } = useSettings();
  const [f, setF] = useState<any>(null);
  const [qt, setQt] = useState("program_date");
  useEffect(() => { if (s) setF({ styles: structuredClone(s.reply_styles ?? {}), program_followup_enabled: s.program_followup_enabled !== false, ...Object.fromEntries(FOLLOW_TEXTS.map((k) => [k, s[k] ?? ""])) }); }, [s]);
  // only non-empty fields are stored; a template without {answer} would be refused, so it is left out until it has one
  const clean = (st: Record<string, any>) => Object.fromEntries(Object.entries(st).map(([k, v]) => [k, Object.fromEntries(Object.entries(v).filter(([kk, vv]) => typeof vv === "string" && vv.trim() !== "" && (!kk.startsWith("template") || (vv as string).includes("{answer}")) && (kk !== "handover" || vv !== "none")))]).filter(([, v]) => Object.keys(v).length));
  const payload = f ? { reply_styles: clean(f.styles), program_followup_enabled: f.program_followup_enabled, ...Object.fromEntries(FOLLOW_TEXTS.map((k) => [k, f[k]])) } : null;
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload), () => save.mutate(payload!));
  if (loading || !f) return <Spinner />;
  const cur = f.styles[qt] ?? {};
  const setCur = (k: string, v: string) => setF({ ...f, styles: { ...f.styles, [qt]: { ...cur, [k]: v } } });
  const t = (k: string, label: string, hint?: string) => <Field label={label} hint={hint}>{(i) => <Textarea id={i} rows={2} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}</Field>;
  const badTpl = ["template", "template_en"].some((k) => (cur[k] ?? "").trim() && !(cur[k] as string).includes("{answer}"));
  return (
    <Panel title="Reply style by question type">
      <p className="mb-3 text-sm text-muted">Each reply is sorted into a question type (shown in Test chat → Reply inspector). For each type you can set a tone for AI-written answers, a template around the answer, a short follow-up message, and a handover rule. Empty fields change nothing. English versions are used for guests who write in English; Banglish gets Bangla.</p>
      <Field label="Question type">{(i) => <Select id={i} value={qt} onChange={(e) => setQt(e.target.value)}>{QTYPES.map(([k, label, hint]) => <option key={k} value={k}>{label} — {hint}{f.styles[k] && Object.keys(f.styles[k]).length ? " ✓" : ""}</option>)}</Select>}</Field>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <Field label="Tone (instruction to the AI)" hint="e.g. “Warm and short, like a helpful receptionist.” Saved Q&A answers are used as written.">{(i) => <Textarea id={i} rows={2} value={cur.tone ?? ""} onChange={(e) => setCur("tone", e.target.value)} />}</Field>
        <Field label="Handover rule" hint="Offer: add “I can connect you with a team member”. Hand over: the chat goes to the team.">{(i) => <Select id={i} value={cur.handover ?? "none"} onChange={(e) => setCur("handover", e.target.value)}><option value="none">None</option><option value="offer">Offer a team member</option><option value="auto">Hand over automatically</option></Select>}</Field>
        <Field label="Answer template (Bangla)" hint="{answer} is replaced with the answer, e.g. “{answer} আর কিছু জানতে চান?”">{(i) => <Textarea id={i} rows={2} value={cur.template ?? ""} onChange={(e) => setCur("template", e.target.value)} />}</Field>
        <Field label="Answer template (English)">{(i) => <Textarea id={i} rows={2} value={cur.template_en ?? ""} onChange={(e) => setCur("template_en", e.target.value)} />}</Field>
        <Field label="Follow-up question (Bangla)" hint="Sent as its own short message after the answer.">{(i) => <Textarea id={i} rows={2} value={cur.followup ?? ""} onChange={(e) => setCur("followup", e.target.value)} />}</Field>
        <Field label="Follow-up question (English)">{(i) => <Textarea id={i} rows={2} value={cur.followup_en ?? ""} onChange={(e) => setCur("followup_en", e.target.value)} />}</Field>
      </div>
      {badTpl && <p className="mt-2 text-sm text-danger">A template must contain {"{answer}"}; it is not saved until it does.</p>}
      <div className="mt-5 space-y-3 rounded-ctl border border-line p-3">
        <Switch checked={f.program_followup_enabled} onChange={(v) => setF({ ...f, program_followup_enabled: v })} label="After a program / course / event answer, ask once which district the guest lives in" />
        <p className="text-xs text-muted">Sent as a separate message, at most once per conversation, never after a general question, and never when the profile already has a location (then a program in their district is suggested instead, if there is one). The district is saved to the profile's location. Programs are matched to the exact district only; no “nearest” place is guessed. A phone number is never asked for here. {"{program_name}"} (put in quotation marks automatically) and {"{place}"} come from the Programs table.</p>
        {f.program_followup_enabled && <div className="grid gap-4 md:grid-cols-2">
          {t("program_district_prompt", "District question (Bangla)")}{t("program_district_prompt_en", "District question (English)")}
          {t("program_nearby_reply", "A program in their district (Bangla)", "Needs {program_name} and {place}.")}{t("program_nearby_reply_en", "A program in their district (English)")}
          {t("program_nearby_none", "No confirmed program there (Bangla)")}{t("program_nearby_none_en", "No confirmed program there (English)")}
        </div>}
        <div className="grid gap-4 md:grid-cols-2">{t("handover_offer", "Handover offer, used by the “Offer” rule (Bangla)")}{t("handover_offer_en", "Handover offer (English)")}</div>
      </div>
      <ErrorNote error={save.error} />
    </Panel>
  );
}

const LEAD_TEXTS = ["lead_prompt", "lead_prompt_en", "lead_thanks", "lead_thanks_en", "lead_declined_ack", "lead_declined_ack_en", "card_warning", "card_warning_en", "lead_complete", "lead_complete_en", "lead_anything_else", "lead_anything_else_en", "lead_explain", "lead_explain_en", "data_saving_reply", "data_saving_reply_en"];

function Messages() {
  const { s, loading, save } = useSettings();
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (s) setF({ router_enabled: !!s.router_enabled, erasure_confirm_prompt: s.erasure_confirm_prompt ?? "", erasure_confirm_prompt_en: s.erasure_confirm_prompt_en ?? "", consent_yes_ack: s.consent_yes_ack ?? "", consent_yes_ack_en: s.consent_yes_ack_en ?? "", consent_no_ack: s.consent_no_ack ?? "", consent_no_ack_en: s.consent_no_ack_en ?? "", handover_ack_en: s.handover_ack_en ?? "", no_answer_reply_en: s.no_answer_reply_en ?? "", consent_prompt_en: s.consent_prompt_en ?? "", erasure_ack_en: s.erasure_ack_en ?? "", greeting_repeat_reply: s.greeting_repeat_reply ?? "", kw: (s.handover_keywords ?? []).join("\n"), handover_ack: s.handover_ack, erasure_ack: s.erasure_ack, no_answer_reply: s.no_answer_reply, consent_prompt: s.consent_prompt, system_prompt: s.system_prompt ?? "",
    greeting_enabled: s.greeting_enabled, greeting_first_reply: s.greeting_first_reply, greeting_return_reply: s.greeting_return_reply, greeting_gap_hours: String(s.greeting_gap_hours), greeting_words: (s.greeting_words ?? []).join("\n"),
    greeting_first_reply_en: s.greeting_first_reply_en ?? "", greeting_return_reply_en: s.greeting_return_reply_en ?? "", greeting_repeat_reply_en: s.greeting_repeat_reply_en ?? "",
    conversation_retention_days: String(s.conversation_retention_days), ai_auto_resume_minutes: String(s.ai_auto_resume_minutes),
    debounce_seconds: String(s.debounce_seconds), debounce_max_seconds: String(s.debounce_max_seconds), reply_max_lines: String(s.reply_max_lines ?? 5),
    lead_capture_enabled: !!s.lead_capture_enabled, info_collection_enabled: s.info_collection_enabled !== false, ...Object.fromEntries(LEAD_TEXTS.map((k) => [k, s[k] ?? ""])) }); }, [s]);
  const payload = f ? { router_enabled: f.router_enabled, erasure_confirm_prompt: f.erasure_confirm_prompt, erasure_confirm_prompt_en: f.erasure_confirm_prompt_en, consent_yes_ack: f.consent_yes_ack, consent_yes_ack_en: f.consent_yes_ack_en, consent_no_ack: f.consent_no_ack, consent_no_ack_en: f.consent_no_ack_en, handover_ack_en: f.handover_ack_en, no_answer_reply_en: f.no_answer_reply_en, consent_prompt_en: f.consent_prompt_en, erasure_ack_en: f.erasure_ack_en, greeting_repeat_reply: f.greeting_repeat_reply, handover_keywords: f.kw.split("\n").map((x: string) => x.trim()).filter(Boolean), handover_ack: f.handover_ack, erasure_ack: f.erasure_ack, no_answer_reply: f.no_answer_reply, consent_prompt: f.consent_prompt, system_prompt: f.system_prompt.trim() || null,
    greeting_enabled: f.greeting_enabled, greeting_first_reply: f.greeting_first_reply, greeting_return_reply: f.greeting_return_reply,
    greeting_first_reply_en: f.greeting_first_reply_en, greeting_return_reply_en: f.greeting_return_reply_en, greeting_repeat_reply_en: f.greeting_repeat_reply_en,
    greeting_gap_hours: Math.max(0, Number(f.greeting_gap_hours) || 0), greeting_words: f.greeting_words.split("\n").map((x: string) => x.trim()).filter(Boolean),
    conversation_retention_days: Math.max(1, Math.round(Number(f.conversation_retention_days) || 30)),
    ai_auto_resume_minutes: Math.max(0, Number(f.ai_auto_resume_minutes) || 0),
    debounce_seconds: Math.min(30, Math.max(0, Number(f.debounce_seconds) || 0)), debounce_max_seconds: Math.min(120, Math.max(1, Number(f.debounce_max_seconds) || 10)), reply_max_lines: Math.min(40, Math.max(0, Math.round(Number(f.reply_max_lines) || 0))),
    lead_capture_enabled: f.lead_capture_enabled, info_collection_enabled: f.info_collection_enabled, ...Object.fromEntries(LEAD_TEXTS.map((k) => [k, f[k]])) } : null;
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload), () => save.mutate(payload!));
  if (loading || !f) return <Spinner />;
  const t = (k: string, label: string, hint?: string, rows = 3) => <Field label={label} hint={hint}>{(i) => <Textarea id={i} rows={rows} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}</Field>;
  return (
    <Panel title="Messages and handover">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(payload!); }}>
        <Switch checked={f.greeting_enabled} onChange={(v) => setF({ ...f, greeting_enabled: v })} label="Reply to greetings (hi, hello, salam) with a welcome message" />
        {f.greeting_enabled && <>
          {t("greeting_first_reply", "Welcome for a customer's very first message", "Sent when someone greets us for the first time. No AI call.", 2)}
          {t("greeting_return_reply", "Welcome when they greet again after a break", undefined, 2)}
          {t("greeting_repeat_reply", "Reply to another greeting within the break", "A short “yes, go ahead”, so a second “hi” never gets an “I don't know”.", 2)}
          <Field label="Break length (hours)" hint="A greeting after this many hours of silence gets the second message. Greetings inside the break go to the normal AI answer.">{(i) => <Input id={i} type="number" min={0} step="0.5" value={f.greeting_gap_hours} onChange={(e) => setF({ ...f, greeting_gap_hours: e.target.value })} />}</Field>
          <div className="grid gap-4 md:grid-cols-3">
            {t("greeting_first_reply_en", "First welcome (English)", "For a guest who writes English. Keep it formal.", 2)}
            {t("greeting_return_reply_en", "Welcome again (English)", undefined, 2)}
            {t("greeting_repeat_reply_en", "Another greeting (English)", undefined, 2)}
          </div>
          <p className="text-xs text-muted">Replies stay formal. A guest who says “নমস্কার” or “আদাব” is greeted back the same way instead of with the salam.</p>
          {t("greeting_words", "Greeting words", "One per line: every way guests say hello (Islamic, Hindu, time of day, English, Banglish, misspellings). Matching ignores case, punctuation and stretched letters (hiii, hellooo), and a message may combine several (“hi, assalamu alaikum sir”).", 10)}
        </>}
        <div className="space-y-3 rounded-ctl border border-line p-3">
          <Switch checked={f.lead_capture_enabled} onChange={(v) => setF({ ...f, lead_capture_enabled: v })} label="Ask a new guest once for full name and phone number" />
          <p className="text-xs text-muted">Asked once, in the guest's first real conversation (not on a bare “hi”), only for what their profile does not have yet — from any channel. A question asked then is answered right after they reply. If they skip or decline, they are never asked again; only a callback or enrolment that truly needs a phone asks once more, optionally. Details they share are saved to their profile; sharing them after this explanation counts as consent, so the separate consent question is not asked. {"{fields}"} is replaced with the details still missing.</p>
          {f.lead_capture_enabled && <div className="grid gap-4 md:grid-cols-2">
            {t("lead_prompt", "Request (Bangla)", undefined, 2)}{t("lead_prompt_en", "Request (English)", undefined, 2)}
            {t("lead_declined_ack", "When the guest declines (Bangla)", "Said once; the held question is answered right after.", 2)}{t("lead_declined_ack_en", "When the guest declines (English)", undefined, 2)}
            {t("lead_thanks", "After the details are saved (Bangla)", undefined, 2)}{t("lead_thanks_en", "After saving (English)", undefined, 2)}
            {t("lead_explain", "When the guest asks why or does not understand (Bangla)", "{example} becomes a sample answer for the missing details, e.g. রহিম উদ্দিন, 01XXXXXXXXX.", 2)}{t("lead_explain_en", "When they ask why / don't understand (English)", undefined, 2)}
            {t("data_saving_reply", "If the guest asks whether we keep their data (Bangla)", "The chat never says “saved” on its own; this is sent only when the guest asks.", 3)}{t("data_saving_reply_en", "If asked about their data (English)", undefined, 3)}
            {t("lead_complete", "When all details are in (Bangla)", "Thanks the guest and asks whether they want to know anything else.", 2)}{t("lead_complete_en", "When all details are in (English)", undefined, 2)}
            {t("lead_anything_else", "“Anything else?” after answering their held question (Bangla)", undefined, 2)}{t("lead_anything_else_en", "“Anything else?” (English)", undefined, 2)}
          </div>}
        </div>
        <div className="space-y-2 rounded-ctl border border-line p-3">
          <Switch checked={f.info_collection_enabled} onChange={(v) => setF({ ...f, info_collection_enabled: v })} label="Collect guest info from every message and save it to the profile" />
          <p className="text-xs text-muted">Each message is split into pieces. Pieces with personal-info words (name, age, family, study, work, health, blood group …) go to the AI, which marks each piece as guest info or a question and saves the guest info. Messages without such words cost nothing. Uses the “Profile extraction” route under Model routing.</p>
        </div>
        <div className="space-y-3 rounded-ctl border border-danger p-3">
          <p className="text-sm font-semibold">Card safety (always on)</p>
          <p className="text-xs text-muted">Card numbers, CVV, PIN and OTP are never asked for. An AI reply that asks for them is replaced with the warning below, a staff reply that asks for them is refused, and a card number a guest sends is hidden and the guest is warned. Each case shows a red alert in the Inbox and System health, and goes to the alert webhook.</p>
          <div className="grid gap-4 md:grid-cols-2">{t("card_warning", "Warning to the guest (Bangla)", undefined, 2)}{t("card_warning_en", "Warning to the guest (English)", undefined, 2)}</div>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Wait for more messages before replying (seconds)" hint="People often split one question over several messages (“hi”, “course er”, “fee koto?”). The assistant waits this long after the last one and answers them together. The guest sees “seen” and the typing bubble right away. 0 = reply to every message separately.">{(i) => <Input id={i} type="number" min={0} max={30} step="0.5" value={f.debounce_seconds} onChange={(e) => setF({ ...f, debounce_seconds: e.target.value })} />}</Field>
          <Field label="Longest wait for a burst (seconds)" hint="If the guest keeps typing, reply anyway after this long.">{(i) => <Input id={i} type="number" min={1} max={120} value={f.debounce_max_seconds} onChange={(e) => setF({ ...f, debounce_max_seconds: e.target.value })} />}</Field>
        </div>
        <Field label="Longest message (lines)" hint="A longer reply is sent as several messages of at most this many lines (a phone line is about 60 characters). Up to one extra line still goes as one message. 0 = never split.">{(i) => <Input id={i} type="number" min={0} max={40} value={f.reply_max_lines} onChange={(e) => setF({ ...f, reply_max_lines: e.target.value })} />}</Field>
        <Field label="Give the chat back to the AI after (minutes of staff silence)" hint="When staff reply from the inbox or press Take over, the AI pauses for that chat. It resumes on its own after this long without a staff message. 0 = stay paused until someone presses Resume.">{(i) => <Input id={i} type="number" min={0} value={f.ai_auto_resume_minutes} onChange={(e) => setF({ ...f, ai_auto_resume_minutes: e.target.value })} />}</Field>
        <Field label="Keep chat history for (days)" hint="Older messages are deleted every night. Customer profiles and the details saved on them are kept.">{(i) => <Input id={i} type="number" min={1} value={f.conversation_retention_days} onChange={(e) => setF({ ...f, conversation_retention_days: e.target.value })} />}</Field>
        {t("kw", "Words that hand the chat to a person", "One per line. Bangla and English both work.", 5)}
        <p className="rounded-ctl bg-sunken/60 px-3 py-2 text-sm text-muted">Each message is sent in one language: the Bangla text normally (also for Banglish such as “course fee koto?”), the English text when the guest writes in English. Leave an English box empty to always use Bangla.</p>
        <div className="grid gap-4 md:grid-cols-2">
        {t("handover_ack", "Handing over to a person (Bangla)")}{t("handover_ack_en", "Handing over to a person (English)")}
        {t("no_answer_reply_en", "When nothing matches (English)")}
        {t("no_answer_reply", "When nothing in the knowledge base matches (Bangla)", "No AI call is made for these questions, so they cost nothing.")}
        {t("consent_prompt_en", "Consent question (English)")}
        {t("consent_prompt", "Consent question (Bangla)", "Asked once, after the customer's second message and after their question is answered.")}
        {t("erasure_ack", "After 'delete my data' (Bangla)")}{t("erasure_ack_en", "After 'delete my data' (English)")}
        </div>
        <div className="space-y-3 rounded-ctl border border-line p-3">
          <Switch checked={f.router_enabled} onChange={(v) => setF({ ...f, router_enabled: v })} label="Intent router: let a cheap AI call understand requests the word lists miss" />
          <p className="text-xs text-muted">Runs only when no greeting, small-talk, saved Q&A or variable matched, so most messages still cost nothing. Set the “Intent router” route under Model routing first, and check the Evaluation results before switching it on. If the router fails, the normal rules are used. A delete request it detects is only carried out after the guest replies YES.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
        {t("erasure_confirm_prompt", "Confirm before deleting (Bangla)", "Sent when the router (not the exact phrase) thinks the guest wants their data deleted.", 2)}{t("erasure_confirm_prompt_en", "Confirm before deleting (English)", undefined, 2)}
        {t("consent_yes_ack", "After the guest agrees to saving details (Bangla)", undefined, 2)}{t("consent_yes_ack_en", "After agreeing (English)", undefined, 2)}
        {t("consent_no_ack", "After the guest declines (Bangla)", undefined, 2)}{t("consent_no_ack_en", "After declining (English)", undefined, 2)}
        </div>
        {t("system_prompt", "System prompt override (advanced)", "Leave empty to use the built-in prompt. Keep it short: it is sent with every AI call.", 8)}
        <ErrorNote error={save.error} />
        <div className="text-right"><Button type="submit" variant="primary" loading={save.isPending}>Save</Button></div>
      </form>
    </Panel>
  );
}

type ChatItem = { questions: string; answer: string; answer_en: string };

function SmallTalk() {
  const { s, loading, save } = useSettings();
  const [enabled, setEnabled] = useState(true);
  const [items, setItems] = useState<ChatItem[] | null>(null);
  useEffect(() => { if (s) { setEnabled(s.smalltalk_enabled); setItems((s.smalltalk ?? []).map((x: any) => ({ questions: x.questions.join("\n"), answer: x.answer, answer_en: x.answer_en ?? "" }))); } }, [s]);
  const payload = items ? { smalltalk_enabled: enabled, smalltalk: items
    .map((x) => ({ questions: x.questions.split("\n").map((q) => q.trim()).filter(Boolean), answer: x.answer.trim(), ...(x.answer_en.trim() ? { answer_en: x.answer_en.trim() } : {}) }))
    .filter((x) => x.questions.length && x.answer) } : null;
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload), () => save.mutate(payload!));
  if (loading || !items) return <Spinner />;
  const set = (i: number, patch: Partial<ChatItem>) => setItems(items.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  return (
    <Panel title="Small talk (ready answers)" actions={<Button size="sm" onClick={() => setItems([...items, { questions: "", answer: "", answer_en: "" }])}><Plus className="h-4 w-4" aria-hidden />Add</Button>}>
      <div className="space-y-4">
        <Switch checked={enabled} onChange={setEnabled} label="Answer everyday questions (who are you, how are you, thanks...) instantly, without an AI call" />
        <p className="text-sm text-muted">Each answer is sent when a short message matches one of its phrasings (case, punctuation and small typos are ignored; a leading "hi" is fine). Longer questions still go to the knowledge base.</p>
        {enabled && items.map((x, i) => (
          <div key={i} className="grid gap-3 rounded-ctl border border-line p-3 md:grid-cols-[1fr_1.4fr_auto]">
            <Field label="When the guest writes" hint="One phrasing per line: English, Bangla and Banglish">{(id) => <Textarea id={id} persistDraft={false} rows={4} value={x.questions} onChange={(e) => set(i, { questions: e.target.value })} />}</Field>
            <div className="space-y-2">
              <Field label="Reply (Bangla)">{(id) => <Textarea id={id} persistDraft={false} rows={3} value={x.answer} onChange={(e) => set(i, { answer: e.target.value })} />}</Field>
              <Field label="Reply in English (optional)" hint="Sent when the guest writes in English.">{(id) => <Textarea id={id} persistDraft={false} rows={2} value={x.answer_en} onChange={(e) => set(i, { answer_en: e.target.value })} />}</Field>
            </div>
            <IconButton label="Remove this answer" className="self-start md:mt-6" onClick={() => setItems(items.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" aria-hidden /></IconButton>
          </div>
        ))}
        <ErrorNote error={save.error} />
      </div>
    </Panel>
  );
}

function Alerts() {
  const { s, loading, save } = useSettings();
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (s) setF({ url: s.alert_webhook_url ?? "", stuck: String(s.alert_stuck_minutes), failed: String(s.alert_failed_sends_per_hour) }); }, [s]);
  const payload = f ? { alert_webhook_url: f.url.trim(), alert_stuck_minutes: Math.max(1, Number(f.stuck) || 5), alert_failed_sends_per_hour: Math.max(0, Math.round(Number(f.failed) || 0)) } : null;
  const valid = !f || !f.url.trim() || f.url.trim().startsWith("https://");
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload) && valid, () => save.mutate(payload!));
  if (loading || !f) return <Spinner />;
  return (
    <Panel title="Alerts">
      <p className="mb-3 text-sm text-muted">Every 5 minutes the system checks for messages stuck in the queue, replies that fail to send, and the budget alert. Problems are posted to the webhook below, at most once per problem per hour.</p>
      <div className="grid gap-3 md:grid-cols-[2fr_1fr_1fr]">
        <Field label="Alert webhook URL" hint={valid ? "Slack incoming webhook, or Telegram https://api.telegram.org/bot<token>/sendMessage?chat_id=<id>. Empty = no alerts." : "Must start with https://"}>{(i) => <Input id={i} persistDraft={false} placeholder="https://hooks.slack.com/services/..." value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} />}</Field>
        <Field label="Stuck after (minutes)">{(i) => <Input id={i} type="number" min={1} value={f.stuck} onChange={(e) => setF({ ...f, stuck: e.target.value })} />}</Field>
        <Field label="Failed sends per hour">{(i) => <Input id={i} type="number" min={0} value={f.failed} onChange={(e) => setF({ ...f, failed: e.target.value })} />}</Field>
      </div>
      <ErrorNote error={save.error} />
    </Panel>
  );
}

const TOOL_INFO: Record<string, [string, string]> = {
  create_lead: ["Save a lead", "When someone wants to join a course or service: saves their interest (and name/phone with consent) on the profile and leaves a note for staff in the Inbox."],
  request_callback: ["Request a callback", "When someone asks for a phone call: hands the chat to staff with the reason and preferred time."],
  current_datetime: ["Current date & time", "When an answer depends on today's date, the weekday or the time: reads the clock (Bangladesh time, UTC+6). Read-only, so no confirmation is asked."],
};
const TOOL_TEXTS: [string, string][] = [["tool_confirm_prompt", "Confirmation question ({action} = what will be done)"], ["tool_declined_ack", "When the guest says no"],
  ["tool_lead_ack", "After a lead is saved"], ["tool_callback_ack", "After a callback is requested ({time} = preferred time)"]];

function Tools() {
  const { s, loading, save } = useSettings();
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (s) setF({ enabled: { ...(s.tools_enabled ?? {}) }, ...Object.fromEntries(TOOL_TEXTS.flatMap(([k]) => [[k, s[k] ?? ""], [`${k}_en`, s[`${k}_en`] ?? ""]])) }); }, [s]);
  const payload = f ? { tools_enabled: f.enabled, ...Object.fromEntries(TOOL_TEXTS.flatMap(([k]) => [[k, f[k]], [`${k}_en`, f[`${k}_en`]]])) } : null;
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload), () => save.mutate(payload!));
  if (loading || !f) return <Spinner />;
  return (
    <Panel title="Actions the assistant can take (tools)">
      <p className="mb-3 text-sm text-muted">With an AI model configured, the assistant can do these on its own initiative. Actions that change something always ask the guest to confirm first (“reply YES”) and nothing happens without that yes. Every call is logged.</p>
      <ul className="mb-4 space-y-3">{Object.entries(TOOL_INFO).map(([k, [label, hint]]) => (
        <li key={k} className="rounded-ctl border border-line p-3"><Switch checked={!!f.enabled[k]} onChange={(v) => setF({ ...f, enabled: { ...f.enabled, [k]: v } })} label={label} /><p className="mt-1 text-xs text-muted">{hint}</p></li>))}</ul>
      <div className="grid gap-3 md:grid-cols-2">{TOOL_TEXTS.flatMap(([k, label]) => [
        <Field key={k} label={`${label} (Bangla)`}>{(i) => <Textarea id={i} rows={2} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}</Field>,
        <Field key={`${k}_en`} label={`${label} (English)`}>{(i) => <Textarea id={i} rows={2} value={f[`${k}_en`]} onChange={(e) => setF({ ...f, [`${k}_en`]: e.target.value })} />}</Field>])}</div>
      <ErrorNote error={save.error} />
    </Panel>
  );
}
