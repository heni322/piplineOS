"use client";

import { useEffect, useState } from "react";
import { getMessages } from "@/lib/i18n";

const t = getMessages().landing;

/** The public pilot-request form: consent is explicit; a hidden honeypot field catches simple bots. */
export function PilotForm() {
  const [f, setF] = useState({ name: "", company: "", email: "", message: "", consent: false, website: "" });
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  // The consent wording comes from the API, which stores the same text with the request (one source of truth).
  const [consent, setConsent] = useState<string>(t.fConsent);
  useEffect(() => {
    fetch("/api/v1/pilot-requests/consent").then((r) => (r.ok ? r.json() : null))
      .then((j: { text?: string } | null) => { if (j?.text) setConsent(j.text); }).catch(() => undefined);
  }, []);
  const valid = f.name.trim().length >= 2 && f.company.trim().length >= 2 && /.+@.+\..+/.test(f.email) && f.consent;

  async function send() {
    setState("sending");
    try {
      const r = await fetch("/api/v1/pilot-requests", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...f, message: f.message || null, website: f.website || null }) });
      setState(r.status === 202 ? "sent" : "error");
    } catch { setState("error"); }
  }

  if (state === "sent") return <p className="status" role="status">{t.fSent}</p>;
  return (
    <form className="panel" aria-labelledby="pilot-form-h" style={{ maxWidth: 560 }} onSubmit={(e) => { e.preventDefault(); if (valid) void send(); }}>
      <h2 id="pilot-form-h" style={{ marginTop: 0 }}>{t.formTitle}</h2>
      <p className="muted">{t.formIntro}</p>
      {state === "error" && <p className="error" role="alert">{t.fError}</p>}
      <label htmlFor="pf-name">{t.fName}</label>
      <input id="pf-name" autoComplete="name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <label htmlFor="pf-company">{t.fCompany}</label>
      <input id="pf-company" autoComplete="organization" value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} />
      <label htmlFor="pf-email">{t.fEmail}</label>
      <input id="pf-email" type="email" autoComplete="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
      <label htmlFor="pf-message">{t.fMessage}</label>
      <textarea id="pf-message" rows={3} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} />
      <div className="sr-only" aria-hidden="true">
        <label htmlFor="pf-website">{t.fHoney}</label>
        <input id="pf-website" tabIndex={-1} autoComplete="off" value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} />
      </div>
      <label className="inline" style={{ marginTop: 12 }}>
        <input type="checkbox" checked={f.consent} onChange={(e) => setF({ ...f, consent: e.target.checked })} /> {consent}
      </label>
      <div className="row-actions" style={{ marginTop: 12 }}>
        <button type="submit" disabled={!valid || state === "sending"}>{t.fSend}</button>
      </div>
    </form>
  );
}
