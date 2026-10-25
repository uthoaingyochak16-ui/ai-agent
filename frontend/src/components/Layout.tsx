import { ArrowLeft, LogOut, Moon, Sun } from "lucide-react";
import { Suspense } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { EMBEDDED } from "../lib/embed";
import { useTheme } from "../lib/theme";
import { LauncherGrid, QuickMenu } from "./Launcher";
import { Avatar, Spinner } from "./ui";

export function Mark({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden className="shrink-0">
      <rect width="40" height="40" rx="11" fill="rgb(var(--accent))" />
      <path d="M12 26c0-8 4-13 8-13s8 5 8 13" fill="none" stroke="rgb(var(--on-accent))" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M8.5 27.5h23" stroke="rgb(var(--brass))" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="20" cy="21" r="2.2" fill="rgb(var(--on-accent))" />
    </svg>
  );
}

const roundBtn = "inline-flex h-9 w-9 items-center justify-center rounded-ctl border border-line bg-surface text-muted transition-colors hover:bg-sunken hover:text-ink";

/** There is no navigation bar in this application: the home page shows every page as a button, and every other page
 *  has a small "Assistant home" link plus a round Menu button in the corner. So it can sit inside the main
 *  application (which keeps the only bar) without a second, nested one. */
export default function Layout() {
  const { user, logout } = useAuth();
  const { dark, toggle } = useTheme();
  const home = useLocation().pathname === "/";
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-ctl focus:bg-accent focus:px-3 focus:py-2 focus:text-on-accent">Skip to content</a>
      <main id="main" className="mx-auto w-full max-w-[1400px] px-4 pb-24 pt-6 sm:px-8">
        {home ? (
          <header className="rise mb-6 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3"><Mark size={40} /><div><p className="font-display text-xl font-semibold leading-tight">Assistant console</p><p className="text-sm text-muted">One desk for every guest</p></div></div>
            {!EMBEDDED && (
              <div className="flex items-center gap-2">
                <div className="mr-1 hidden items-center gap-2.5 sm:flex"><Avatar name={user?.name || user?.email} size={34} /><div className="min-w-0 leading-tight"><p className="truncate text-sm font-semibold">{user?.name || user?.email}</p><p className="text-xs text-muted">{user?.role === "admin" ? "Administrator" : "Agent"}</p></div></div>
                <button type="button" className={roundBtn} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"} title={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>{dark ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}</button>
                <button type="button" className={roundBtn} aria-label="Sign out" title="Sign out" onClick={logout}><LogOut className="h-4 w-4" aria-hidden /></button>
              </div>
            )}
          </header>
        ) : (
          <Link to="/" className="mb-4 inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-sm font-semibold text-muted transition-colors hover:text-ink"><ArrowLeft className="h-4 w-4" aria-hidden />Assistant home</Link>
        )}
        {home && <LauncherGrid />}
        <Suspense fallback={<Spinner />}><Outlet /></Suspense>
      </main>
      {!home && <QuickMenu />}
    </div>
  );
}
