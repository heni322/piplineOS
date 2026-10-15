"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { day } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Testimonial } from "@/lib/types";

const t = getMessages().proof;
const scopes = t.scopes as Record<string, string>;

/** The client admin's testimonial, with the scope they consent to, and withdrawal at any time. */
export function TestimonialForm() {
  const [admin, setAdmin] = useState(false);
  const [mine, setMine] = useState<Testimonial[]>([]);
  const [form, setForm] = useState({ quote: "", attribution: "", scope: "anonymous" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api<Testimonial[]>("/api/v1/testimonials?include_withdrawn=true").then(setMine).catch(() => undefined);
  }, []);
  useEffect(() => {
    api<{ role: string }>("/api/v1/auth/me").then((me) => { if (me.role === "tenant_admin") { setAdmin(true); load(); } }).catch(() => undefined);
  }, [load]);
  if (!admin) return null;

  async function run(fn: () => Promise<void>, ok: string) {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ ok: true, text: ok }); load(); }
    catch (e) { setMsg({ ok: false, text: (e as Problem).detail ?? (e as Problem).title }); } finally { setBusy(false); }
  }
  const valid = form.quote.trim().length >= 20 && (form.scope === "anonymous" || form.attribution.trim().length >= 2);
  return (
    <section className="panel" aria-labelledby="proof-h" style={{ marginTop: 28, maxWidth: 640 }}>
      <h2 id="proof-h">{t.clientTitle}</h2>
      <p className="muted">{t.clientIntro}</p>
      {msg && <p className={msg.ok ? "status" : "error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      <label htmlFor="pr-quote">{t.quote}</label>
      <textarea id="pr-quote" rows={3} value={form.quote} onChange={(e) => setForm({ ...form, quote: e.target.value })} />
      <label htmlFor="pr-scope">{t.scope}</label>
      <select id="pr-scope" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
        {Object.entries(scopes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      {form.scope !== "anonymous" && (
        <>
          <label htmlFor="pr-attr">{t.attribution}</label>
          <input id="pr-attr" value={form.attribution} onChange={(e) => setForm({ ...form, attribution: e.target.value })} />
        </>
      )}
      <div className="row-actions" style={{ marginTop: 10 }}>
        <button type="button" disabled={busy || !valid} onClick={() => run(async () => {
          await api("/api/v1/testimonials", { method: "POST", body: JSON.stringify({ quote: form.quote, scope: form.scope,
            attribution: form.scope === "anonymous" ? null : form.attribution }) });
          setForm({ quote: "", attribution: "", scope: "anonymous" });
        }, t.thanks)}>{t.submit}</button>
      </div>
      {mine.length > 0 && (
        <ul className="states" style={{ marginTop: 14 }}>
          {mine.map((x) => (
            <li key={x.id}>
              <span>“{x.quote}” <small className="muted">{scopes[x.scope]}</small></span>
              {x.withdrawn_at ? <span className="muted">{t.withdrawn.replace("{d}", day(x.withdrawn_at))}</span>
                : <button type="button" className="secondary" disabled={busy}
                  onClick={() => run(async () => { await api(`/api/v1/testimonials/${x.id}/withdraw`, { method: "POST" }); }, t.withdrawnOk)}>{t.withdraw}</button>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
