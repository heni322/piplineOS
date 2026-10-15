"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { OfferPanel } from "@/components/OfferPanel";
import { SendingPanel } from "@/components/SendingPanel";
import { TargetingPanel } from "@/components/TargetingPanel";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import { day } from "@/lib/format";
import type { Client } from "@/lib/types";

const t = getMessages().clients;
const stepNames = t.steps as Record<string, string>;
// The order an onboarding call goes in: the first item not done is the client's next step.
const ORDER = ["company_details", "lia", "dpa", "config", "pricing", "eu_representative", "client_user", "criteria_signed",
  "pricing_signed", "sending_domain"];
type Form = "lia" | "dpa" | "user" | "eurep" | "pause" | "details" | null;

export default function ClientsPage() {
  const [clients, setClients] = useState<Client[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ slug: "", legal_name: "", company_number: "", registered_address: "", billing_currency: "GBP" });
  const [form, setForm] = useState<{ id: string; kind: Form }>({ id: "", kind: null });
  const [fields, setFields] = useState({ a: "", b: "", c: "" });
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [targeting, setTargeting] = useState<{ id: string; name: string } | null>(null);
  const [ownForm, setOwnForm] = useState(false);
  const [panel, setPanel] = useState<{ id: string; name: string; kind: "offer" | "sending" } | null>(null);

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

  const createOwn = () => run(async () => {
    await api("/api/v1/own-account", { method: "POST", body: JSON.stringify({ legal_name: draft.legal_name,
      company_number: draft.company_number || null, registered_address: draft.registered_address || null }) });
    setOwnForm(false); return t.ownCreated;
  });

  const submitForm = () => run(async () => {
    const { id, kind } = form;
    if (kind === "lia") await api(`/api/v1/clients/${id}/lia`, { method: "POST", body: JSON.stringify({ reference: fields.a, approved_on: fields.b }) });
    if (kind === "dpa") await api(`/api/v1/clients/${id}/dpa`, { method: "POST", body: JSON.stringify({ version: fields.a, signed_on: fields.b }) });
    if (kind === "eurep") await api(`/api/v1/clients/${id}/eu-representative`, { method: "POST",
      body: JSON.stringify({ name: fields.a, contact_email: fields.b, country_code: fields.c.toUpperCase() }) });
    if (kind === "details") {
      await api(`/api/v1/clients/${id}/details`, { method: "PUT", body: JSON.stringify({ legal_name: fields.a,
        company_number: fields.b || null, registered_address: fields.c || null }) });
      setForm({ id: "", kind: null });
      return t.detailsSaved;
    }
    if (kind === "pause") {
      await api(`/api/v1/clients/${id}/pause`, { method: "POST", body: JSON.stringify({ reason: fields.a }) });
      setForm({ id: "", kind: null });
      return t.paused;
    }
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
      {!clients.some((c) => c.own) && (!ownForm
        ? <button type="button" className="secondary" style={{ marginLeft: 8 }} onClick={() => { setOwnForm(true); setAdding(false); }}>{t.ownAdd}</button>
        : (
          <div className="panel" role="dialog" aria-labelledby="own-title" style={{ maxWidth: 560 }}>
            <h2 id="own-title">{t.ownTitle}</h2>
            <p className="muted">{t.ownIntro}</p>
            <label htmlFor="own-name">{t.legalName}</label>
            <input id="own-name" value={draft.legal_name} onChange={(e) => setDraft({ ...draft, legal_name: e.target.value })} />
            <label htmlFor="own-num">{t.companyNumber}</label>
            <input id="own-num" value={draft.company_number} onChange={(e) => setDraft({ ...draft, company_number: e.target.value })} />
            <label htmlFor="own-addr">{t.address}</label>
            <input id="own-addr" value={draft.registered_address} onChange={(e) => setDraft({ ...draft, registered_address: e.target.value })} />
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button type="button" disabled={busy || draft.legal_name.trim().length < 2} onClick={createOwn}>{t.ownCreate}</button>
              <button type="button" className="secondary" onClick={() => setOwnForm(false)}>{t.cancel}</button>
            </div>
          </div>
        ))}
      {targeting && (
        <TargetingPanel key={targeting.id} id={targeting.id} name={targeting.name} onClose={() => setTargeting(null)}
          onSaved={(m) => { setTargeting(null); setStatus(m); }} />
      )}
      {panel?.kind === "offer" && <OfferPanel key={panel.id} id={panel.id} name={panel.name} onClose={() => setPanel(null)}
        onSaved={(m) => { setPanel(null); setStatus(m); load(); }} />}
      {panel?.kind === "sending" && <SendingPanel key={panel.id} id={panel.id} name={panel.name} onClose={() => setPanel(null)}
        onSaved={(m) => { setPanel(null); setStatus(m); load(); }} />}
      {clients.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="grid" style={{ marginTop: 20 }}>
          {clients.map((c) => {
            const remaining = c.checklist.filter((i) => !i.done).length;
            return (
              <section key={c.id} className="card" aria-labelledby={`c-${c.id}`}>
                <h2 id={`c-${c.id}`}>{c.legal_name}</h2>
                {c.own && <p className="eyebrow" style={{ margin: "0 0 4px" }}>{t.ownBadge}</p>}
                <p className="muted" style={{ margin: 0 }}>{c.status}{c.status === "onboarding" ? `, ${remaining} to do` : ""}</p>
                {c.status === "paused" && (
                  <p className="error" role="status">{t.pausedSince.replace("{d}", c.paused_at ? day(c.paused_at) : "").replace("{r}", c.paused_reason ?? "")}</p>
                )}
                {c.status === "onboarding" && (() => {
                  const next = ORDER.map((k) => c.checklist.find((i) => i.key === k)).find((i) => i && !i.done);
                  const open = (kind: Form) => { setForm({ id: c.id, kind }); setFields({ a: "", b: "", c: "" }); };
                  const action: Record<string, React.ReactNode> = {
                    lia: <button type="button" onClick={() => open("lia")}>{t.lia}</button>,
                    dpa: <button type="button" onClick={() => open("dpa")}>{t.dpa}</button>,
                    config: <button type="button" onClick={() => setPanel({ id: c.id, name: c.legal_name, kind: "offer" })}>{t.offer}</button>,
                    pricing: <button type="button" onClick={() => setPanel({ id: c.id, name: c.legal_name, kind: "offer" })}>{t.offer}</button>,
                    eu_representative: <button type="button" onClick={() => open("eurep")}>{t.eurep}</button>,
                    client_user: <button type="button" onClick={() => open("user")}>{t.user}</button>,
                    criteria_signed: <span className="muted">{t.waitingClient}</span>,
                    pricing_signed: <span className="muted">{t.waitingClient}</span>,
                    sending_domain: c.checklist.some((i) => i.key === "sending_domain") && (
                      <span className="row-actions">
                        <button type="button" onClick={() => setPanel({ id: c.id, name: c.legal_name, kind: "sending" })}>{t.sending}</button>
                        <Link href="/operator/inboxes">{t.openInboxes}</Link>
                      </span>),
                  };
                  return (
                    <div className="next-step" role="group" aria-label={t.nextStep}>
                      {next ? <><strong>{t.nextStep}: {stepNames[next.key] ?? next.key}</strong>
                        <span className="muted">{next.detail}</span><div style={{ marginTop: 8 }}>{action[next.key]}</div></>
                        : <strong>{t.allDone}</strong>}
                    </div>
                  );
                })()}
                <ul className="checklist">
                  {c.checklist.map((i) => <li key={i.key} data-done={i.done}>{i.detail}</li>)}
                </ul>
                {form.id === c.id && form.kind === "eurep" ? (
                  <div>
                    <label htmlFor={`fa-${c.id}`}>{t.repName}</label>
                    <input id={`fa-${c.id}`} value={fields.a} onChange={(e) => setFields({ ...fields, a: e.target.value })} />
                    <label htmlFor={`fb-${c.id}`}>{t.repEmail}</label>
                    <input id={`fb-${c.id}`} type="email" value={fields.b} onChange={(e) => setFields({ ...fields, b: e.target.value })} />
                    <label htmlFor={`fc-${c.id}`}>{t.repCountry}</label>
                    <input id={`fc-${c.id}`} maxLength={2} value={fields.c} onChange={(e) => setFields({ ...fields, c: e.target.value })} />
                    <div className="row-actions" style={{ marginTop: 10 }}>
                      <button type="button" disabled={busy || fields.a.length < 2 || !fields.b.includes("@") || fields.c.length !== 2} onClick={submitForm}>{t.save}</button>
                      <button type="button" className="secondary" onClick={() => setForm({ id: "", kind: null })}>{t.cancel}</button>
                    </div>
                  </div>
                ) : form.id === c.id && form.kind === "details" ? (
                  <div>
                    <label htmlFor={`fa-${c.id}`}>{t.legalName}</label>
                    <input id={`fa-${c.id}`} value={fields.a} onChange={(e) => setFields({ ...fields, a: e.target.value })} />
                    <label htmlFor={`fb-${c.id}`}>{t.companyNumber}</label>
                    <input id={`fb-${c.id}`} value={fields.b} onChange={(e) => setFields({ ...fields, b: e.target.value })} />
                    <label htmlFor={`fc-${c.id}`}>{t.address}</label>
                    <input id={`fc-${c.id}`} value={fields.c} onChange={(e) => setFields({ ...fields, c: e.target.value })} />
                    <div className="row-actions" style={{ marginTop: 10 }}>
                      <button type="button" disabled={busy || fields.a.trim().length < 2} onClick={submitForm}>{t.save}</button>
                      <button type="button" className="secondary" onClick={() => setForm({ id: "", kind: null })}>{t.cancel}</button>
                    </div>
                  </div>
                ) : form.id === c.id && form.kind === "pause" ? (
                  <div>
                    <p className="hint">{t.pauseHint}</p>
                    <label htmlFor={`fa-${c.id}`}>{t.pauseReason}</label>
                    <input id={`fa-${c.id}`} value={fields.a} onChange={(e) => setFields({ ...fields, a: e.target.value })} />
                    <div className="row-actions" style={{ marginTop: 10 }}>
                      <button type="button" className="danger" disabled={busy || fields.a.trim().length < 3} onClick={submitForm}>{t.pauseConfirm}</button>
                      <button type="button" className="secondary" onClick={() => setForm({ id: "", kind: null })}>{t.cancel}</button>
                    </div>
                  </div>
                ) : form.id === c.id && form.kind ? (
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
                      <button key={k} type="button" className="secondary" onClick={() => { setForm({ id: c.id, kind: k }); setFields({ a: "", b: "", c: "" }); }}>{t[k]}</button>
                    ))}
                    <button type="button" className="secondary" onClick={() => { setPanel({ id: c.id, name: c.legal_name, kind: "offer" }); setStatus(null); }}>{t.offer}</button>
                    <button type="button" className="secondary" onClick={() => { setForm({ id: c.id, kind: "details" }); setFields({ a: c.legal_name, b: c.company_number ?? "", c: c.registered_address ?? "" }); }}>{t.details}</button>
                    <button type="button" className="secondary" onClick={() => { setTargeting({ id: c.id, name: c.legal_name }); setStatus(null); }}>{t.targeting}</button>
                    <button type="button" className="secondary" onClick={() => { setPanel({ id: c.id, name: c.legal_name, kind: "sending" }); setStatus(null); }}>{t.sending}</button>
                    {c.checklist.some((i) => i.key === "eu_representative") && (
                      <button type="button" className="secondary" onClick={() => { setForm({ id: c.id, kind: "eurep" }); setFields({ a: "", b: "", c: "" }); }}>{t.eurep}</button>
                    )}
                    {c.status === "active" && (
                      <button type="button" className="danger" onClick={() => { setForm({ id: c.id, kind: "pause" }); setFields({ a: "", b: "", c: "" }); }}>{t.pause}</button>
                    )}
                    {c.status === "paused" && (
                      <button type="button" disabled={busy} onClick={() => run(async () => {
                        await api(`/api/v1/clients/${c.id}/resume`, { method: "POST" }); return t.resumed; })}>{t.resume}</button>
                    )}
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
