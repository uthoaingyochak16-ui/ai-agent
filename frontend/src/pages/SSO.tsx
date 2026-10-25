import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ErrorNote, Spinner } from "../components/ui";
import { useAuth } from "../lib/auth";

/** Sign-in from the main application: /sso#token=<one-time token>&next=/inbox (docs/INTEGRATION.md).
 *  The token is in the #fragment so it never reaches a server log; it is removed from the address bar at once. */
export default function SSO() {
  const { loginWithSSO } = useAuth();
  const navigate = useNavigate();
  const [err, setErr] = useState<unknown>(null);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;  // a token works once: never send it twice (React dev mode runs effects twice)
    started.current = true;
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("token") ?? "";
    const next = params.get("next") ?? "/";
    window.history.replaceState(null, "", window.location.pathname);
    const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";  // only pages of this dashboard
    if (!token) { setErr("No sign-in token was given."); return; }
    loginWithSSO(token).then(() => navigate(safeNext, { replace: true })).catch(setErr);
  }, [loginWithSSO, navigate]);
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="pop w-full max-w-sm space-y-3 rounded-panel border border-line bg-surface p-7 text-sm shadow-lg">
        {err ? <><ErrorNote error={err} /><p>Open the assistant again from the main application, or <Link className="underline" to="/login">sign in with a password</Link>.</p></>
             : <Spinner label="Signing you in…" />}
      </div>
    </main>
  );
}
