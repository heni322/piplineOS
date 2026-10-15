"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Meeting } from "@/lib/types";

const t = getMessages().meetings;
const fmt = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/London" });

export default function MeetingsPage() {
  const [rows, setRows] = useState<Meeting[] | null>(null);
  const [brief, setBrief] = useState<{ id: string; text: string } | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Meeting[]>("/api/v1/meetings").then(setRows).catch(() => setError(getMessages().operator.loadError));
  }, []);
  useEffect(load, [load]);

  async function openBrief(id: string) {
    setError(null);
    try {
      const r = await api<{ brief: string }>(`/api/v1/meetings/${id}/brief`);
      setBrief({ id, text: r.brief.replace(/^Pre-meeting brief\s*\n+/, "") });
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    }
  }

  async function attendance(id: string, s: "attended" | "no_show") {
    setBusy(true); setError(null);
    try {
      await api(`/api/v1/meetings/${id}/attendance`, { method: "POST", body: JSON.stringify({ status: s }) });
      setStatus(t.recorded); load();
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  if (!rows) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  const now = Date.now();
  return (
    <>
      <h1>{t.title}</h1>
      {status && <p className="status" role="status">{status}</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
          <table>
            <thead><tr><th scope="col">{t.when}</th><th scope="col">{t.company}</th><th scope="col">{t.prospect}</th>
              <th scope="col">{t.status}</th><th scope="col">{t.reschedules}</th><th scope="col"><span className="skip">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((m) => {
                const over = new Date(m.ends_at).getTime() < now;
                const active = m.status === "booked" || m.status === "confirmed";
                return (
                  <tr key={m.id}>
                    <td>{fmt(m.starts_at)}</td>
                    <td>{m.company}</td>
                    <td>{m.prospect ?? "–"}</td>
                    <td>{m.status.replaceAll("_", " ")}{m.prospect_response ? ` (${m.prospect_response})` : ""}</td>
                    <td>{m.reschedule_count}</td>
                    <td>
                      <div className="row-actions">
                        <button type="button" className="secondary" onClick={() => openBrief(m.id)}>{t.brief}</button>
                        {active && over && (
                          <>
                            <button type="button" disabled={busy} onClick={() => attendance(m.id, "attended")}>{t.attended}</button>
                            <button type="button" className="danger" disabled={busy} onClick={() => attendance(m.id, "no_show")}>{t.noShow}</button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="hint">{t.briefLogged}</p>
      {brief && (
        <div className="panel" role="dialog" aria-labelledby="brief-title">
          <h2 id="brief-title">{t.briefTitle}</h2>
          <p className="brief">{brief.text}</p>
          <div className="actionbar" style={{ position: "static", border: 0 }}>
            <button type="button" className="secondary" onClick={() => setBrief(null)}>{t.close}</button>
          </div>
        </div>
      )}
    </>
  );
}
