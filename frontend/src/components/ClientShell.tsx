"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { getMessages } from "@/lib/i18n";

const t = getMessages();
const LINKS = [
  { href: "/client", label: t.nav.clientOverview },
  { href: "/client/meetings", label: t.nav.clientMeetings },
  { href: "/client/invoices", label: t.nav.clientInvoices },
  { href: "/client/terms", label: t.nav.clientTerms },
  { href: "/client/crm", label: t.nav.clientCrm },
];

export type Me = { role: string; tenant_id: string | null };

/** Client portal guard: the API enforces tenant isolation on every call; this only routes people to the right place. */
export function ClientShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api<Me>("/api/v1/auth/me")
      .then((me) => (me.role === "operator" ? router.replace("/operator") : setReady(true)))
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
        <nav aria-label={t.nav.clientPrimary}>
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} aria-current={path === l.href ? "page" : undefined}>{l.label}</Link>
          ))}
        </nav>
        <button className="signout" type="button" onClick={signOut}>{t.nav.signOut}</button>
      </header>
      <main id="main" className="container">
        {ready ? children : <p className="muted">{t.operator.loading}</p>}
      </main>
    </>
  );
}
