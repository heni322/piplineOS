"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { day } from "@/lib/format";
import { getMessages } from "@/lib/i18n";

const t = getMessages().pilotRequests;
const statuses = t.statuses as Record<string, string>;
type Req = { id: string; company: string; name: string; email: string; message: string | null; status: string; created_at: string };

/** Inbound pilot requests from the landing page (opt-in contacts). */
export default function PilotRequestsPage() {
  const [rows, setRows] = useState<Req[] | null>(null);
  const [error, setError] = useState(false);
  const load = useCallback(() => { api<Req[]>("/api/v1/pilot-requests").then(setRows).catch(() => setError(true)); }, []);
  useEffect(load, [load]);
  const mark = (id: string, status: string) =>
    api(`/api/v1/pilot-requests/${id}/status`, { method: "POST", body: JSON.stringify({ status }) }).then(load).catch(() => setError(true));

  if (error) return <p role="alert" className="error">{getMessages().operator.loadError}</p>;
  if (!rows) return <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      {rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
          <table>
            <thead><tr><th scope="col">{t.company}</th><th scope="col">{t.person}</th><th scope="col">{t.message}</th>
              <th scope="col">{t.received}</th><th scope="col">{t.status}</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <th scope="row">{r.company}</th>
                  <td>{r.name}<span className="hint" style={{ display: "block" }}><a href={`mailto:${r.email}`}>{r.email}</a></span></td>
                  <td>{r.message ?? "–"}</td>
                  <td>{day(r.created_at)}</td>
                  <td>{statuses[r.status]}
                    <div className="row-actions">{Object.keys(statuses).filter((s) => s !== r.status).map((s) => (
                      <button key={s} type="button" className="secondary" onClick={() => mark(r.id, s)}>{t.mark.replace("{s}", statuses[s] ?? s)}</button>
                    ))}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
