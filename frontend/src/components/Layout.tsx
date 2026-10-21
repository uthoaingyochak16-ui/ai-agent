import { BookOpen, GraduationCap, Megaphone, MessageCircle, Inbox, ListChecks, LayoutDashboard, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Plug, SlidersHorizontal, Sun, Users, X } from "lucide-react";
import { Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";
import { cx, IconButton, Spinner } from "./ui";

// grouped the way people think about the work: talk to guests, teach the assistant, set it up
const NAV: { group: string; items: { to: string; label: string; icon: typeof Inbox; end?: boolean; admin?: boolean; hint: string }[] }[] = [
  { group: "Guests", items: [
    { to: "/", label: "Overview", icon: LayoutDashboard, end: true, hint: "How the assistant is doing today" },
    { to: "/inbox", label: "Inbox", icon: Inbox, hint: "Messenger, Instagram and WhatsApp messages of every Page and number" },
    { to: "/comments", label: "Post comments", icon: MessageCircle, hint: "Comments on your Facebook and Instagram posts" },
    { to: "/posts", label: "Social media", icon: Megaphone, hint: "Calendar, posts, insights, audience, ads and WhatsApp broadcasts" },
    { to: "/leads", label: "Leads", icon: ListChecks, hint: "Guests interested in a program" },
    { to: "/profiles", label: "Guest profiles", icon: Users, hint: "Everything known about each guest" },
  ] },
  { group: "Assistant", items: [
    { to: "/train", label: "Train the assistant", icon: GraduationCap, hint: "Test chat, teach, find mistakes, test set" },
    { to: "/knowledge", label: "Knowledge", icon: BookOpen, hint: "Documents, Q&A, values, programs and data sheets" },
  ] },
  { group: "Settings", items: [
    { to: "/channels", label: "Pages and numbers", icon: Plug, admin: true, hint: "Connect Facebook Pages, Instagram, WhatsApp" },
    { to: "/ai", label: "AI settings", icon: SlidersHorizontal, admin: true, hint: "Models, messages, behaviour" },
  ] },
];
const MIN_W = 180, MAX_W = 380, ICONS_W = 64;
const load = (k: string, d: string) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const keep = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private window: not remembered */ } };

