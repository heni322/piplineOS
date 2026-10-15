"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Decision, Lead, LeadPage, Summary, Unmasked } from "@/lib/types";

const t = getMessages().leads;
const human = (s: string) => s.replaceAll("_", " ");
const when = (iso: string) => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

type Open = { id: string; view: "reason" | "details" | "decisions" };

export default function LeadsPage() {
  const [states, setStates] = useState<string[]>([]);
  const [state, setState] = useState("");
  const [rows, setRows] = useState<Lead[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [open, setOpen] = useState<Open | null>(null);
  const [reason, setReason] = useState("");
  const [revealed, setRevealed] = useState<Record<string, Unmasked>>({});
  const [decisions, setDecisions] = useState<Record<string, Decision[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reasonRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open?.view === "reason") reasonRef.current?.focus(); }, [open]);

  useEffect(() => {
    api<Summary>("/api/v1/pipeline/summary").then((s) => setStates(Object.keys(s.by_state).sort())).catch(() => undefined);
  }, []);

  const load = useCallback(async (more?: string | null) => {
    setError(null);
    const q = new URLSearchParams({ limit: "50" });
    if (state) q.set("state", state);
    if (more) q.set("cursor", more);
    try {
      const page = await api<LeadPage>(`/api/v1/leads?${q}`);
      setRows((cur) => (more && cur ? [...cur, ...page.items] : page.items));
      setCursor(page.next_cursor);
    } catch { setError(getMessages().operator.loadError); }
  }, [state]);
  useEffect(() => { load(); }, [load]);

  async function reveal(lead: Lead) {
    setBusy(true); setError(null);
    try {
      const u = await api<Unmasked>(`/api/v1/leads/${lead.id}/unmask`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) });
      setRevealed((r) => ({ ...r, [lead.id]: u })); setOpen({ id: lead.id, view: "details" }); setReason("");
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  async function showDecisions(lead: Lead) {
    if (open?.id === lead.id && open.view === "decisions") { setOpen(null); return; }
    setOpen({ id: lead.id, view: "decisions" });
    if (decisions[lead.id]) return;
    try {
      const d = await api<Decision[]>(`/api/v1/compliance/decisions?lead_id=${lead.id}&tenant_id=${lead.tenant_id}`);
      setDecisions((x) => ({ ...x, [lead.id]: d }));
    } catch { setError(getMessages().operator.loadError); }
  }

  function hide(id: string) {
    setRevealed((r) => Object.fromEntries(Object.entries(r).filter(([k]) => k !== id)));
    setOpen(null);
  }

  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      <div className="toolbar">
        <div>
          <label htmlFor="lead-state">{t.state}</label>
          <select id="lead-state" value={state} onChange={(e) => { setState(e.target.value); setOpen(null); }}>
            <option value="">{t.all}</option>
            {states.map((s) => <option key={s} value={s}>{human(s)}</option>)}
          </select>
        </div>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {!rows ? <p className="muted" aria-busy="true">{getMessages().operator.loading}</p> : rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
          <table>
            <thead>
              <tr><th scope="col">{t.company}</th><th scope="col">{t.prospect}</th><th scope="col">{t.email}</th><th scope="col">{t.jobTitle}</th>
                <th scope="col">{t.country}</th><th scope="col">{t.state}</th><th scope="col">{t.changed}</th><th scope="col"><span className="sr-only">{t.show}</span></th></tr>
            </thead>
            <tbody>
              {rows.map((l) => {
                const u = revealed[l.id];
                const isOpen = open?.id === l.id;
                return (
                  <Fragment key={l.id}>
                    <tr>
                      <td>{l.company_name ?? "–"}</td>
                      <td>{u ? u.prospect_name ?? "–" : l.prospect_name ?? "–"}</td>
                      <td>{u ? u.prospect_email ?? "–" : l.prospect_email ?? "–"}</td>
                      <td>{l.title ?? "–"}</td>
                      <td>{l.country_code ?? "–"}</td>
                      <td>{human(l.state)}</td>
                      <td>{when(l.state_changed_at)}</td>
                      <td>
                        <div className="row-actions">
                          {u ? <button type="button" className="secondary" onClick={() => hide(l.id)}>{t.hide}</button>
                            : l.prospect_name && <button type="button" className="secondary" onClick={() => { setOpen({ id: l.id, view: "reason" }); setReason(""); setError(null); }}>{t.show}</button>}
                          <button type="button" className="secondary" aria-expanded={isOpen && open?.view === "decisions"} onClick={() => showDecisions(l)}>{t.decisions}</button>
                        </div>
                      </td>
                    </tr>
                    {isOpen && open?.view === "reason" && (
                      <tr><td colSpan={8}>
                        <div className="panel" role="dialog" aria-labelledby={`show-${l.id}`} style={{ marginTop: 0 }}>
                          <h3 id={`show-${l.id}`} style={{ marginTop: 0 }}>{t.showTitle.replace("{c}", l.company_name ?? "")}</h3>
                          <label htmlFor={`reason-${l.id}`}>{t.reason}</label>
                          <input id={`reason-${l.id}`} ref={reasonRef} value={reason} onChange={(e) => setReason(e.target.value)} />
                          <div className="row-actions" style={{ marginTop: 10 }}>
                            <button type="button" disabled={busy || reason.trim().length < 3} onClick={() => reveal(l)}>{t.reveal}</button>
                            <button type="button" className="secondary" onClick={() => setOpen(null)}>{getMessages().governance.cancel}</button>
                          </div>
                        </div>
                      </td></tr>
                    )}
                    {isOpen && open?.view === "details" && u && (
                      <tr><td colSpan={8}>
                        <p className="hint" role="status" style={{ margin: 0 }}>
                          {t.logged}{u.profile_url && <> {t.profile}: <a href={u.profile_url} target="_blank" rel="noopener noreferrer">{u.profile_url}</a></>}
                        </p>
                      </td></tr>
                    )}
                    {isOpen && open?.view === "decisions" && (
                      <tr><td colSpan={8}>
                        {!decisions[l.id] ? <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>
                          : decisions[l.id]!.length === 0 ? <p className="muted">{t.noDecisions}</p> : (
                          <ul className="reasons">
                            {decisions[l.id]!.map((d) => (
                              <li key={d.id}>
                                {when(d.created_at)}, {human(d.stage)}: <span className={`outcome-${d.outcome}`}>{human(d.outcome)}</span>
                                {d.reason_codes.length > 0 && <span className="muted"> ({d.reason_codes.join(", ")})</span>}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {cursor && <p><button type="button" className="secondary" onClick={() => load(cursor)}>{t.more}</button></p>}
    </>
  );
}
