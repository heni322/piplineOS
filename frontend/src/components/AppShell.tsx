"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { getMessages } from "@/lib/i18n";

const t = getMessages();

export type NavGroup = { label?: string; links: { href: string; label: string }[] };

/** The signed-in frame shared by the operator console and the client portal: a sidebar of grouped links (a
 * scrolling bar on phones), and the page. Auth is enforced by the API; the shells only route people. */
export function AppShell({ area, navLabel, groups, onSignOut, banner, children }: {
  area: string; navLabel: string; groups: NavGroup[]; onSignOut: () => void; banner?: React.ReactNode; children: React.ReactNode;
}) {
  const path = usePathname();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">P</span>
          <span className="brand-text"><strong>{t.app.name}</strong><span>{area}</span></span>
        </div>
        <nav aria-label={navLabel}>
          {groups.map((g, i) => (
            <div className="nav-group" key={g.label ?? i}>
              {g.label && <p className="nav-label">{g.label}</p>}
              {g.links.map((l) => (
                <Link key={l.href} href={l.href} aria-current={path === l.href ? "page" : undefined}>{l.label}</Link>
              ))}
            </div>
          ))}
        </nav>
        <button className="signout" type="button" onClick={onSignOut}>{t.nav.signOut}</button>
      </aside>
      <div className="shell-main">
        <main id="main" className="container">
          {banner}
          {children}
        </main>
      </div>
    </div>
  );
}
