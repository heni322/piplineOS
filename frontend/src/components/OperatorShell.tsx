"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppShell, type NavGroup } from "@/components/AppShell";
import { api } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { GovernanceStatus, SendingProblems } from "@/lib/types";

const t = getMessages();
const GROUPS: NavGroup[] = [
  { links: [{ href: "/operator", label: t.nav.overview }] },
  { label: t.nav.groupWork, links: [
    { href: "/operator/approvals", label: t.nav.approvals },
    { href: "/operator/leads", label: t.nav.leads },
    { href: "/operator/tasks", label: t.nav.tasks },
    { href: "/operator/escalations", label: t.nav.escalations },
    { href: "/operator/data-requests", label: t.nav.dataRequests },
    { href: "/operator/meetings", label: t.nav.meetings },
  ] },
  { label: t.nav.groupMoney, links: [
    { href: "/operator/pilot-requests", label: t.nav.pilotRequests },
    { href: "/operator/clients", label: t.nav.clients },
    { href: "/operator/discover", label: t.nav.discover },
    { href: "/operator/campaigns", label: t.nav.campaigns },
    { href: "/operator/billing", label: t.nav.billing },
    { href: "/operator/reports", label: t.nav.reports },
    { href: "/operator/proof", label: t.nav.proof },
  ] },
  { label: t.nav.groupSystem, links: [
    { href: "/operator/inboxes", label: t.nav.inboxes },
    { href: "/operator/compliance", label: t.nav.compliance },
    { href: "/operator/dry-run", label: t.nav.dryRun },
    { href: "/operator/governance", label: t.nav.governance },
    { href: "/operator/checks", label: t.nav.checks },
  ] },
];

/** Live only when all three keys allow a real send: the global switch, the live email adapter and a live client. */
export function isSendingLive(s: GovernanceStatus | null): boolean {
  return !!s && s.global_go_live && s.adapter_modes.email === "live" && s.live_clients > 0;
}

/** Client-side guard: the API enforces auth on every call; this only redirects signed-out operators to sign in. */
export function OperatorShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  // false until confirmed: the dry-run banner stays up unless the system is really live (safe default)
  const [live, setLive] = useState(false);
  // emails that failed recently: shown as a red alert on every page until they are dealt with
  const [sendErrors, setSendErrors] = useState(0);

  useEffect(() => {
    api<{ role: string }>("/api/v1/auth/me")
      .then((me) => {
        if (me.role !== "operator") return router.replace("/client");
        setReady(true);
        api<GovernanceStatus>("/api/v1/governance/status")
          .then((s) => setLive(isSendingLive(s)))
          .catch(() => setLive(false));
        api<SendingProblems>("/api/v1/sending-problems?days=7")
          .then((p) => setSendErrors(p.errors_last_7_days))
          .catch(() => setSendErrors(0));
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  async function signOut() {
    await api("/api/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }

  return (
    <AppShell area={t.nav.console} navLabel={t.nav.primary} groups={GROUPS} onSignOut={signOut}
      banner={<>
        {sendErrors > 0 && (
          <p className="alert" role="alert">
            {t.operator.sendErrors.replace("{n}", String(sendErrors))}{" "}
            <Link href="/operator/checks">{t.operator.sendErrorsLink}</Link>
          </p>
        )}
        {!live && <p className="banner" role="status">{t.operator.dryRunBanner}</p>}
      </>}>
      {ready ? children : <p className="muted">{t.operator.loading}</p>}
    </AppShell>
  );
}
