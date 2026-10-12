"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { getMessages } from "@/lib/i18n";

const t = getMessages();
const LINKS = [
  { href: "/operator", label: t.nav.overview },
  { href: "/operator/approvals", label: t.nav.approvals },
  { href: "/operator/escalations", label: t.nav.escalations },
  { href: "/operator/data-requests", label: t.nav.dataRequests },
  { href: "/operator/meetings", label: t.nav.meetings },
  { href: "/operator/billing", label: t.nav.billing },
  { href: "/operator/clients", label: t.nav.clients },
  { href: "/operator/reports", label: t.nav.reports },
  { href: "/operator/compliance", label: t.nav.compliance },
  { href: "/operator/dry-run", label: t.nav.dryRun },
];

/** Client-side guard: the API enforces auth on every call; this only redirects signed-out operators to sign in. */
export function OperatorShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api<{ role: string }>("/api/v1/auth/me")
      .then((me) => (me.role === "operator" ? setReady(true) : router.replace("/client")))
      .catch(() => router.replace("/login"));
  }, [router]);

  async function signOut() {
    await api("/api/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }

  return (
    <>
      <header className="topbar">
        <strong>{t.app.name}</strong>
        <nav aria-label={t.nav.primary}>
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} aria-current={path === l.href ? "page" : undefined}>{l.label}</Link>
          ))}
        </nav>
        <button className="signout" type="button" onClick={signOut}>{t.nav.signOut}</button>
      </header>
      <main id="main" className="container">
        <p className="banner" role="status">{t.operator.dryRunBanner}</p>
        {ready ? children : <p className="muted">{t.operator.loading}</p>}
      </main>
    </>
  );
}
