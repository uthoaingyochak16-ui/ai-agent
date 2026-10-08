import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, ErrorNote, Field, IconButton, Input, Meter, PageHeader, Panel, SecretInput, Select, Spinner, Switch, Tabs, TableWrap, Td, Textarea, Th, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useDebouncedAutosave } from "../lib/autosave";
import { num, usd, when } from "../lib/format";

const PROVIDERS = ["anthropic", "openai", "google"];
const TASKS: Record<string, string> = { answer: "Answer (standard)", answer_large: "Answer (large, for hard questions)", extraction: "Profile extraction", summarization: "Conversation summary" };
type Tab = "providers" | "routing" | "budget" | "retrieval" | "privacy" | "messages";
const AI_TABS: Tab[] = ["providers", "routing", "budget", "retrieval", "privacy", "messages"];

function useSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["ai-settings"], queryFn: () => api("/api/ai/settings") });
  const save = useMutation({ mutationFn: (body: Record<string, unknown>) => api("/api/ai/settings", { method: "PUT", body }), onSuccess: (d) => { qc.setQueryData(["ai-settings"], d); qc.invalidateQueries({ queryKey: ["dashboard"] }); toast("Settings saved"); } });
  return { s: q.data, loading: q.isLoading, save };
}

export default function AISettings() {
  const [tab, setTabState] = useState<Tab>(() => {
    const saved = localStorage.getItem("assistant:ai-settings-tab") as Tab | null;
    return saved && AI_TABS.includes(saved) ? saved : "providers";
  });
  const setTab = (next: Tab) => { localStorage.setItem("assistant:ai-settings-tab", next); setTabState(next); };
  return (
    <>
      <PageHeader title="AI settings" subtitle="Which providers and models answer, what they cost, and what is allowed to leave your system." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "providers", label: "Providers and prices" }, { id: "routing", label: "Model routing" }, { id: "budget", label: "Budget and cost" }, { id: "retrieval", label: "Retrieval" }, { id: "privacy", label: "Privacy" }, { id: "messages", label: "Messages and handover" }]} />
      <div hidden={tab !== "providers"}><Providers /></div>
      <div hidden={tab !== "routing"}><Routing /></div>
      <div hidden={tab !== "budget"}><Budget /></div>
      <div hidden={tab !== "retrieval"}><Retrieval /></div>
      <div hidden={tab !== "privacy"}><Privacy /></div>
      <div hidden={tab !== "messages"} className="space-y-5"><Messages /><SmallTalk /></div>
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
  const save = useMutation({ mutationFn: (b: any) => api(`/api/ai/providers/${p.name}`, { method: "PUT", body: b }), onSuccess: () => { toast("Provider saved"); setKey(""); qc.invalidateQueries({ queryKey: ["providers"] }); } });
  const test = useMutation({ mutationFn: () => api(`/api/ai/providers/${p.name}/test`, { body: { model } }), onSuccess: setRes });
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
      <form className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3" onSubmit={(e) => { e.preventDefault(); setRes(null); test.mutate(); }}>
        <Field label="Test with model" className="min-w-[220px] flex-1">{(i) => <Input id={i} required placeholder="model name" value={model} onChange={(e) => setModel(e.target.value)} />}</Field>
        <Button type="submit" loading={test.isPending} disabled={!p.has_key}>Test connection</Button>
        {res && <span className={`text-sm ${res.ok ? "text-ok" : "text-danger"}`}>{res.ok ? `Works (${res.latency_ms} ms)` : res.error}</span>}
      </form>
    </Panel>
  );
}

