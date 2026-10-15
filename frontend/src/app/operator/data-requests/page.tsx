"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import { Countdown, closed } from "@/components/Countdown";
import type { DataRequest } from "@/lib/types";

const t = getMessages().dataRequests;
const typeLabel = (r: string) => (t.types as Record<string, string>)[r] ?? r;
export default function DataRequestsPage() {
  const [rows, setRows] = useState<DataRequest[] | null>(null);
  const [open, setOpen] = useState<{ id: string; action: "status" | "erase" } | null>(null);
  const [note, setNote] = useState("");
  const [nextStatus, setNextStatus] = useState("in_progress");
  const [totp, setTotp] = useState("");
  const [verified, setVerified] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<DataRequest[]>("/api/v1/data-protection-requests").then(setRows).catch(() => setError(getMessages().operator.loadError));
  }, []);
  useEffect(load, [load]);

  function start(id: string, action: "status" | "erase", s = "in_progress") {
    setOpen({ id, action }); setNote(""); setTotp(""); setVerified(false); setNextStatus(s); setError(null);
  }

  async function submit() {
    if (!open) return;
    setBusy(true); setError(null);
    try {
      if (open.action === "erase") {
        await api(`/api/v1/data-protection-requests/${open.id}/complete-erasure`, {
          method: "POST", body: JSON.stringify({ totp_code: totp.trim(), identity_verified: verified }),
        });
        setStatus(t.erased);
      } else {
        await api(`/api/v1/data-protection-requests/${open.id}/status`, {
          method: "POST", body: JSON.stringify({ status: nextStatus, note: note.trim() }),
        });
        setStatus(t.updated);
      }
      setOpen(null); load();
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  if (!rows) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  const current = open ? rows.find((r) => r.id === open.id) : undefined;
  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      {status && <p className="status" role="status">{status}</p>}
      {rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
          <table>
            <thead><tr><th scope="col">{t.deadline}</th><th scope="col">{t.type}</th><th scope="col">{t.company}</th>
              <th scope="col">{t.received}</th><th scope="col">{t.regime}</th><th scope="col">{t.status}</th><th scope="col"><span className="skip">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id}>
                  <td><Countdown d={d} /><br /><span className="muted">{new Date(d.deadline_at).toLocaleDateString("en-GB")}</span></td>
                  <td><strong>{typeLabel(d.request_type)}</strong></td>
                  <td>{d.company ?? "–"}</td>
                  <td>{new Date(d.received_at).toLocaleDateString("en-GB")}</td>
                  <td>{d.regime.replace("_", " ").toUpperCase()}</td>
                  <td>{d.status.replaceAll("_", " ")}</td>
                  <td>
                    {!closed(d) && (
                      <div className="row-actions">
                        {d.status === "open" && <button type="button" className="secondary" onClick={() => start(d.id, "status", "in_progress")}>{t.markInProgress}</button>}
                        {d.request_type === "erasure"
                          ? <button type="button" className="danger" onClick={() => start(d.id, "erase")}>{t.erase}</button>
                          : <button type="button" className="secondary" onClick={() => start(d.id, "status", "completed")}>{t.markCompleted}</button>}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && current && (
        <div className="panel" role="dialog" aria-labelledby="dpr-title">
          <h2 id="dpr-title">{open.action === "erase" ? t.eraseTitle : `${typeLabel(current.request_type)}, ${current.company ?? "–"}`}</h2>
          {current.response_draft && (
            <details open={open.action === "status"}>
              <summary>{t.draft}</summary>
              <p className="brief">{current.response_draft}</p>
            </details>
          )}
          {open.action === "erase" ? (
            <>
              <p>{t.eraseWarning}</p>
              <label className="check"><input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />{t.identity}</label>
              <label htmlFor="totp">{t.totp}</label>
              <input id="totp" inputMode="numeric" autoComplete="one-time-code" value={totp} onChange={(e) => setTotp(e.target.value)} style={{ maxWidth: "12rem" }} />
            </>
          ) : (
            <>
              <label htmlFor="note">{t.note}</label>
              <textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} style={{ minHeight: "4rem", fontFamily: "var(--sans)" }} />
            </>
          )}
          {error && <p role="alert" className="error">{error}</p>}
          <div className="actionbar" style={{ position: "static", border: 0 }}>
            {open.action === "erase"
              ? <button type="button" className="danger" disabled={busy || !verified || totp.trim().length < 6} onClick={submit}>{t.eraseConfirm}</button>
              : <button type="button" disabled={busy || note.trim().length < 3} onClick={submit}>
                  {nextStatus === "completed" ? t.markCompleted : t.markInProgress}</button>}
            <button type="button" className="secondary" onClick={() => setOpen(null)}>{t.cancel}</button>
          </div>
        </div>
      )}
    </>
  );
}
