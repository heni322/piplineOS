"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppShell, type NavGroup } from "@/components/AppShell";
import { api } from "@/lib/api";
import { getMessages } from "@/lib/i18n";

const t = getMessages();
const GROUPS: NavGroup[] = [{ links: [
  { href: "/client", label: t.nav.clientOverview },
  { href: "/client/meetings", label: t.nav.clientMeetings },
  { href: "/client/targets", label: t.nav.clientTargets },
  { href: "/client/invoices", label: t.nav.clientInvoices },
  { href: "/client/terms", label: t.nav.clientTerms },
  { href: "/client/crm", label: t.nav.clientCrm },
] }];

export type Me = { role: string; tenant_id: string | null };

/** Client portal guard: the API enforces tenant isolation on every call; this only routes people to the right place. */
export function ClientShell({ children }: { children: React.ReactNode }) {
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
    <AppShell area={t.nav.portal} navLabel={t.nav.clientPrimary} groups={GROUPS} onSignOut={signOut}>
      {ready ? children : <p className="muted">{t.operator.loading}</p>}
    </AppShell>
  );
}