/* ---------------------------------------------------------------- routing */
function Routing() {
  const routes = useQuery({ queryKey: ["routes"], queryFn: () => api<any[]>("/api/ai/routes") });
  const models = useQuery({ queryKey: ["models"], queryFn: () => api<any[]>("/api/ai/models") });
  if (routes.isLoading) return <Spinner />;
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted">Use a small, cheap model for routine answers, extraction and summaries. The large model is used only when the knowledge match is weak or the question is complex. If the primary model fails, times out or is rate limited, the fallback is used automatically.</p>
      <datalist id="model-names">{models.data?.map((m) => <option key={m.id} value={m.model} />)}</datalist>
      {routes.data?.map((r) => <RouteRow key={r.task} r={r} />)}
    </div>
  );
}
function RouteRow({ r }: { r: any }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ primary_provider: r.primary_provider ?? "anthropic", primary_model: r.primary_model ?? "", fallback_provider: r.fallback_provider ?? "", fallback_model: r.fallback_model ?? "", max_tokens: r.max_tokens ?? 400, temperature: r.temperature ?? 0.2 });
  const save = useMutation({ mutationFn: () => api(`/api/ai/routes/${r.task}`, { method: "PUT", body: f }), onSuccess: () => { toast("Route saved"); qc.invalidateQueries({ queryKey: ["routes"] }); } });
  useDebouncedAutosave(JSON.stringify(f), true, () => { if (f.primary_model) save.mutate(); });
  return (
    <Panel title={TASKS[r.task]} actions={<Badge tone={r.configured ? "ok" : "warn"}>{r.configured ? "Configured" : "Not set"}</Badge>}>
      <form className="grid items-end gap-3 md:grid-cols-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Primary provider">{(i) => <Select id={i} value={f.primary_provider} onChange={(e) => setF({ ...f, primary_provider: e.target.value })}>{PROVIDERS.map((p) => <option key={p}>{p}</option>)}</Select>}</Field>
        <Field label="Primary model" className="md:col-span-1">{(i) => <Input id={i} list="model-names" required value={f.primary_model} onChange={(e) => setF({ ...f, primary_model: e.target.value })} />}</Field>
        <Field label="Fallback provider">{(i) => <Select id={i} value={f.fallback_provider} onChange={(e) => setF({ ...f, fallback_provider: e.target.value })}><option value="">None</option>{PROVIDERS.map((p) => <option key={p}>{p}</option>)}</Select>}</Field>
        <Field label="Fallback model">{(i) => <Input id={i} list="model-names" value={f.fallback_model} onChange={(e) => setF({ ...f, fallback_model: e.target.value })} />}</Field>
        <Field label="Max output tokens" hint="Keep answers short to save cost.">{(i) => <Input id={i} type="number" min={16} max={8000} value={f.max_tokens} onChange={(e) => setF({ ...f, max_tokens: +e.target.value })} />}</Field>
        <Field label="Temperature">{(i) => <Input id={i} type="number" step="0.1" min={0} max={2} value={f.temperature} onChange={(e) => setF({ ...f, temperature: +e.target.value })} />}</Field>
        <div className="md:col-span-2 md:text-right"><Button type="submit" variant="primary" loading={save.isPending}>Save route</Button></div>
      </form>
      <div className="mt-2"><ErrorNote error={save.error} /></div>
    </Panel>
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
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (s) setF(s); }, [s]);
  const ks = ["top_k", "history_len", "chunk_token_budget", "qa_threshold", "cache_threshold", "cache_ttl_days", "retrieval_min_similarity", "escalate_below", "extraction_every_n_messages", "summarize_every_n_messages", "chunk_words", "chunk_overlap_words", "max_answer_chars", "qa_text_threshold"];
  const payload = f ? { ...Object.fromEntries(ks.map((k) => [k, +f[k]])), embedding_provider: f.embedding_provider, embedding_model: f.embedding_model } : null;
  useDebouncedAutosave(JSON.stringify(payload), Boolean(payload), () => save.mutate(payload!));
  if (loading || !f) return <Spinner />;
  return (
    <Panel title="Retrieval and token savings">
      <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={(e) => { e.preventDefault(); save.mutate(payload!); }}>
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
        <NumField label="Extract profile details every N messages" hint="0 turns extraction off." k="extraction_every_n_messages" s={s} f={f} setF={setF} />
        <NumField label="Summarize after N older messages" k="summarize_every_n_messages" s={s} f={f} setF={setF} />
        <Field label="Embedding provider" hint="'hash' works offline for testing only. Use openai or google for real meaning-based search.">{(i) => <Select id={i} value={f.embedding_provider} onChange={(e) => setF({ ...f, embedding_provider: e.target.value })}><option value="hash">hash (testing)</option><option value="openai">openai</option><option value="google">google</option></Select>}</Field>
        <Field label="Embedding model" hint="Changing the provider or model means re-uploading documents and re-saving Q&A so all vectors match.">{(i) => <Input id={i} value={f.embedding_model} onChange={(e) => setF({ ...f, embedding_model: e.target.value })} />}</Field>
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

function Messages() {
  const { s, loading, save } = useSettings();
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (s) setF({ handover_ack_en: s.handover_ack_en ?? "", no_answer_reply_en: s.no_answer_reply_en ?? "", consent_prompt_en: s.consent_prompt_en ?? "", erasure_ack_en: s.erasure_ack_en ?? "", greeting_repeat_reply: s.greeting_repeat_reply ?? "", kw: (s.handover_keywords ?? []).join("\n"), handover_ack: s.handover_ack, erasure_ack: s.erasure_ack, no_answer_reply: s.no_answer_reply, consent_prompt: s.consent_prompt, system_prompt: s.system_prompt ?? "",
    greeting_enabled: s.greeting_enabled, greeting_first_reply: s.greeting_first_reply, greeting_return_reply: s.greeting_return_reply, greeting_gap_hours: String(s.greeting_gap_hours), greeting_words: (s.greeting_words ?? []).join("\n"),
    conversation_retention_days: String(s.conversation_retention_days), ai_auto_resume_minutes: String(s.ai_auto_resume_minutes) }); }, [s]);
  const payload = f ? { handover_ack_en: f.handover_ack_en, no_answer_reply_en: f.no_answer_reply_en, consent_prompt_en: f.consent_prompt_en, erasure_ack_en: f.erasure_ack_en, greeting_repeat_reply: f.greeting_repeat_reply, handover_keywords: f.kw.split("\n").map((x: string) => x.trim()).filter(Boolean), handover_ack: f.handover_ack, erasure_ack: f.erasure_ack, no_answer_reply: f.no_answer_reply, consent_prompt: f.consent_prompt, system_prompt: f.system_prompt.trim() || null,
    greeting_enabled: f.greeting_enabled, greeting_first_reply: f.greeting_first_reply, greeting_return_reply: f.greeting_return_reply,
    greeting_gap_hours: Math.max(0, Number(f.greeting_gap_hours) || 0), greeting_words: f.greeting_words.split("\n").map((x: string) => x.trim()).filter(Boolean),
    conversation_retention_days: Math.max(1, Math.round(Number(f.conversation_retention_days) || 30)),
    ai_auto_resume_minutes: Math.max(0, Number(f.ai_auto_resume_minutes) || 0) } : null;
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
          {t("greeting_words", "Greeting words", "One per line. Matching ignores case, punctuation and stretched letters (hiii, hellooo).", 5)}
        </>}
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
