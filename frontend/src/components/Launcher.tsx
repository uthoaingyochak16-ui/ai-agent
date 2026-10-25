import { LayoutGrid, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { NAV, type NavItem } from "../nav";
import { cx } from "./ui";

/** The pages the signed-in person may open, in groups (the two settings pages are for administrators only). */
function useGroups() {
  const { user } = useAuth();
  return NAV.map((g) => ({ ...g, items: g.items.filter((n) => !n.admin || user?.role === "admin") })).filter((g) => g.items.length);
}

function Tile({ item, current, compact }: { item: NavItem; current?: boolean; compact?: boolean }) {
  const Icon = item.icon;
  return (
    <Link to={item.to} aria-current={current ? "page" : undefined} title={item.hint}
      className={cx("group flex items-start gap-3 rounded-panel border bg-surface text-left transition hover:-translate-y-px hover:border-brass/70 hover:shadow-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
        compact ? "items-center p-2.5" : "p-4", current ? "border-accent bg-accent-soft" : "border-line")}>
      <span className={cx("shrink-0 rounded-ctl bg-accent-soft text-accent-strong transition-colors group-hover:bg-accent group-hover:text-on-accent", compact ? "p-1.5" : "p-2.5", current && "bg-accent text-on-accent")}>
        <Icon className={compact ? "h-4 w-4" : "h-5 w-5"} aria-hidden />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold leading-tight text-ink">{item.label}</span>
        {!compact && <span className="mt-1 line-clamp-2 block text-xs text-muted">{item.hint}</span>}
      </span>
    </Link>
  );
}

/** Home screen menu: every page as a big button, grouped by what the person is doing. Replaces the old side bar. */
export function LauncherGrid() {
  // the home page is the Overview, so it needs no button of its own
  const groups = useGroups().map((g) => ({ ...g, items: g.items.filter((it) => !it.end) })).filter((g) => g.items.length);
  return (
    <nav aria-label="Assistant pages" className="mb-8 space-y-5">
      {groups.map((g) => (
        <section key={g.group} aria-label={g.group}>
          <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wide text-muted">{g.group}</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{g.items.map((it) => <Tile key={it.to} item={it} />)}</div>
        </section>
      ))}
    </nav>
  );
}

/** A round "Menu" button in the corner of every other page. It opens the same buttons in a small panel, so there is
 *  no bar across the screen and nothing to nest inside the main application's own navigation. */
export function QuickMenu() {
  const [open, setOpen] = useState(false);
  const groups = useGroups();
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open]);
  return (
    <>
      {open && <div className="fade fixed inset-0 z-40 bg-black/25" aria-hidden onClick={() => setOpen(false)} />}
      {open && (
        <div role="dialog" aria-label="Assistant pages" className="pop fixed bottom-20 right-4 z-50 max-h-[78vh] w-[min(94vw,520px)] overflow-y-auto rounded-panel border border-line bg-surface p-4 shadow-lg">
          <div className="space-y-4">
            {groups.map((g) => (
              <section key={g.group} aria-label={g.group}>
                <h2 className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-muted">{g.group}</h2>
                <div className="grid gap-2 sm:grid-cols-2">
                  {g.items.map((it) => <Tile key={it.to} item={it} compact current={it.end ? pathname === it.to : pathname === it.to || pathname.startsWith(`${it.to}/`)} />)}
                </div>
              </section>
            ))}
          </div>
        </div>
      )}
      <button type="button" aria-expanded={open} aria-label={open ? "Close the menu" : "Open the menu"} onClick={() => setOpen(!open)}
        className="fixed bottom-4 right-4 z-50 inline-flex h-12 items-center gap-2 rounded-full bg-side px-4 text-sm font-semibold text-side-ink shadow-lg transition hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brass">
        {open ? <X className="h-5 w-5" aria-hidden /> : <LayoutGrid className="h-5 w-5 text-brass" aria-hidden />}<span className="hidden sm:inline">{open ? "Close" : "Menu"}</span>
      </button>
    </>
  );
}
