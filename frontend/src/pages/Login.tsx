import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { Button, ErrorNote, Field, Input } from "../components/ui";
import { useAuth } from "../lib/auth";

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
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-panel border border-line bg-surface p-6">
        <div><h1 className="text-xl font-bold">Sign in</h1><p className="text-sm text-muted">Assistant console</p></div>
        <Field label="Email">{(id) => <Input id={id} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
        <Field label="Password">{(id) => <Input id={id} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}</Field>
        <ErrorNote error={err} />
        <Button type="submit" variant="primary" loading={busy} className="w-full">Sign in</Button>
      </form>
    </main>
  );
}
