"use client";

import { useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";

const t = getMessages().clients;
type Crit = { question: string; required: boolean; evidence_from: "reply" | "profile" };
type Price = { setup_fee_minor: number; monthly_retainer_minor: number; per_meeting_minor: number; payment_terms_days: number; dispute_window_days: number };
type Setup = { configured: boolean; templates: Record<string, string>; niche: string | null; sender_name: string; sender_title: string;
  value_proposition: string; outreach_purpose: string; criteria: Crit[]; pricing: Price; currency: string; subject_variants: number };

const major = (minor: number) => (minor / 100).toString();
const minor = (s: string) => Math.round((parseFloat(s) || 0) * 100);

/** Offer, qualification criteria and pricing for one client, without editing JSON. */
export function OfferPanel({ id, name, onClose, onSaved }: { id: string; name: string; onClose: () => void; onSaved: (m: string) => void }) {
  const [s, setS] = useState<Setup | null>(null);
  const [money, setMoney] = useState({ setup: "0", retainer: "0", meeting: "0" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Setup>(`/api/v1/clients/${id}/setup`).then((d) => {
      setS(d);
      setMoney({ setup: major(d.pricing.setup_fee_minor), retainer: major(d.pricing.monthly_retainer_minor), meeting: major(d.pricing.per_meeting_minor) });
    }).catch(() => setError(getMessages().operator.loadError));
  }, [id]);

  const setCrit = (i: number, patch: Partial<Crit>) => s && setS({ ...s, criteria: s.criteria.map((c, k) => (k === i ? { ...c, ...patch } : c)) });

  async function save() {
    if (!s) return;
    setBusy(true); setError(null);
    try {
      const r = await api<{ version: number }>(`/api/v1/clients/${id}/setup`, { method: "PUT", body: JSON.stringify({
        niche: s.configured ? null : s.niche, sender_name: s.sender_name, sender_title: s.sender_title,
        value_proposition: s.value_proposition, outreach_purpose: s.outreach_purpose,
        criteria: s.criteria.filter((c) => c.question.trim()), subject_variants: s.subject_variants ?? 1,
        pricing: { ...s.pricing, setup_fee_minor: minor(money.setup), monthly_retainer_minor: minor(money.retainer), per_meeting_minor: minor(money.meeting) },
      }) });
      onSaved(t.offerSaved.replace("{v}", String(r.version)));
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  const field = (key: "sender_name" | "sender_title" | "value_proposition" | "outreach_purpose", label: string) => s && (
    <>
      <label htmlFor={`o-${key}`}>{label}</label>
      <input id={`o-${key}`} value={s[key]} onChange={(e) => setS({ ...s, [key]: e.target.value })} />
    </>
  );
  const ready = s && s.sender_name.trim().length > 1 && s.sender_title.trim().length > 1 && s.value_proposition.trim().length >= 10
    && s.outreach_purpose.trim().length >= 5 && s.criteria.some((c) => c.question.trim().length >= 5);

  return (
    <div className="panel targeting" role="dialog" aria-labelledby="offer-title">
      <h2 id="offer-title">{t.offerTitle.replace("{c}", name)}</h2>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.offerIntro}</p>
      {error && <p role="alert" className="error">{error}</p>}
      {!s ? (!error && <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>) : (
        <>
          {!s.configured && (
            <>
              <label htmlFor="o-niche">{t.template}</label>
              <select id="o-niche" value={s.niche ?? ""} onChange={(e) => setS({ ...s, niche: e.target.value })}>
                {Object.entries(s.templates).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </>
          )}
          <div className="targeting-grid">
            <div>{field("sender_name", t.senderName)}</div>
            <div>{field("sender_title", t.senderTitle)}</div>
          </div>
          {field("value_proposition", t.valueProp)}
          {field("outreach_purpose", t.purpose)}
          <label htmlFor="o-ab">{t.abLabel}</label>
          <select id="o-ab" value={s.subject_variants ?? 1} onChange={(e) => setS({ ...s, subject_variants: Number(e.target.value) })}>
            <option value={1}>{t.abOff}</option>
            <option value={2}>{t.abOn.replace("{n}", "2")}</option>
            <option value={3}>{t.abOn.replace("{n}", "3")}</option>
          </select>
          <fieldset>
            <legend>{t.criteriaTitle}</legend>
            {s.criteria.map((c, i) => (
              <div key={i} className="criterion">
                <label htmlFor={`o-c-${i}`}>{t.criterion.replace("{n}", String(i + 1))}</label>
                <input id={`o-c-${i}`} value={c.question} onChange={(e) => setCrit(i, { question: e.target.value })} />
                <div className="row-actions">
                  <label className="inline"><input type="checkbox" checked={c.required} onChange={(e) => setCrit(i, { required: e.target.checked })} /> {t.requiredLabel}</label>
                  <label className="inline"><input type="checkbox" checked={c.evidence_from === "profile"}
                    onChange={(e) => setCrit(i, { evidence_from: e.target.checked ? "profile" : "reply" })} /> {t.fromProfile}</label>
                  {s.criteria.length > 1 && <button type="button" className="secondary" onClick={() => setS({ ...s, criteria: s.criteria.filter((_, k) => k !== i) })}>{t.remove}</button>}
                </div>
              </div>
            ))}
            {s.criteria.length < 8 && <button type="button" className="secondary"
              onClick={() => setS({ ...s, criteria: [...s.criteria, { question: "", required: true, evidence_from: "reply" }] })}>{t.addCriterion}</button>}
          </fieldset>
          <fieldset>
            <legend>{t.pricingTitle.replace("{cur}", s.currency)}</legend>
            <div className="targeting-grid">
              <div><label htmlFor="o-setup">{t.setupFee}</label><input id="o-setup" inputMode="decimal" value={money.setup} onChange={(e) => setMoney({ ...money, setup: e.target.value })} /></div>
              <div><label htmlFor="o-ret">{t.retainer}</label><input id="o-ret" inputMode="decimal" value={money.retainer} onChange={(e) => setMoney({ ...money, retainer: e.target.value })} /></div>
              <div><label htmlFor="o-meet">{t.perMeeting}</label><input id="o-meet" inputMode="decimal" value={money.meeting} onChange={(e) => setMoney({ ...money, meeting: e.target.value })} /></div>
              <div><label htmlFor="o-terms">{t.paymentTerms}</label><input id="o-terms" inputMode="numeric" value={s.pricing.payment_terms_days}
                onChange={(e) => setS({ ...s, pricing: { ...s.pricing, payment_terms_days: parseInt(e.target.value, 10) || 0 } })} /></div>
              <div><label htmlFor="o-disp">{t.disputeWindow}</label><input id="o-disp" inputMode="numeric" value={s.pricing.dispute_window_days}
                onChange={(e) => setS({ ...s, pricing: { ...s.pricing, dispute_window_days: parseInt(e.target.value, 10) || 0 } })} /></div>
            </div>
          </fieldset>
          <div className="row-actions" style={{ marginTop: 14 }}>
            <button type="button" disabled={busy || !ready} onClick={save}>{t.save}</button>
            <button type="button" className="secondary" onClick={onClose}>{t.cancel}</button>
          </div>
        </>
      )}
    </div>
  );
}
