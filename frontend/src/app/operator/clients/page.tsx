"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Client } from "@/lib/types";

const t = getMessages().clients;
type Form = "lia" | "dpa" | "user" | null;

export default function ClientsPage() {
  const [clients, setClients] = useState<Client[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ slug: "", legal_name: "", company_number: "", registered_address: "", billing_currency: "GBP" });
  const [form, setForm] = useState<{ id: string; kind: Form }>({ id: "", kind: null });
  const [fields, setFields] = useState({ a: "", b: "" });
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [devLink, setDevLink] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Client[]>("/api/v1/clients").then(setClients).catch(() => setError(getMessages().operator.loadError));
  }, []);
  useEffect(load, [load]);

  async function run(fn: () => Promise<string | void>) {
    setBusy(true); setError(null); setStatus(null);
    try { const s = await fn(); setStatus(s || t.saved); load(); }
    catch (e) { setError((e as Problem).detail ?? (e as Problem).title); }
    finally { setBusy(false); }
  }

  const create = () => run(async () => {
    await api("/api/v1/clients", { method: "POST", body: JSON.stringify({ ...draft, company_number: draft.company_number || null,
      registered_address: draft.registered_address || null }) });
    setAdding(false); return t.created;
  });

  const submitForm = () => run(async () => {
    const { id, kind } = form;
    if (kind === "lia") await api(`/api/v1/clients/${id}/lia`, { method: "POST", body: JSON.stringify({ reference: fields.a, approved_on: fields.b }) });
    if (kind === "dpa") await api(`/api/v1/clients/${id}/dpa`, { method: "POST", body: JSON.stringify({ version: fields.a, signed_on: fields.b }) });
    if (kind === "user") {
      const r = await api<{ email: string; dev_invite_link?: string }>(`/api/v1/clients/${id}/users`, { method: "POST", body: JSON.stringify({ email: fields.a }) });
      setForm({ id: "", kind: null });
      setDevLink(r.dev_invite_link ?? null);
      return t.invited.replace("{e}", r.email);
    }
    setForm({ id: "", kind: null });
  });

  if (!clients) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.title}</h1>
      {status && <p className="status" role="status">{status}</p>}
      {devLink && <p className="hint">{t.devLink}: <a href={devLink}>{devLink}</a></p>}
      {error && <p role="alert" className="error">{error}</p>}
      {!adding ? <button type="button" className="secondary" onClick={() => setAdding(true)}>{t.add}</button> : (
        <div className="panel" role="dialog" aria-labelledby="add-title" style={{ maxWidth: 560 }}>
          <h2 id="add-title">{t.add}</h2>
          <label htmlFor="c-name">{t.legalName}</label>
          <input id="c-name" value={draft.legal_name} onChange={(e) => setDraft({ ...draft, legal_name: e.target.value })} />
          <label htmlFor="c-slug">{t.slug}</label>
          <input id="c-slug" value={draft.slug} onChange={(e) => setDraft({ ...draft, slug: e.target.value.toLowerCase() })} />
          <label htmlFor="c-num">{t.companyNumber}</label>
          <input id="c-num" value={draft.company_number} onChange={(e) => setDraft({ ...draft, company_number: e.target.value })} />
          <label htmlFor="c-addr">{t.address}</label>
          <input id="c-addr" value={draft.registered_address} onChange={(e) => setDraft({ ...draft, registered_address: e.target.value })} />
          <label htmlFor="c-cur">{t.currency}</label>
          <select id="c-cur" value={draft.billing_currency} onChange={(e) => setDraft({ ...draft, billing_currency: e.target.value })}>
            <option value="GBP">GBP</option><option value="EUR">EUR</option>
          </select>
          <div className="actionbar" style={{ position: "static", border: 0 }}>
            <button type="button" disabled={busy || draft.legal_name.length < 2 || draft.slug.length < 2} onClick={create}>{t.create}</button>
            <button type="button" className="secondary" onClick={() => setAdding(false)}>{t.cancel}</button>
          </div>
        </div>
      )}
      {clients.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="grid" style={{ marginTop: 20 }}>
          {clients.map((c) => {
            const remaining = c.checklist.filter((i) => !i.done).length;
            return (
              <section key={c.id} className="card" aria-labelledby={`c-${c.id}`}>
                <h2 id={`c-${c.id}`}>{c.legal_name}</h2>
                <p className="muted" style={{ margin: 0 }}>{c.status}{c.status === "onboarding" ? `, ${remaining} to do` : ""}</p>
                <ul className="checklist">
                  {c.checklist.map((i) => <li key={i.key} data-done={i.done}>{i.detail}</li>)}
                </ul>
                {form.id === c.id && form.kind ? (
                  <div>
                    <label htmlFor={`fa-${c.id}`}>{form.kind === "lia" ? t.reference : form.kind === "dpa" ? t.version : t.email}</label>
                    <input id={`fa-${c.id}`} value={fields.a} onChange={(e) => setFields({ ...fields, a: e.target.value })} />
                    {form.kind !== "user" && (
                      <>
                        <label htmlFor={`fb-${c.id}`}>{form.kind === "lia" ? t.approvedOn : t.signedOn}</label>
                        <input id={`fb-${c.id}`} type="date" value={fields.b} onChange={(e) => setFields({ ...fields, b: e.target.value })} />
                      </>
                    )}
                    <div className="row-actions" style={{ marginTop: 10 }}>
                      <button type="button" disabled={busy || fields.a.length < 1 || (form.kind !== "user" && !fields.b)} onClick={submitForm}>{t.save}</button>
                      <button type="button" className="secondary" onClick={() => setForm({ id: "", kind: null })}>{t.cancel}</button>
                    </div>
                  </div>
                ) : (
                  <div className="row-actions">
                    {(["lia", "dpa", "user"] as const).map((k) => (
                      <button key={k} type="button" className="secondary" onClick={() => { setForm({ id: c.id, kind: k }); setFields({ a: "", b: "" }); }}>{t[k]}</button>
                    ))}
                    {c.status === "onboarding" && (
                      <button type="button" disabled={busy || remaining > 0} onClick={() => run(async () => {
                        await api(`/api/v1/clients/${c.id}/activate`, { method: "POST" }); return t.activated; })}>{t.activate}</button>
                    )}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}
