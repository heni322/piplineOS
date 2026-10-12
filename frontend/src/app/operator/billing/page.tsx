"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { InvoiceDocument } from "@/components/InvoiceDocument";
import { api, type Problem } from "@/lib/api";
import { money, monthStart } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Dispute, Invoice, Tenant } from "@/lib/types";

const t = getMessages().billing;
const reason = (r: string) => (t.reasons as Record<string, string>)[r] ?? r.replaceAll("_", " ").toLowerCase();
type DraftOut = { invoice_id: string | null; excluded: { meeting_id: string; label: string; reasons: string[] }[];
  blocked_reasons: string[]; total_minor: number };
type Vat = { turnover_minor: number; threshold_minor: number | null; pct: number | null; warning: string | null; registered: boolean };

export default function BillingPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenant, setTenant] = useState("");
  const [period, setPeriod] = useState(monthStart());
  const [invoices, setInvoices] = useState<Invoice[] | null>(null);
  const [open, setOpen] = useState<Invoice | null>(null);
  const [lastDraft, setLastDraft] = useState<DraftOut | null>(null);
  const [disputes, setDisputes] = useState<Dispute[]>([]);
  const [vat, setVat] = useState<Vat | null>(null);
  const [totp, setTotp] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [resolving, setResolving] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const totpRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (issuing) totpRef.current?.focus(); }, [issuing]);

  useEffect(() => {
    api<Tenant[]>("/api/v1/tenants").then((ts) => { setTenants(ts); setTenant((cur) => cur || ts[0]?.id || ""); })
      .catch(() => setError(getMessages().operator.loadError));
    api<Vat>("/api/v1/billing/vat-threshold").then(setVat).catch(() => undefined);
  }, []);

  const load = useCallback(async () => {
    if (!tenant) return;
    try {
      setInvoices(await api<Invoice[]>(`/api/v1/invoices?tenant_id=${tenant}`));
      setDisputes(await api<Dispute[]>(`/api/v1/disputes?tenant_id=${tenant}`));
    } catch { setError(getMessages().operator.loadError); }
  }, [tenant]);
  useEffect(() => { load(); }, [load]);

  async function show(id: string) {
    setError(null); setIssuing(false);
    try { setOpen(await api<Invoice>(`/api/v1/invoices/${id}`)); } catch (e) { setError((e as Problem).title); }
  }

  async function redraft() {
    setBusy(true); setError(null); setStatus(null);
    try {
      const d = await api<DraftOut>("/api/v1/invoices/draft", { method: "POST", body: JSON.stringify({ tenant_id: tenant, period_start: period }) });
      setLastDraft(d); setStatus(t.drafted); await load();
      if (d.invoice_id) await show(d.invoice_id); else setOpen(null);
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  async function issue() {
    if (!open) return;
    setBusy(true); setError(null);
    try {
      const r = await api<{ number: string }>(`/api/v1/invoices/${open.id}/issue`, { method: "POST", body: JSON.stringify({ totp_code: totp.trim() }) });
      setStatus(t.issued.replace("{n}", r.number)); setIssuing(false); setTotp(""); await load(); await show(open.id);
    } catch (e) {
      const p = e as Problem;
      setError(p.detail ? p.detail.split(", ").map(reason).join("; ") : p.title);
    } finally { setBusy(false); }
  }

  async function resolve(id: string, outcome: "upheld" | "rejected") {
    setBusy(true); setError(null);
    try {
      await api(`/api/v1/disputes/${id}/resolve`, { method: "POST", body: JSON.stringify({ outcome, note: note.trim() }) });
      setStatus(t.resolved); setResolving(null); setNote(""); await load();
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  const blocked = open?.status === "draft" ? open.blocked_reasons : [];
  return (
    <>
      <h1>{t.title}</h1>
      {vat && (
        <p className="muted">
          {t.vat}: {vat.threshold_minor === null ? t.vatUnknown
            : t.vatOf.replace("{pct}", String(vat.pct)).replace("{threshold}", money(vat.threshold_minor))}
          {vat.warning && <strong className="error"> {t.vatWarn.replace("{pct}", String(vat.pct))}</strong>}
        </p>
      )}
      <div className="toolbar">
        <div>
          <label htmlFor="tenant">{t.client}</label>
          <select id="tenant" value={tenant} onChange={(e) => { setTenant(e.target.value); setOpen(null); setLastDraft(null); }}>
            {tenants.map((x) => <option key={x.id} value={x.id}>{x.legal_name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="period">{t.period}</label>
          <input id="period" type="month" value={period.slice(0, 7)} onChange={(e) => setPeriod(`${e.target.value}-01`)} />
        </div>
        <button type="button" disabled={busy || !tenant} onClick={redraft}>{t.draft}</button>
      </div>
      {status && <p className="status" role="status">{status}</p>}
      {error && <p role="alert" className="error">{error}</p>}

      <div className="split">
        <section aria-labelledby="inv-h">
          <h2 id="inv-h">{t.invoices}</h2>
          {!invoices ? <p className="muted">{getMessages().operator.loading}</p> : invoices.length === 0 ? <p className="muted">{t.empty}</p> : (
            <ul className="queue">
              {invoices.map((i) => (
                <li key={i.id}>
                  <button type="button" aria-current={open?.id === i.id} onClick={() => show(i.id)}>
                    <span className="co">{i.period_start ? new Date(i.period_start).toLocaleDateString("en-GB", { month: "long", year: "numeric" }) : i.number}</span>
                    <span className="meta">{i.status}, {money(i.total_minor, i.currency)}{i.status !== "draft" ? `, ${i.number}` : ""}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <h2 style={{ marginTop: 24 }}>{t.disputes}</h2>
          {disputes.length === 0 ? <p className="muted">{t.noDisputes}</p> : (
            <ul className="queue">
              {disputes.map((d) => (
                <li key={d.id} style={{ padding: "10px 8px", borderBottom: "1px solid var(--rule)" }}>
                  <span className="co">{d.company}</span>
                  <span className="meta">{getMessages().client.kinds[d.kind as "other"] ?? d.kind}, {d.status}</span>
                  <p style={{ margin: "6px 0" }}>{d.reason}</p>
                  {d.status === "open" && (resolving === d.id ? (
                    <div>
                      <label htmlFor={`n-${d.id}`}>{t.note}</label>
                      <textarea id={`n-${d.id}`} value={note} onChange={(e) => setNote(e.target.value)} style={{ minHeight: "4rem", fontFamily: "var(--sans)" }} />
                      <div className="row-actions" style={{ marginTop: 8 }}>
                        <button type="button" className="danger" disabled={busy || note.trim().length < 5} onClick={() => resolve(d.id, "upheld")}>{t.upheld}</button>
                        <button type="button" className="secondary" disabled={busy || note.trim().length < 5} onClick={() => resolve(d.id, "rejected")}>{t.rejected}</button>
                      </div>
                    </div>
                  ) : <button type="button" className="secondary" onClick={() => { setResolving(d.id); setNote(""); }}>{t.resolve}</button>)}
                  {d.resolution_note && <p className="muted">{d.resolution_note}</p>}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label={t.view}>
          {lastDraft && (
            <div className="panel" style={{ marginTop: 0, marginBottom: 16 }}>
              <h3 style={{ marginTop: 0 }}>{t.excluded}</h3>
              {lastDraft.excluded.length === 0 ? <p className="muted">{t.excludedNone}</p> : (
                <ul className="reasons">{lastDraft.excluded.map((x) => <li key={x.meeting_id}>{x.label}: {x.reasons.map(reason).join("; ")}</li>)}</ul>
              )}
            </div>
          )}
          {open && (
            <>
              <InvoiceDocument inv={open} />
              {open.status === "draft" && (
                <div className="panel">
                  <h3 style={{ marginTop: 0 }}>{blocked.length ? t.blocked : t.blockedNone}</h3>
                  {blocked.length > 0 && <ul className="reasons">{blocked.map((b) => <li key={b}>{reason(b)}</li>)}</ul>}
                  {issuing ? (
                    <div role="dialog" aria-labelledby="issue-title">
                      <h3 id="issue-title">{t.issueTitle}</h3>
                      <p className="hint">{t.issueHint}</p>
                      <label htmlFor="totp">{t.totp}</label>
                      <input id="totp" ref={totpRef} inputMode="numeric" autoComplete="one-time-code" value={totp} onChange={(e) => setTotp(e.target.value)} style={{ maxWidth: "12rem" }} />
                      <div className="actionbar" style={{ position: "static", border: 0 }}>
                        <button type="button" disabled={busy || totp.trim().length < 6} onClick={issue}>{t.issue}</button>
                        <button type="button" className="secondary" onClick={() => setIssuing(false)}>{t.cancel}</button>
                      </div>
                    </div>
                  ) : <button type="button" className="secondary" onClick={() => setIssuing(true)}>{t.issue}</button>}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
