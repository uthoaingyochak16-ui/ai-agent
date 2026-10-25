import { Check, Copy, Facebook, Instagram, Loader2, Phone, X } from "lucide-react";
import { createContext, forwardRef, useCallback, useContext, useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type ChangeEvent, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { CHANNEL_LABEL } from "../lib/format";

export const cx = (...a: (string | false | null | undefined)[]) => a.filter(Boolean).join(" ");

/* ---------- buttons ---------- */
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md"; loading?: boolean };
export function Button({ variant = "secondary", size = "md", loading, className, children, disabled, ...p }: BtnProps) {
  const v = {
    primary: "bg-accent text-on-accent hover:bg-accent-strong border-transparent shadow-[inset_0_1px_0_rgb(255_255_255/0.16),0_1px_2px_rgb(var(--shadow)/0.25)]",
    secondary: "bg-surface text-ink border-line hover:border-ink/25 hover:bg-sunken/60 shadow-sm",
    ghost: "bg-transparent text-ink border-transparent hover:bg-sunken",
    danger: "bg-danger text-white border-transparent hover:opacity-90 shadow-sm",
  }[variant];
  return (
    <button type="button" {...p} disabled={disabled || loading}
      className={cx("inline-flex items-center justify-center gap-2 rounded-ctl border font-semibold transition-[background,border-color,box-shadow,transform] duration-150 active:translate-y-px disabled:opacity-50 disabled:cursor-not-allowed", size === "sm" ? "px-2.5 py-1 text-[13px]" : "px-4 py-2 text-sm", v, className)}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}
export function IconButton({ label, children, className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return <button type="button" {...p} aria-label={label} title={label} className={cx("inline-flex h-9 w-9 items-center justify-center rounded-ctl text-muted transition-colors hover:bg-sunken hover:text-ink", className)}>{children}</button>;
}

/* ---------- form fields ---------- */
export function Field({ label, hint, children, className }: { label: string; hint?: string; children: (id: string) => ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1.5 block text-[13px] font-semibold text-ink/90">{label}</label>
      {children(id)}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
const ctl = "w-full rounded-ctl border border-line bg-surface px-3 py-2 text-sm shadow-[inset_0_1px_2px_rgb(var(--shadow)/0.04)] transition-shadow placeholder:text-muted/70 hover:border-ink/25 focus:border-accent focus:outline-none focus:ring-4 focus:ring-accent/15 disabled:opacity-60";
type DraftProps = { draftKey?: string; persistDraft?: boolean };
const draftStorageKey = (kind: string, p: { draftKey?: string; name?: string; id?: string; placeholder?: string; "aria-label"?: string }) => {
  const field = p.draftKey || p.name || p["aria-label"] || p.placeholder || p.id;
  return field ? `assistant:draft:${window.location.pathname}:${kind}:${field}` : null;
};
const fakeChange = <T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(value: string) => ({ target: { value }, currentTarget: { value } } as ChangeEvent<T>);

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & DraftProps>(function Input({ draftKey, persistDraft = true, ...p }, ref) {
  const key = draftStorageKey("input", { ...p, draftKey });
  const previous = useRef(p.value);
  const mounted = useRef(false);
  const canPersist = persistDraft && p.type !== "password" && p.type !== "file" && p.type !== "hidden" && Boolean(key);
  useEffect(() => {
    if (canPersist && (p.value === "" || p.value == null)) {
      const saved = localStorage.getItem(key!);
      if (saved != null) p.onChange?.(fakeChange<HTMLInputElement>(saved));
    }
    mounted.current = true;
  }, []); // restore this field once
  useEffect(() => {
    if (mounted.current && canPersist && previous.current !== "" && p.value === "") localStorage.removeItem(key!);
    previous.current = p.value;
  }, [p.value, canPersist, key]);
  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (canPersist) e.target.value ? localStorage.setItem(key!, e.target.value) : localStorage.removeItem(key!);
    p.onChange?.(e);
  };
  return <input {...p} ref={ref} onChange={onChange} className={cx(ctl, p.className)} />;
});
export function Textarea({ draftKey, persistDraft = true, ...p }: TextareaHTMLAttributes<HTMLTextAreaElement> & DraftProps) {
  const key = draftStorageKey("textarea", { ...p, draftKey });
  const previous = useRef(p.value);
  const mounted = useRef(false);
  useEffect(() => {
    if (persistDraft && key && (p.value === "" || p.value == null)) {
      const saved = localStorage.getItem(key);
      if (saved != null) p.onChange?.(fakeChange<HTMLTextAreaElement>(saved));
    }
    mounted.current = true;
  }, []);
  useEffect(() => {
    if (mounted.current && persistDraft && key && previous.current !== "" && p.value === "") localStorage.removeItem(key);
    previous.current = p.value;
  }, [p.value, persistDraft, key]);
  const onChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    if (persistDraft && key) e.target.value ? localStorage.setItem(key, e.target.value) : localStorage.removeItem(key);
    p.onChange?.(e);
  };
  return <textarea {...p} onChange={onChange} className={cx(ctl, "min-h-[84px]", p.className)} />;
}
export function Select({ draftKey, persistDraft = true, ...p }: SelectHTMLAttributes<HTMLSelectElement> & DraftProps) {
  const key = draftStorageKey("select", { ...p, draftKey });
  useEffect(() => {
    if (persistDraft && key && (p.value === "" || p.value == null)) {
      const saved = localStorage.getItem(key);
      if (saved != null) p.onChange?.(fakeChange<HTMLSelectElement>(saved));
    }
  }, []);
  const onChange = (e: ChangeEvent<HTMLSelectElement>) => {
    if (persistDraft && key) localStorage.setItem(key, e.target.value);
    p.onChange?.(e);
  };
  return <select {...p} onChange={onChange} className={cx(ctl, "pr-8", p.className)} />;
}

export function SecretInput({ configured, value, onChange, ...p }: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & { configured: boolean; value: string; onChange: (e: ChangeEvent<HTMLInputElement>) => void }) {
  const [editing, setEditing] = useState(false);
  const masked = configured && !editing && !value;
  return <Input {...p} type={masked ? "text" : "password"} persistDraft={false} autoComplete="off" value={masked ? "•••••••••••• (saved)" : value}
    readOnly={masked} onFocus={() => setEditing(true)} onBlur={(e) => { if (!value) setEditing(false); p.onBlur?.(e); }} onChange={onChange} />;
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
      <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
        className={cx("relative h-6 w-10 shrink-0 rounded-full border transition-colors disabled:opacity-50", checked ? "border-accent bg-accent" : "border-line bg-sunken")}>
        <span className={cx("absolute top-0.5 h-[18px] w-[18px] rounded-full bg-white shadow transition-all", checked ? "left-[19px]" : "left-0.5")} />
      </button>
      <span aria-hidden>{label}</span>
    </label>
  );
}

/* ---------- display ---------- */
const tones = { neutral: "bg-sunken text-muted", ok: "bg-ok-soft text-ok", warn: "bg-warn-soft text-warn", danger: "bg-danger-soft text-danger", blue: "bg-blue-soft text-blue", accent: "bg-accent-soft text-accent-strong" };
export function Badge({ tone = "neutral", children, className }: { tone?: keyof typeof tones; children: ReactNode; className?: string }) {
  return <span className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ring-current/15", tones[tone], className)}>{children}</span>;
}
export function ChannelBadge({ channel }: { channel: string }) {
  const Icon = channel === "instagram" ? Instagram : channel === "whatsapp" ? Phone : Facebook;
  return <Badge tone={channel === "whatsapp" ? "ok" : channel === "instagram" ? "accent" : "blue"}><Icon className="h-3 w-3" aria-hidden />{CHANNEL_LABEL[channel] ?? channel}</Badge>;
}
/** Customer photo from the channel, or initials when there is none (WhatsApp) or the URL has expired. */
export function Avatar({ name, src, size = 40 }: { name?: string | null; src?: string | null; size?: number }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [src]);
  const initials = (name ?? "").trim().split(/\s+/).filter(Boolean).slice(0, 2).map((w) => [...w][0]).join("").toUpperCase() || "?";
  const style = { width: size, height: size, fontSize: Math.round(size * 0.38) };
  if (src && !broken) return <img src={src} alt="" referrerPolicy="no-referrer" loading="lazy" onError={() => setBroken(true)} style={style} className="shrink-0 rounded-full border border-line bg-sunken object-cover ring-2 ring-surface" />;
  return <span aria-hidden style={style} className="inline-flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-accent-soft to-brass-soft font-display font-semibold text-accent-strong ring-2 ring-surface">{initials}</span>;
}
export function Spinner({ label = "Loading" }: { label?: string }) {
  return <div role="status" className="flex items-center gap-2 py-6 text-sm text-muted"><Loader2 className="h-4 w-4 animate-spin" aria-hidden />{label}</div>;
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="rounded-panel border border-dashed border-line bg-surface/50 px-6 py-12 text-center"><span aria-hidden className="mx-auto mb-3 block h-1 w-8 rounded-full bg-brass/70" /><p className="font-display text-base font-semibold">{title}</p>{children && <p className="mx-auto mt-1 max-w-md text-sm text-muted">{children}</p>}</div>;
}
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <p role="alert" className="rounded-ctl border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger">{error instanceof Error ? error.message : String(error)}</p>;
}
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="rise mb-6 flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-[28px] font-semibold leading-tight">{title}</h1>{subtitle && <p className="mt-1 max-w-2xl text-sm text-muted">{subtitle}</p>}</div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
export const Panel = ({ title, actions, children, className }: { title?: string; actions?: ReactNode; children: ReactNode; className?: string }) => (
  <section className={cx("rounded-panel border border-line bg-surface shadow-panel", className)}>
    {(title || actions) && <header className="flex items-center justify-between gap-2 border-b border-line px-5 py-3.5"><h2 className="text-[17px] font-semibold">{title}</h2>{actions}</header>}
    <div className="p-5">{children}</div>
  </section>
);
export function Meter({ value, tone = "accent", label }: { value: number; tone?: "accent" | "warn" | "danger" | "blue"; label: string }) {
  const color = { accent: "bg-accent", warn: "bg-warn", danger: "bg-danger", blue: "bg-blue" }[tone];
  return <div role="meter" aria-label={label} aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100} className="h-2 w-full overflow-hidden rounded-full bg-sunken"><div className={cx("h-full rounded-full transition-[width] duration-700", color)} style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }} /></div>;
}
export const Th = ({ children, className }: { children?: ReactNode; className?: string }) => <th scope="col" className={cx("px-3.5 py-2.5 text-left text-xs font-semibold text-muted", className)}>{children}</th>;
export const Td = ({ children, className, ...p }: { children?: ReactNode; className?: string } & React.TdHTMLAttributes<HTMLTableCellElement>) => <td {...p} className={cx("px-3.5 py-3 align-top", className)}>{children}</td>;
export const TableWrap = ({ children, compact }: { children: ReactNode; compact?: boolean }) => (
  <div className="overflow-x-auto rounded-panel border border-line bg-surface shadow-panel">
    <table className={cx("w-full text-sm [&_tbody_tr]:border-t [&_tbody_tr]:border-line [&_tbody_tr]:transition-colors [&_tbody_tr:hover]:bg-sunken/40 [&_thead]:bg-sunken/70", !compact && "min-w-[640px]")}>{children}</table>
  </div>
);

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div role="tablist" className="mb-5 flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={cx("-mb-px inline-flex items-center gap-2 whitespace-nowrap rounded-t-ctl border-b-2 px-3.5 py-2.5 text-sm font-semibold transition-colors", value === t.id ? "border-brass text-ink" : "border-transparent text-muted hover:bg-sunken/70 hover:text-ink")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button size="sm" onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); }}>
      {done ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}{done ? "Copied" : label}
    </Button>
  );
}

