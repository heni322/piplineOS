"use client";

import { useState } from "react";
import { api, type Problem } from "@/lib/api";
import { day, money } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Invoice } from "@/lib/types";

const t = getMessages().billing;
const methods = t.methods as Record<string, string>;

/** What was received against an issued invoice, and recording a new payment (needs the authenticator code). */
export function PaymentsPanel({ inv, onChange }: { inv: Invoice; onChange: (inv: Invoice, message: string) => void }) {
  const [form, setForm] = useState({ amount: "", on: new Date().toISOString().slice(0, 10), method: "bank_transfer", ref: "", totp: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const minor = Math.round((parseFloat(form.amount) || 0) * 100) * (form.method === "reversal" ? -1 : 1);
  async function save() {
    setBusy(true); setError(null);
    try {
      const r = await api<Invoice>(`/api/v1/invoices/${inv.id}/payments`, { method: "POST", body: JSON.stringify({
        amount_minor: minor, received_on: form.on, method: form.method, reference: form.ref || null, totp_code: form.totp.trim() }) });
      setOpen(false); setForm({ ...form, amount: "", ref: "", totp: "" });
      onChange(r, r.status === "paid" ? t.paidInFull.replace("{n}", r.number) : t.recorded);
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  return (
    <div className="panel" aria-labelledby="pay-title">
      <h3 id="pay-title" style={{ marginTop: 0 }}>{t.paymentsTitle}</h3>
      <dl className="ledger">
        <div><dt>{t.paid}</dt><dd>{money(inv.paid_minor, inv.currency)}</dd></div>
        <div><dt>{t.due}</dt><dd className={inv.overdue ? "outcome-block" : undefined}>{money(Math.max(inv.due_minor, 0), inv.currency)}
          {inv.overdue && <small> · {t.overdue}</small>}</dd></div>
      </dl>
      {inv.payments.length === 0 ? <p className="muted">{t.noPayments}</p> : (
        <ul className="states">{inv.payments.map((p, i) => (
          <li key={i}><span>{day(p.received_on)} · {methods[p.method] ?? p.method}{p.reference ? ` · ${p.reference}` : ""}</span>
            <span>{money(p.amount_minor, inv.currency)}</span></li>))}</ul>
      )}
      {inv.status === "issued" && (!open ? <button type="button" className="secondary" onClick={() => setOpen(true)}>{t.recordPayment}</button> : (
        <div role="dialog" aria-labelledby="rec-title">
          <h3 id="rec-title">{t.recordPayment}</h3>
          <p className="hint">{t.paymentHint}</p>
          {error && <p role="alert" className="error">{error}</p>}
          <label htmlFor="p-amount">{t.amount}</label>
          <input id="p-amount" inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} style={{ maxWidth: "12rem" }} />
          <label htmlFor="p-on">{t.receivedOn}</label>
          <input id="p-on" type="date" value={form.on} onChange={(e) => setForm({ ...form, on: e.target.value })} style={{ maxWidth: "12rem" }} />
          <label htmlFor="p-method">{t.method}</label>
          <select id="p-method" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
            {Object.entries(methods).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <label htmlFor="p-ref">{t.reference}</label>
          <input id="p-ref" value={form.ref} maxLength={100} onChange={(e) => setForm({ ...form, ref: e.target.value })} />
          <label htmlFor="p-totp">{t.totp}</label>
          <input id="p-totp" inputMode="numeric" autoComplete="one-time-code" value={form.totp} onChange={(e) => setForm({ ...form, totp: e.target.value })} style={{ maxWidth: "12rem" }} />
          <div className="actionbar" style={{ position: "static", border: 0 }}>
            <button type="button" disabled={busy || minor === 0 || form.totp.trim().length < 6} onClick={save}>{t.recordPayment}</button>
            <button type="button" className="secondary" onClick={() => setOpen(false)}>{t.cancel}</button>
          </div>
        </div>
      ))}
    </div>
  );
}