export default function Layout() {
  const { user, logout } = useAuth();
  const { dark, toggle } = useTheme();
  const [open, setOpen] = useState(false);
  // desktop side bar: collapsed to icons, or as wide as the person dragged it (remembered in this browser)
  const [collapsed, setCollapsed] = useState(() => load("nav:collapsed", "0") === "1");
  const [width, setWidth] = useState(() => Math.min(MAX_W, Math.max(MIN_W, Number(load("nav:width", "232")) || 232)));
  const drag = useRef<{ x: number; w: number } | null>(null);
  useEffect(() => keep("nav:collapsed", collapsed ? "1" : "0"), [collapsed]);
  useEffect(() => keep("nav:width", String(width)), [width]);
  useEffect(() => {
    const move = (e: PointerEvent) => { if (drag.current) setWidth(Math.min(MAX_W, Math.max(MIN_W, drag.current.w + e.clientX - drag.current.x))); };
    const up = () => { drag.current = null; document.body.style.cursor = ""; document.body.style.userSelect = ""; };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  }, []);
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((n) => !n.admin || user?.role === "admin") })).filter((g) => g.items.length);
  const nav = (icons: boolean) => (
    <nav aria-label="Main" className={cx("flex flex-col p-3", icons ? "items-center gap-3 px-2" : "gap-4")}>
      {groups.map((g) => (
        <div key={g.group} className={cx(icons && "flex w-full flex-col items-center border-t border-line pt-3 first:border-0 first:pt-0")}>
          {!icons && <p className="px-3 pb-1 text-[11px] font-bold uppercase tracking-wide text-muted">{g.group}</p>}
          <div className="flex w-full flex-col gap-0.5">{g.items.map(({ to, label, icon: Icon, end, hint }) => (
            <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)} title={icons ? `${label}: ${hint}` : hint} aria-label={icons ? label : undefined}
              className={({ isActive }) => cx("flex items-center rounded-ctl text-sm font-semibold", icons ? "h-10 w-full justify-center" : "gap-3 px-3 py-2",
                isActive ? "bg-accent-soft text-accent-strong" : "text-muted hover:bg-sunken hover:text-ink")}>
              <Icon className={cx("shrink-0", icons ? "h-5 w-5" : "h-4 w-4")} aria-hidden />{!icons && <span className="truncate">{label}</span>}
            </NavLink>
          ))}</div>
        </div>
      ))}
    </nav>
  );
  const asideW = collapsed ? ICONS_W : width;
  return (
    <div className="min-h-screen lg:grid" style={{ gridTemplateColumns: `${asideW}px minmax(0,1fr)` }}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-ctl focus:bg-accent focus:px-3 focus:py-2 focus:text-on-accent">Skip to content</a>
      <aside className="relative hidden border-r border-line bg-surface lg:block">
        <div className="sticky top-0 flex h-screen flex-col">
          <div className={cx("flex items-center gap-2 py-3", collapsed ? "justify-center px-2" : "justify-between px-4")}>
            {!collapsed && <div className="min-w-0"><p className="truncate text-lg font-bold leading-tight">Assistant console</p><p className="truncate text-xs text-muted">Facebook, Instagram and WhatsApp in one place</p></div>}
            <IconButton label={collapsed ? "Show the menu with names" : "Show only icons"} onClick={() => setCollapsed(!collapsed)}>
              {collapsed ? <PanelLeftOpen className="h-4 w-4" aria-hidden /> : <PanelLeftClose className="h-4 w-4" aria-hidden />}</IconButton>
          </div>
          <div className="flex-1 overflow-y-auto">{nav(collapsed)}</div>
          <div className={cx("border-t border-line p-3 text-sm", collapsed && "px-2")}>
            {!collapsed && <><p className="truncate font-semibold">{user?.name || user?.email}</p><p className="truncate text-xs text-muted">{user?.email} ({user?.role})</p></>}
            <div className={cx("mt-2 flex gap-1", collapsed && "mt-0 flex-col items-center")}>
              <IconButton label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>{dark ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}</IconButton>
              <IconButton label="Sign out" onClick={logout}><LogOut className="h-4 w-4" aria-hidden /></IconButton>
            </div>
          </div>
        </div>
        {!collapsed && <div role="separator" aria-orientation="vertical" aria-label="Drag to change the menu width (arrow keys too)" tabIndex={0}
          aria-valuemin={MIN_W} aria-valuemax={MAX_W} aria-valuenow={width}
          onPointerDown={(e) => { drag.current = { x: e.clientX, w: width }; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; }}
          onDoubleClick={() => setWidth(232)}
          onKeyDown={(e) => { if (e.key === "ArrowLeft") setWidth((w) => Math.max(MIN_W, w - 16)); if (e.key === "ArrowRight") setWidth((w) => Math.min(MAX_W, w + 16)); }}
          className="absolute -right-1 top-0 z-20 h-full w-2 cursor-col-resize hover:bg-accent/30 focus:bg-accent/40 focus:outline-none" title="Drag to widen or narrow the menu; double-click for the usual width" />}
      </aside>
      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface px-3 py-2 lg:hidden">
          <div className="flex items-center gap-1"><IconButton label={open ? "Close menu" : "Open menu"} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <X className="h-5 w-5" aria-hidden /> : <Menu className="h-5 w-5" aria-hidden />}</IconButton><span className="font-bold">Assistant console</span></div>
          <div className="flex"><IconButton label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>{dark ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}</IconButton><IconButton label="Sign out" onClick={logout}><LogOut className="h-4 w-4" aria-hidden /></IconButton></div>
        </header>
        {open && <div className="border-b border-line bg-surface lg:hidden">{nav(false)}</div>}
        <main id="main" className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6"><Suspense fallback={<Spinner />}><Outlet /></Suspense></main>
      </div>
    </div>
  );
}
