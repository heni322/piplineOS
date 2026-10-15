"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Campaign, Client } from "@/lib/types";

const t = getMessages().campaigns;
type Edit = { id: string; kind: "pause" | "limit"; value: string } | null;

/** Pilot Mode and campaign emergency stop. A pause holds queued emails (nothing is cancelled); the pilot limit caps
 * how many NEW people a campaign emails. Every change is audited by the API. */
export default function CampaignsPage() {
  const [rows, setRows] = useState<Campaign[] | null>(null);
  const [edit, setEdit] = useState<Edit>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [clients, setClients] = useState<Client[]>([]);
  const [draft, setDraft] = useState({ tenant: "", name: "" });

  const load = useCallback(() => {
    api<Campaign[]>("/api/v1/campaigns").then(setRows).catch(() => setError(getMessages().operator.loadError));
    api<Client[]>("/api/v1/clients").then((cs) => {
      const ready = cs.filter((c) => c.checklist.some((i) => i.key === "config" && i.done));
      setClients(ready); setDraft((d) => ({ ...d, tenant: d.tenant || ready[0]?.id || "" }));
    }).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  async function act(path: string, init: RequestInit, ok: string) {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await api<Campaign>(path, init);
      setRows((cur) => (cur ?? []).map((x) => (x.id === r.id ? r : x)));
      setEdit(null); setNotice(ok);
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  async function create() {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await api<Campaign>(`/api/v1/clients/${draft.tenant}/campaigns`, { method: "POST", body: JSON.stringify({ name: draft.name }) });
      setRows((cur) => [...(cur ?? []), r]); setDraft({ ...draft, name: "" }); setNotice(t.created);
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  const limitValue = edit?.kind === "limit" ? edit.value.trim() : "";
  const limitValid = limitValue === "" || (/^\d+$/.test(limitValue) && +limitValue >= 1 && +limitValue <= 100000);

  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      {notice && <p className="status" role="status">{notice}</p>}
      <section className="panel" aria-labelledby="new-camp" style={{ maxWidth: 640, marginBottom: 20 }}>
        <h2 id="new-camp">{t.new}</h2>
        {clients.length === 0 ? <p className="muted">{t.noActiveClients}</p> : (
          <>
            <label htmlFor="nc-client">{t.newClient}</label>
            <select id="nc-client" value={draft.tenant} onChange={(e) => setDraft({ ...draft, tenant: e.target.value })}>
              {clients.map((c) => <option key={c.id} value={c.id}>{c.legal_name}</option>)}
            </select>
            <label htmlFor="nc-name">{t.newName}</label>
            <input id="nc-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button type="button" disabled={busy || !draft.tenant || draft.name.trim().length < 3} onClick={create}>{t.create}</button>
            </div>
          </>
        )}
      </section>
      {error && <p role="alert" className="error">{error}</p>}
      {!rows ? <p className="muted" aria-busy="true">{getMessages().operator.loading}</p> : rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
          <table>
            <thead><tr><th scope="col">{t.campaign}</th><th scope="col">{t.client}</th><th scope="col">{t.status}</th>
              <th scope="col">{t.dailyCap}</th><th scope="col">{t.pilot}</th><th scope="col">{t.actions}</th></tr></thead>
            <tbody>
              {rows.map((c) => {
                const reached = c.pilot_max_contacts !== null && c.pilot_contacts_used >= c.pilot_max_contacts;
                const paused = c.status === "paused" || c.status === "auto_paused";
                return (
                  <Fragment key={c.id}>
                    <tr>
                      <th scope="row">{c.name}</th>
                      <td>{c.tenant_name}</td>
                      <td>{c.status.replaceAll("_", " ")}{c.paused_reason && <span className="hint" style={{ display: "block" }}>{c.paused_reason}</span>}</td>
                      <td>{c.daily_cap}</td>
                      <td>
                        {c.pilot_max_contacts === null ? `${c.pilot_contacts_used} (${t.noLimit})`
                          : t.of.replace("{u}", String(c.pilot_contacts_used)).replace("{l}", String(c.pilot_max_contacts))}
                        {reached && <span className="hint outcome-block" style={{ display: "block" }}>{t.reached}</span>}
                      </td>
                      <td>
                        <div className="row-actions">
                          {c.status === "draft" && <button type="button" disabled={busy}
                            onClick={() => act(`/api/v1/campaigns/${c.id}/start`, { method: "POST" }, t.started)}>{t.start}</button>}
                          {c.status === "active" && <button type="button" className="danger" disabled={busy}
                            onClick={() => setEdit({ id: c.id, kind: "pause", value: "" })}>{t.pause}</button>}
                          {paused && <button type="button" disabled={busy}
                            onClick={() => act(`/api/v1/campaigns/${c.id}/resume`, { method: "POST" }, t.resumedOk)}>{t.resume}</button>}
                          <button type="button" className="secondary" disabled={busy}
                            onClick={() => setEdit({ id: c.id, kind: "limit", value: c.pilot_max_contacts === null ? "" : String(c.pilot_max_contacts) })}>{t.editLimit}</button>
                        </div>
                      </td>
                    </tr>
                    {edit?.id === c.id && (
                      <tr><td colSpan={6}>
                        <label htmlFor={`e-${c.id}`}>{edit.kind === "pause" ? t.pauseReason : t.limit}</label>
                        <input id={`e-${c.id}`} inputMode={edit.kind === "limit" ? "numeric" : undefined} value={edit.value} style={{ maxWidth: 360 }}
                          onChange={(e) => setEdit({ ...edit, value: e.target.value })} />
                        {edit.kind === "limit" && <p className="hint">{t.limitHint}</p>}
                        <div className="row-actions" style={{ marginTop: 10 }}>
                          {edit.kind === "pause" ? (
                            <button type="button" className="danger" disabled={busy || edit.value.trim().length < 3}
                              onClick={() => act(`/api/v1/campaigns/${c.id}/pause`, { method: "POST", body: JSON.stringify({ reason: edit.value }) }, t.pausedOk)}>{t.confirmPause}</button>
                          ) : (
                            <button type="button" disabled={busy || !limitValid}
                              onClick={() => act(`/api/v1/campaigns/${c.id}/pilot-limit`, { method: "PUT",
                                body: JSON.stringify({ max_contacts: limitValue === "" ? null : Number(limitValue) }) }, t.saved)}>{t.save}</button>
                          )}
                          <button type="button" className="secondary" onClick={() => setEdit(null)}>{t.cancel}</button>
                        </div>
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