/* ---------- modal ---------- */
export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input,textarea,select,button:not([data-close])")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [onClose]);
  return (
    <div className="fade fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-side/60 p-4 backdrop-blur-sm sm:p-10" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className={cx("pop w-full rounded-panel border border-line bg-surface shadow-xl", wide ? "max-w-3xl" : "max-w-lg")}>
        <header className="flex items-center justify-between border-b border-line px-5 py-3.5"><h2 className="text-lg font-semibold">{title}</h2><IconButton data-close label="Close" onClick={onClose}><X className="h-4 w-4" aria-hidden /></IconButton></header>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

/* ---------- toasts ---------- */
type Toast = { id: number; text: string; tone: "ok" | "danger" };
/* ---------- confirmation card (window.confirm is silently blocked in embedded browsers such as VS Code) ---------- */
type ConfirmOpts = { title: string; message?: ReactNode; confirmLabel?: string; danger?: boolean };
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => false);
/** `if (await confirm({ title: "Delete?", danger: true })) ...` */
export const useConfirm = () => useContext(ConfirmCtx);
function ConfirmProvider({ children }: { children: ReactNode }) {
  const [req, setReq] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const ask = useCallback((o: ConfirmOpts) => new Promise<boolean>((resolve) => setReq({ ...o, resolve })), []);
  const close = (v: boolean) => { req?.resolve(v); setReq(null); };
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      {req && (
        <Modal title={req.title} onClose={() => close(false)}>
          {req.message && <div className="mb-4 text-sm text-muted">{req.message}</div>}
          <div className="flex justify-end gap-2">
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button variant={req.danger ? "danger" : "primary"} autoFocus onClick={() => close(true)}>{req.confirmLabel ?? "Confirm"}</Button>
          </div>
        </Modal>
      )}
    </ConfirmCtx.Provider>
  );
}

const ToastCtx = createContext<(text: string, tone?: "ok" | "danger") => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "danger" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      <ConfirmProvider>{children}</ConfirmProvider>
      <div aria-live="polite" className="fixed bottom-4 right-4 z-[60] flex max-w-sm flex-col gap-2">
        {items.map((t) => <div key={t.id} role="status" className={cx("slide-in rounded-ctl border border-l-4 bg-surface px-4 py-2.5 text-sm font-medium text-ink shadow-lg", t.tone === "ok" ? "border-line border-l-ok" : "border-line border-l-danger")}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}
