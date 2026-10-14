"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { AdequacyRow, ChecklistItem, CountryRow, GovernanceStatus, ReadinessItem, TaxRule } from "@/lib/types";

const t = getMessages().governance;
const fill = (s: string, v: Record<string, string | number>) => Object.entries(v).reduce((a, [k, x]) => a.replaceAll(`{${k}}`, String(x)), s);
const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const today = () => new Date().toISOString().slice(0, 10);
const label = (map: Record<string, string>, k: string) => map[k] ?? k.replaceAll("_", " ");

type Form =
  | { kind: "signoff"; id: string; title: string }
  | { kind: "counsel" | "enable" | "disable"; code: string; name: string }
  | { kind: "accountant"; code: string };

export default function GovernancePage() {
  const [status, setStatus] = useState<GovernanceStatus | null>(null);
  const [readiness, setReadiness] = useState<ReadinessItem[]>([]);
  const [items, setItems] = useState<ChecklistItem[]>([]);
  const [countries, setCountries] = useState<CountryRow[]>([]);
  const [tax, setTax] = useState<TaxRule[]>([]);
  const [adequacy, setAdequacy] = useState<AdequacyRow[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [fields, setFields] = useState({ totp: "", text: "", date: today() });
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const firstField = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [s, g, c, x, a, r] = await Promise.all([
        api<GovernanceStatus>("/api/v1/governance/status"), api<ChecklistItem[]>("/api/v1/golive"),
        api<CountryRow[]>("/api/v1/countries"), api<TaxRule[]>("/api/v1/tax-rules"), api<AdequacyRow[]>("/api/v1/governance/adequacy"),
        api<ReadinessItem[]>("/api/v1/governance/readiness"),
      ]);
      setStatus(s); setItems(g); setCountries(c); setTax(x); setAdequacy(a); setReadiness(r);
    } catch { setError(getMessages().operator.loadError); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (form) firstField.current?.focus(); }, [form]);

  function open(f: Form) { setForm(f); setFields({ totp: "", text: "", date: today() }); setError(null); setNotice(null); }

  async function submit() {
    if (!form) return;
    setBusy(true); setError(null);
    const code = fields.totp.trim();
    try {
      if (form.kind === "signoff") {
        await api(`/api/v1/golive/${form.id}/sign-off`, { method: "POST", body: JSON.stringify({ totp_code: code, evidence_url: fields.text.trim() || null }) });
        setNotice(fill(t.signedOk, { title: form.title }));
      } else if (form.kind === "counsel") {
        await api(`/api/v1/countries/${form.code}/counsel-verification`, { method: "POST",
          body: JSON.stringify({ totp_code: code, solicitor: fields.text.trim(), reviewed_on: fields.date }) });
        setNotice(fill(t.reviewSaved, { c: form.name }));
      } else if (form.kind === "enable") {
        await api(`/api/v1/countries/${form.code}/enable`, { method: "POST", body: JSON.stringify({ totp_code: code }) });
        setNotice(fill(t.enabledOk, { c: form.name }));
      } else if (form.kind === "disable") {
        await api(`/api/v1/countries/${form.code}/disable`, { method: "POST", body: JSON.stringify({ reason: fields.text.trim() }) });
        setNotice(fill(t.disabledOk, { c: form.name }));
      } else {
        await api(`/api/v1/tax-rules/${form.code}/accountant-verification`, { method: "POST",
          body: JSON.stringify({ totp_code: code, accountant: fields.text.trim(), reviewed_on: fields.date }) });
        setNotice(fill(t.accountantSaved, { c: form.code }));
      }
      setForm(null); await load();
    } catch (e) {
      const p = e as Problem;
      setError(p.status === 403 && p.title.startsWith("MFA") ? t.mfaFailed
        : p.detail ? p.detail.split(", ").map((b) => label(t.blockers, b)).join("; ") : p.title);
    } finally { setBusy(false); }
  }

  if (!status) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;

  const needsCode = form && form.kind !== "disable";
  const needsText = form && form.kind !== "enable";
  const needsDate = form && (form.kind === "counsel" || form.kind === "accountant");
  const minText = form?.kind === "disable" ? 10 : form?.kind === "signoff" ? 0 : 3;
  const ready = !!form && (!needsCode || fields.totp.trim().length >= 6) && fields.text.trim().length >= minText;
  const formTitle = !form ? "" : form.kind === "signoff" ? form.title : form.kind === "counsel" ? `${t.recordReview}: ${form.name}`
    : form.kind === "enable" ? fill(t.enableTitle, { c: form.name }) : form.kind === "disable" ? fill(t.disableTitle, { c: form.name })
    : `${t.recordAccountant}: ${form.code}`;
  const textLabel = form?.kind === "signoff" ? t.evidence : form?.kind === "counsel" ? t.solicitor : form?.kind === "disable" ? t.reason : t.accountantName;

  const formPanel = form && (
    <div className="panel" role="dialog" aria-labelledby="gov-form-title">
      <h3 id="gov-form-title" style={{ marginTop: 0 }}>{formTitle}</h3>
      {form.kind === "enable" && <p className="hint">{t.enableHint}</p>}
      {needsText && (
        <>
          <label htmlFor="gov-text">{textLabel}</label>
          <input id="gov-text" ref={firstField} type={form.kind === "signoff" ? "url" : "text"} value={fields.text}
            onChange={(e) => setFields({ ...fields, text: e.target.value })} />
        </>
      )}
      {needsDate && (
        <>
          <label htmlFor="gov-date">{t.reviewedOn}</label>
          <input id="gov-date" type="date" max={today()} value={fields.date} onChange={(e) => setFields({ ...fields, date: e.target.value })} style={{ maxWidth: "12rem" }} />
        </>
      )}
      {needsCode && (
        <>
          <label htmlFor="gov-totp">{t.totp}</label>
          <input id="gov-totp" ref={needsText ? undefined : firstField} inputMode="numeric" autoComplete="one-time-code" value={fields.totp}
            onChange={(e) => setFields({ ...fields, totp: e.target.value })} style={{ maxWidth: "12rem" }} />
        </>
      )}
      <div className="row-actions" style={{ marginTop: 12 }}>
        <button type="button" className={form.kind === "disable" ? "danger" : undefined} disabled={busy || !ready} onClick={submit}>
          {form.kind === "signoff" ? t.confirm : form.kind === "counsel" ? t.saveReview : form.kind === "enable" ? t.enable
            : form.kind === "disable" ? t.disable : t.recordAccountant}
        </button>
        <button type="button" className="secondary" onClick={() => setForm(null)}>{t.cancel}</button>
      </div>
    </div>
  );
  const inline = (match: (f: Form) => boolean) => (form && match(form) ? formPanel : null);

  const modes = status.adapter_modes;
  const providersLive = modes.email === "live" || modes.calendar === "live";
  const keys = [
    { name: t.keyAdapters, on: providersLive, text: providersLive ? fill(t.keyAdaptersOn, { email: modes.email ?? "", calendar: modes.calendar ?? "" }) : fill(t.keyAdaptersOff, { mode: modes.email ?? "" }) },
    { name: t.keyClients, on: status.live_clients > 0, text: status.live_clients > 0 ? fill(t.keyClientsOn, { n: status.live_clients }) : t.keyClientsOff },
    { name: t.keyGolive, on: status.golive_complete && status.global_go_live,
      text: status.golive_complete ? t.keyGoliveOn : fill(t.keyGoliveOff, { signed: status.golive_signed, total: status.golive_items }) },
  ];

  const modeName = (m: string) => (m === "live" ? t.modeLive : m === "sandbox" ? t.modeSandbox : t.modeMock);
  const paid = (m: string) => (m === "live" ? t.srcPaidLive : m === "sandbox" ? t.srcPaidSandbox : t.srcPaidMock);
  const mode = (k: string) => modes[k] ?? "mock";
  const sources = [
    { name: t.srcSignals, mode: mode("signals"), text: mode("signals") === "mock" ? t.srcSignalsMock : t.srcSignalsReal },
    { name: t.srcEnrichment, mode: mode("enrichment"), text: paid(mode("enrichment")) },
    { name: t.srcVerifier, mode: mode("verifier"), text: paid(mode("verifier")) },
    { name: t.srcRegistry, mode: mode("companies_house"), text: mode("companies_house") === "mock" ? t.srcRegistryMock : t.srcRegistryReal },
    { name: t.srcLlm, mode: mode("llm"), text: mode("llm") === "live" ? t.srcLlmReal : mode("llm") === "sandbox" ? t.srcLlmLocal : t.srcLlmMock },
  ];

  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      {notice && <p className="status" role="status">{notice}</p>}
      {error && !form && <p role="alert" className="error">{error}</p>}

      <section aria-labelledby="keys-h">
        <h2 id="keys-h" style={{ marginTop: 20 }}>{t.keysTitle}</h2>
        <ol className="keys">
          {keys.map((k) => (
            <li key={k.name} data-on={k.on}>
              <span className="key-state">{k.on ? t.on : t.off}</span>
              <strong>{k.name}</strong>
              <span className="muted">{k.text}</span>
            </li>
          ))}
        </ol>
        <p className="hint">{t.globalFlag}: <strong>{status.global_go_live ? t.on : t.off}</strong></p>
      </section>

      <section aria-labelledby="ready-h">
        <h2 id="ready-h" style={{ marginTop: 32 }}>{t.readinessTitle} ({readiness.filter((r) => r.state === "done").length}/{readiness.length})</h2>
        <p className="muted" style={{ maxWidth: "70ch" }}>{t.readinessIntro}</p>
        <ul className="golive readiness">
          {readiness.map((r) => (
            <li key={r.key} data-done={r.state === "done"}>
              <div>
                <strong>{r.title}</strong>
                <p className="muted" style={{ margin: "2px 0 0" }}>{r.detail}</p>
              </div>
              <div className="golive-act">
                <span className={r.state === "done" ? "outcome-allow" : r.state === "todo" ? "outcome-block" : "muted"}>
                  {r.state === "done" ? t.stateDone : r.state === "todo" ? t.stateTodo : t.stateInfo}
                </span>
                <span className="hint" style={{ display: "block" }}>{r.who === "you" ? t.whoYou : t.whoSystem}</span>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="sources-h">
        <h2 id="sources-h" style={{ marginTop: 32 }}>{t.sourcesTitle}</h2>
        <p className="muted" style={{ maxWidth: "70ch" }}>{t.sourcesIntro}</p>
        <ul className="keys">
          {sources.map((x) => (
            <li key={x.name} data-on={x.mode === "live"}>
              <span className="key-state">{modeName(x.mode)}</span>
              <strong>{x.name}</strong>
              <span className="muted">{x.text}</span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="check-h">
        <h2 id="check-h" style={{ marginTop: 32 }}>{t.checklistTitle} ({status.golive_signed}/{status.golive_items})</h2>
        <p className="muted" style={{ maxWidth: "70ch" }}>{t.checklistIntro}</p>
        <ul className="golive">
          {items.map((i) => (
            <li key={i.id} data-done={i.signed_off}>
              <div>
                <strong>{i.title}</strong>
                <p className="muted" style={{ margin: "2px 0 0" }}>{i.description}</p>
              </div>
              <div className="golive-act">
                {i.signed_off && i.signed_off_at ? <span>{fill(t.signedOn, { date: day(i.signed_off_at) })}</span> : (
                  <button type="button" className="secondary" onClick={() => open({ kind: "signoff", id: i.id, title: i.title })}>{t.signOff}</button>
                )}
              </div>
              {inline((f) => f.kind === "signoff" && f.id === i.id)}
              {form?.kind === "signoff" && form.id === i.id && error && <p role="alert" className="error">{error}</p>}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="countries-h">
        <h2 id="countries-h" style={{ marginTop: 32 }}>{t.countriesTitle}</h2>
        <p className="muted" style={{ maxWidth: "70ch" }}>{t.countriesIntro}</p>
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">{t.country}</th><th scope="col">{t.stage}</th><th scope="col">{t.rule}</th>
              <th scope="col">{t.counsel}</th><th scope="col">{t.built}</th><th scope="col">{t.stillNeeded}</th>
              <th scope="col">{t.status}</th><th scope="col"><span className="sr-only">{t.status}</span></th></tr></thead>
            <tbody>
              {countries.map((c) => (
                <tr key={c.code}>
                  <td>{c.name} <span className="muted">({c.code})</span></td>
                  <td>{label(t.stages, c.stage)}</td>
                  <td>{c.rule_mode ? label(t.modes, c.rule_mode) : "–"}</td>
                  <td>{c.verified_by_counsel && c.last_reviewed ? fill(t.reviewed, { date: day(c.last_reviewed) }) : t.notReviewed}</td>
                  <td className="hint" style={{ minWidth: 220 }}>
                    {c.build ? (
                      <>
                        <div>{t.register}: {c.build.register_name} <span className={c.build.register_ready ? "outcome-allow" : undefined}>({c.build.register_detail})</span></div>
                        <div>{t.texts}: {[...c.build.languages_with_texts, ...c.build.languages_in_english.map((l) => fill(t.englishFor, { lang: l }))].join(", ") || "–"}</div>
                        <div>{t.safetyTested}: {c.build.safety_tested.join(", ")}</div>
                        {c.build.note && <div>{c.build.note}</div>}
                      </>
                    ) : "–"}
                  </td>
                  <td style={{ minWidth: 220 }}>
                    {c.build && c.build.still_needed.length > 0 ? (
                      <ul className="reasons" style={{ margin: 0 }}>
                        {c.build.still_needed.map((n) => <li key={n.what}><strong>{label(t.who, n.who)}</strong>: {n.what}</li>)}
                      </ul>
                    ) : <span className="outcome-allow">{t.nothingLeft}</span>}
                  </td>
                  <td>
                    {c.enabled ? <strong className="outcome-allow">{t.enabled}</strong> : t.disabled}
                    {!c.enabled && c.blocked_by.length > 0 && <div className="hint">{c.blocked_by.map((b) => label(t.blockers, b)).join("; ")}</div>}
                  </td>
                  <td>
                    <div className="row-actions">
                      {!c.verified_by_counsel && c.rule_mode && (
                        <button type="button" className="secondary" onClick={() => open({ kind: "counsel", code: c.code, name: c.name })}>{t.recordReview}</button>
                      )}
                      {c.can_enable && <button type="button" onClick={() => open({ kind: "enable", code: c.code, name: c.name })}>{t.enable}</button>}
                      {c.enabled && <button type="button" className="danger" onClick={() => open({ kind: "disable", code: c.code, name: c.name })}>{t.disable}</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {inline((f) => f.kind === "counsel" || f.kind === "enable" || f.kind === "disable")}
        {form && ["counsel", "enable", "disable"].includes(form.kind) && error && <p role="alert" className="error">{error}</p>}
      </section>

      <section aria-labelledby="tax-h">
        <h2 id="tax-h" style={{ marginTop: 32 }}>{t.taxTitle}</h2>
        <p className="muted" style={{ maxWidth: "70ch" }}>{t.taxIntro}</p>
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">{t.code}</th><th scope="col">{t.rate}</th><th scope="col">{t.wording}</th>
              <th scope="col">{t.accountant}</th></tr></thead>
            <tbody>
              {tax.map((r) => (
                <tr key={r.code}>
                  <td>{r.code}</td>
                  <td>{(r.rate_bps / 100).toFixed(r.rate_bps % 100 ? 2 : 0)}%</td>
                  <td>{r.invoice_wording ?? "–"}</td>
                  <td>
                    {r.verified_by_accountant && r.last_reviewed ? fill(t.reviewed, { date: day(r.last_reviewed) }) : (
                      <button type="button" className="secondary" onClick={() => open({ kind: "accountant", code: r.code })}>{t.recordAccountant}</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {inline((f) => f.kind === "accountant")}
        {form?.kind === "accountant" && error && <p role="alert" className="error">{error}</p>}
      </section>

      {adequacy.length > 0 && (
        <section aria-labelledby="adeq-h">
          <h2 id="adeq-h" style={{ marginTop: 32 }}>{t.adequacyTitle}</h2>
          <p className="muted" style={{ maxWidth: "70ch" }}>{t.adequacyIntro}</p>
          <ul className="reasons">
            {adequacy.map((a) => (
              <li key={a.key} className={a.warning ? "error" : undefined}>
                {a.key}: {a.valid_until && a.days_remaining !== null ? fill(t.validUntil, { date: day(a.valid_until), days: a.days_remaining }) : t.unknownDate}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
