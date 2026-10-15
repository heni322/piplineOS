"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { day } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Eligibility } from "@/lib/types";

const t = getMessages().client;
const reasons = getMessages().billing.reasons as Record<string, string>;
type Kind = "attendance" | "qualification" | "other";

export default function ClientMeetings() {
  const [rows, setRows] = useState<Eligibility[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind>("attendance");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const firstRef = useRef<HTMLSelectElement>(null);
  useEffect(() => { if (open) firstRef.current?.focus(); }, [open]);

  const load = useCallback(() => {
    api<Eligibility[]>("/api/v1/billing/meetings").then(setRows).catch(() => setError(getMessages().operator.loadError));
  }, []);
  useEffect(load, [load]);

  async function send() {
    if (!open) return;
    setBusy(true); setError(null);
    try {
      await api("/api/v1/disputes", { method: "POST", body: JSON.stringify({ meeting_id: open, kind, reason: reason.trim() }) });
      setStatus(t.disputeSent); setOpen(null); setReason(""); load();
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  if (!rows) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.meetingsTitle}</h1>
      {status && <p className="status" role="status">{status}</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {rows.length === 0 ? <p className="muted">{t.emptyMeetings}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.meetingsTitle}>
          <table>
            <thead><tr><th scope="col">{getMessages().meetings.when}</th><th scope="col">{getMessages().meetings.company}</th>
              <th scope="col">{getMessages().meetings.status}</th><th scope="col">{getMessages().billing.title}</th><th scope="col"><span className="skip">{t.dispute}</span></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.meeting_id}>
                  <td>{day(r.starts_at)}</td>
                  <td>{r.company}</td>
                  <td>{r.status.replaceAll("_", " ")}</td>
                  <td>{r.billable ? <span className="outcome-allow">{t.billable}</span> : (
                    <><span className="muted">{t.notBillable}</span>
                      <ul className="reasons muted">{r.reasons.map((x) => <li key={x}>{reasons[x] ?? x}</li>)}</ul></>
                  )}</td>
                  <td>{r.status === "attended" && !r.reasons.includes("DISPUTE_OPEN") && !r.reasons.includes("DISPUTE_UPHELD") && (
                    <button type="button" className="secondary" onClick={() => { setOpen(r.meeting_id); setReason(""); }}>{t.dispute}</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && (
        <div className="panel" role="dialog" aria-labelledby="d-title" style={{ maxWidth: 620 }}>
          <h2 id="d-title">{t.disputeTitle}</h2>
          <label htmlFor="d-kind">{t.disputeKind}</label>
          <select id="d-kind" ref={firstRef} value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            {(Object.keys(t.kinds) as Kind[]).map((k) => <option key={k} value={k}>{t.kinds[k]}</option>)}
          </select>
          <label htmlFor="d-reason">{t.disputeReason}</label>
          <textarea id="d-reason" value={reason} onChange={(e) => setReason(e.target.value)} aria-describedby="d-hint" style={{ minHeight: "5rem", fontFamily: "var(--sans)" }} />
          <p id="d-hint" className="hint">{t.disputeHint}</p>
          <div className="actionbar" style={{ position: "static", border: 0 }}>
            <button type="button" disabled={busy || reason.trim().length < 10} onClick={send}>{t.dispute}</button>
            <button type="button" className="secondary" onClick={() => setOpen(null)}>{getMessages().billing.cancel}</button>
          </div>
        </div>
      )}
    </>
  );
}
