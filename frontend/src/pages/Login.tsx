import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { Mark } from "../components/Layout";
import { Button, ErrorNote, Field, Input } from "../components/ui";
import { useAuth } from "../lib/auth";

/** Concentric brass arcs: the doorway mark of the brand, drawn large and quiet behind the welcome text. */
function Arches() {
  return (
    <svg aria-hidden viewBox="0 0 600 600" className="pointer-events-none absolute -bottom-24 -right-24 h-[640px] w-[640px] opacity-60">
      {[270, 222, 174, 126, 78].map((r, i) => (
        <path key={r} d={`M${300 - r} 600V${300} a${r} ${r} 0 0 1 ${r * 2} 0V600`} fill="none" stroke="rgb(var(--brass))" strokeOpacity={0.12 + i * 0.07} strokeWidth="1.5" />
      ))}
    </svg>
  );
}

export default function Login() {
  const { user, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to="/" replace />;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await login(email, password); } catch (x) { setErr(x); } finally { setBusy(false); }
  };
  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,1.1fr)_minmax(420px,1fr)]">
      <section className="relative hidden overflow-hidden bg-side p-14 text-side-ink lg:flex lg:flex-col lg:justify-between">
        <div className="absolute inset-0 bg-[radial-gradient(700px_420px_at_15%_0%,rgb(var(--accent)/0.28),transparent_70%)]" aria-hidden />
        <Arches />
        <div className="relative flex items-center gap-3"><Mark size={40} /><span className="font-display text-xl font-semibold text-white">Assistant console</span></div>
        <div className="rise relative max-w-lg">
          <h2 className="font-display text-[52px] font-semibold leading-[1.05] text-white">Every guest message answered, and someone watching over it.</h2>
          <p className="mt-6 max-w-md text-base leading-relaxed text-side-muted">Facebook, Instagram and WhatsApp land in one inbox. The assistant replies in Bangla, Banglish and English, and hands the conversation to you when it should.</p>
        </div>
        <p className="relative text-sm text-side-muted">Your team's desk for guest conversations.</p>
      </section>
      <section className="flex items-center justify-center p-6 sm:p-10">
        <form onSubmit={submit} className="rise-2 w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden"><Mark size={36} /><span className="font-display text-lg font-semibold">Assistant console</span></div>
          <h1 className="text-[32px] font-semibold leading-tight">Welcome back</h1>
          <p className="mb-7 mt-1.5 text-sm text-muted">Sign in to see what your guests are asking.</p>
          <div className="space-y-4">
            <Field label="Email">{(id) => <Input id={id} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} className="h-11" />}</Field>
            <Field label="Password">{(id) => <Input id={id} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} className="h-11" />}</Field>
            <ErrorNote error={err} />
            <Button type="submit" variant="primary" loading={busy} className="h-11 w-full text-[15px]">Sign in</Button>
          </div>
        </form>
      </section>
    </main>
  );
}
