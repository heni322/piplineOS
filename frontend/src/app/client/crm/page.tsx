"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { day } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { CrmConnection, CrmStatus } from "@/lib/types";

const t = getMessages().crm;
const ORDER: CrmStatus[] = ["booked", "confirmed", "attended", "no_show", "cancelled"];
// HubSpot's standard sales pipeline, as a starting point the client can change
const HUBSPOT_DEFAULT: Record<CrmStatus, string> = {
  booked: "appointmentscheduled",
  confirmed: "qualifiedtobuy",
  attended: "presentationscheduled",
  no_show: "closedlost",
  cancelled: "closedlost",
};
const fill = (s: string, v: Record<string, string | number>) => Object.entries(v).reduce((a, [k, x]) => a.replaceAll(`{${k}}`, String(x)), s);

export default function ClientCrm() {
  const [c, setC] = useState<CrmConnection | null>(null);
  const [mode, setMode] = useState<"view" | "connect" | "disconnect">("view");
  const [token, setToken] = useState("");
  const [pipeline, setPipeline] = useState("default");
  const [stages, setStages] = useState<Record<CrmStatus, string>>(HUBSPOT_DEFAULT);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api<CrmConnection>("/api/v1/crm/connection").then(setC).catch(() => setError(getMessages().operator.loadError));
  }, []);
  useEffect(load, [load]);
  useEffect(() => { if (mode !== "view") first.current?.focus(); }, [mode]);

  function open(next: "connect" | "disconnect") {
    setNotice(null); setError(null); setToken(""); setReason("");
    if (next === "connect" && c?.stages) { setStages(c.stages); setPipeline(c.pipeline_id ?? "default"); }
    setMode(next);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const out = await api<CrmConnection>("/api/v1/crm/connection", {
        method: "PUT", body: JSON.stringify({ provider: "hubspot", token, pipeline_id: pipeline, stages }),
      });
      setC(out); setToken(""); setMode("view"); setNotice(t.saved);
    } catch (x) { setError((x as Problem).detail ?? (x as Problem).title); } finally { setBusy(false); }
  }

  async function disconnect(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const out = await api<CrmConnection>("/api/v1/crm/connection/disconnect", { method: "POST", body: JSON.stringify({ reason }) });
      setC(out); setMode("view"); setNotice(t.disconnectedNotice);
    } catch (x) { setError((x as Problem).detail ?? (x as Problem).title); } finally { setBusy(false); }
  }

  if (!c) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;

  const state = c.connected ? t.connected : c.status === "disconnected" ? t.disconnected : t.notConnected;
  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "68ch" }}>{t.intro}</p>
      {notice && <p className="status" role="status">{notice}</p>}

      <section className="sheet" aria-labelledby="crm-state" style={{ marginBottom: 18 }}>
        <h2 id="crm-state">{state}{c.connected && c.connected_at ? <span className="muted" style={{ fontWeight: 400 }}> {fill(t.since, { d: day(c.connected_at) })}</span> : null}</h2>
        {c.status && (
          <dl className="facts">
            <div>
              <dt>{t.pipeline}</dt><dd>{c.pipeline_id}</dd>
              <dt>{c.last_sync_at ? fill(t.lastSync, { d: day(c.last_sync_at) }) : t.neverSynced}</dt>
              <dd>{fill(t.counts, { synced: c.meetings_synced, waiting: c.meetings_waiting, failed: c.meetings_failed })}</dd>
            </div>
            <div>
              {ORDER.map((k) => (<Fragment key={k}><dt>{t.stages[k]}</dt><dd><code>{c.stages?.[k]}</code></dd></Fragment>))}
            </div>
          </dl>
        )}
        {c.status === "disconnected" && c.disconnected_reason && <p className="muted">{fill(t.reasonWas, { r: c.disconnected_reason })}</p>}
        <p className="hint" style={{ maxWidth: "68ch" }}>{t.safety}</p>
        {mode === "view" && (
          <p style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
            <button type="button" onClick={() => open("connect")}>{c.connected ? t.change : t.connectTitle}</button>
            {c.connected && <button type="button" className="secondary" onClick={() => open("disconnect")}>{t.disconnect}</button>}
          </p>
        )}
      </section>

      {mode === "connect" && (
        <form className="sheet" onSubmit={save} aria-labelledby="crm-connect">
          <h2 id="crm-connect">{c.connected ? t.replaceTitle : t.connectTitle}</h2>
          <label htmlFor="crm-token">{t.tokenLabel}</label>
          <input id="crm-token" ref={first} type="password" autoComplete="off" spellCheck={false} required minLength={10} maxLength={500}
            value={token} onChange={(e) => setToken(e.target.value)} aria-describedby="crm-token-hint" />
          <p id="crm-token-hint" className="hint">{t.tokenHint}</p>
          <label htmlFor="crm-pipeline">{t.pipelineLabel}</label>
          <input id="crm-pipeline" required maxLength={100} value={pipeline} onChange={(e) => setPipeline(e.target.value)} aria-describedby="crm-pipeline-hint" />
          <p id="crm-pipeline-hint" className="hint">{t.pipelineHint}</p>
          <fieldset style={{ border: 0, padding: 0, margin: "18px 0 0" }} aria-describedby="crm-stages-hint">
            <legend style={{ fontWeight: 600 }}>{t.stagesLegend}</legend>
            <p id="crm-stages-hint" className="hint">{t.stagesHint}</p>
            {ORDER.map((k) => (
              <div key={k}>
                <label htmlFor={`crm-stage-${k}`}>{t.stages[k]}</label>
                <input id={`crm-stage-${k}`} required maxLength={100} value={stages[k]}
                  onChange={(e) => setStages({ ...stages, [k]: e.target.value })} />
              </div>
            ))}
          </fieldset>
          {error && <p role="alert" className="error">{error}</p>}
          <p style={{ display: "flex", gap: 10, marginTop: 18 }}>
            <button type="submit" disabled={busy}>{t.save}</button>
            <button type="button" className="secondary" onClick={() => setMode("view")}>{t.cancel}</button>
          </p>
        </form>
      )}

      {mode === "disconnect" && (
        <form className="sheet" onSubmit={disconnect} aria-labelledby="crm-disc">
          <h2 id="crm-disc">{t.disconnectConfirm}</h2>
          <label htmlFor="crm-reason">{t.disconnectReason}</label>
          <input id="crm-reason" ref={first} required minLength={10} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          {error && <p role="alert" className="error">{error}</p>}
          <p style={{ display: "flex", gap: 10, marginTop: 18 }}>
            <button type="submit" disabled={busy}>{t.disconnectConfirm}</button>
            <button type="button" className="secondary" onClick={() => setMode("view")}>{t.cancel}</button>
          </p>
        </form>
      )}
    </>
  );
}
