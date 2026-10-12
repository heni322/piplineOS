"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { day, money } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Terms } from "@/lib/types";

const t = getMessages().client;
type Criterion = { id: string; question: string; evidence_from: string; required: boolean };
type Pricing = { currency: string; setup_fee_minor: number; monthly_retainer_minor: number; per_meeting_minor: number;
  payment_terms_days: number; dispute_window_days: number };

function CriteriaView({ c }: { c: { criteria: Criterion[] } }) {
  return (
    <ol className="sources">
      {c.criteria.map((x) => (
        <li key={x.id}>{x.question} <span className="muted">({x.required ? t.required : t.optional}, {x.evidence_from === "profile" ? t.fromProfile : t.fromReply})</span></li>
      ))}
    </ol>
  );
}

function PricingView({ p }: { p: Pricing }) {
  return (
    <dl className="facts">
      <div><dt>{t.perMeeting}</dt><dd>{money(p.per_meeting_minor, p.currency)}</dd>
        <dt>{t.retainer}</dt><dd>{money(p.monthly_retainer_minor, p.currency)}</dd>
        <dt>{t.setup}</dt><dd>{money(p.setup_fee_minor, p.currency)}</dd></div>
      <div><dt>{t.terms}</dt><dd>{t.days.replace("{n}", String(p.payment_terms_days))}</dd>
        <dt>{t.window}</dt><dd>{t.days.replace("{n}", String(p.dispute_window_days))}</dd></div>
    </dl>
  );
}

export default function ClientTerms() {
  const [terms, setTerms] = useState<Terms[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api<Terms[]>("/api/v1/terms").then(setTerms).catch(() => setError(getMessages().operator.loadError));
  }, []);
  useEffect(load, [load]);

  async function accept(kind: string) {
    setBusy(true); setError(null);
    try { await api(`/api/v1/terms/${kind}/sign`, { method: "POST" }); load(); }
    catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  if (!terms) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.termsTitle}</h1>
      <p className="muted" style={{ maxWidth: "68ch" }}>{t.acceptHint}</p>
      {error && <p role="alert" className="error">{error}</p>}
      {terms.map((x) => (
        <section key={x.kind} className="sheet" style={{ marginBottom: 18 }} aria-labelledby={`h-${x.kind}`}>
          <h2 id={`h-${x.kind}`}>{x.kind === "pricing" ? t.pricing : t.criteria}</h2>
          {x.content && (x.kind === "pricing" ? <PricingView p={x.content as unknown as Pricing} /> : <CriteriaView c={x.content as unknown as { criteria: Criterion[] }} />)}
          <p className={x.signed ? "outcome-allow" : "muted"}>{x.signed && x.signed_at ? t.accepted.replace("{d}", day(x.signed_at)) : t.notAccepted}</p>
          {!x.signed && x.content && <button type="button" disabled={busy} onClick={() => accept(x.kind)}>{t.accept}</button>}
        </section>
      ))}
    </>
  );
}
