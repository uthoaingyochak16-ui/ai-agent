import { BarChart3, BookOpen, FlaskConical, Inbox, ListChecks, LayoutDashboard, LogOut, Menu, MessagesSquare, Moon, Plug, SlidersHorizontal, Sun, Users, X } from "lucide-react";
import { Suspense, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";
import { cx, IconButton, Spinner } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/inbox", label: "Inbox", icon: Inbox },
  { to: "/profiles", label: "Profiles", icon: Users },
  { to: "/leads", label: "Leads", icon: ListChecks },
  { to: "/knowledge", label: "Knowledge base", icon: BookOpen },
  { to: "/qa", label: "Q&A manager", icon: MessagesSquare },
  { to: "/test-chat", label: "Test chat", icon: FlaskConical },
  { to: "/channels", label: "Channels", icon: Plug, admin: true },
  { to: "/ai", label: "AI settings", icon: SlidersHorizontal, admin: true },
  { to: "/evaluation", label: "Evaluation", icon: BarChart3 },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const { dark, toggle } = useTheme();
  const [open, setOpen] = useState(false);
  const items = NAV.filter((n) => !n.admin || user?.role === "admin");
  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-0.5 p-3">
      {items.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)}
          className={({ isActive }) => cx("flex items-center gap-3 rounded-ctl px-3 py-2 text-sm font-semibold", isActive ? "bg-accent-soft text-accent-strong" : "text-muted hover:bg-sunken hover:text-ink")}>
          <Icon className="h-4 w-4 shrink-0" aria-hidden />{label}
        </NavLink>
      ))}
    </nav>
  );
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-ctl focus:bg-accent focus:px-3 focus:py-2 focus:text-on-accent">Skip to content</a>
      <aside className="hidden border-r border-line bg-surface lg:block">
        <div className="sticky top-0 flex h-screen flex-col">
          <div className="px-5 py-4"><p className="text-lg font-bold leading-tight">Assistant console</p><p className="text-xs text-muted">Messenger, Instagram, WhatsApp</p></div>
          <div className="flex-1 overflow-y-auto">{nav}</div>
          <div className="border-t border-line p-3 text-sm">
            <p className="truncate font-semibold">{user?.name || user?.email}</p><p className="truncate text-xs text-muted">{user?.email} ({user?.role})</p>
            <div className="mt-2 flex gap-1">
              <IconButton label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>{dark ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}</IconButton>
              <IconButton label="Sign out" onClick={logout}><LogOut className="h-4 w-4" aria-hidden /></IconButton>
            </div>
          </div>
        </div>
      </aside>
      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface px-3 py-2 lg:hidden">
          <div className="flex items-center gap-1"><IconButton label={open ? "Close menu" : "Open menu"} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <X className="h-5 w-5" aria-hidden /> : <Menu className="h-5 w-5" aria-hidden />}</IconButton><span className="font-bold">Assistant console</span></div>
          <div className="flex"><IconButton label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>{dark ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}</IconButton><IconButton label="Sign out" onClick={logout}><LogOut className="h-4 w-4" aria-hidden /></IconButton></div>
        </header>
        {open && <div className="border-b border-line bg-surface lg:hidden">{nav}</div>}
        <main id="main" className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6"><Suspense fallback={<Spinner />}><Outlet /></Suspense></main>
      </div>
    </div>
  );
}
