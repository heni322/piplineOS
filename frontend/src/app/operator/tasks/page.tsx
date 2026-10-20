"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { webUrl } from "@/lib/format";
import { getMessages, LOCALE } from "@/lib/i18n";
import type { ContactTask, Unmasked } from "@/lib/types";

const t = getMessages().tasks;
const channelLabel = (c: string) => (t.channels as Record<string, string>)[c] ?? c.replaceAll("_", " ");

export default function TasksPage() {
  const [rows, setRows] = useState<ContactTask[] | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [revealing, setRevealing] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [closing, setClosing] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<"done" | "skipped">("done");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (closing) noteRef.current?.focus(); }, [closing]);

  const load = useCallback(() => {
    api<ContactTask[]>(`/api/v1/tasks?status=${showClosed ? "all" : "open"}`)
      .then(setRows).catch(() => setError(getMessages().operator.loadError));
  }, [showClosed]);
  useEffect(load, [load]);

  async function reveal(task: ContactTask) {
    setBusy(true); setError(null);
    try {
      const u = await api<Unmasked>(`/api/v1/leads/${task.lead_id}/unmask`, {
        method: "POST", body: JSON.stringify({ reason: reason.trim() }),
      });
      setRevealed((r) => ({ ...r, [task.id]: u.prospect_name ?? "–" }));
      setRevealing(null); setReason(""); setStatus(t.logged);
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  async function close(task: ContactTask) {
    setBusy(true); setError(null);
    try {
      await api(`/api/v1/tasks/${task.id}/complete`, {
        method: "POST", body: JSON.stringify({ outcome, note: note.trim() }),
      });
      setStatus(t.closed); setClosing(null); setNote(""); setOutcome("done"); load();
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  if (!rows) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  const current = rows.find((r) => r.id === closing);
  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      <label className="check"><input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />{t.showClosed}</label>
      {status && <p className="status" role="status">{status}</p>}
      {error && !closing && <p role="alert" className="error">{error}</p>}
      {rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
          <table>
            <thead><tr><th scope="col">{t.company}</th><th scope="col">{t.contact}</th><th scope="col">{t.trigger}</th>
              <th scope="col">{t.channel}</th><th scope="col">{t.created}</th><th scope="col"><span className="skip">{t.close}</span></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <strong>{r.company}</strong>
                    {r.company_number && (
                      <div className="muted">
                        <a href={`https://find-and-update.company-information.service.gov.uk/company/${r.company_number}`} target="_blank" rel="noopener noreferrer">
                          {r.company_number} ({t.register})
                        </a>
                      </div>
                    )}
                    {r.company_domain && (
                      <div className="muted"><a href={`https://${r.company_domain}`} target="_blank" rel="noopener noreferrer">{r.company_domain} ({t.website})</a></div>
                    )}
                  </td>
                  <td>
                    {revealed[r.id] ?? r.prospect ?? "–"}
                    {r.prospect_title && <div className="muted">{r.prospect_title}</div>}
                    {r.prospect && !revealed[r.id] && (revealing === r.id ? (
                      <div>
                        <label htmlFor={`reason-${r.id}`}>{t.reason}</label>
                        <input id={`reason-${r.id}`} value={reason} onChange={(e) => setReason(e.target.value)} />
                        <button type="button" disabled={busy || reason.trim().length < 3} onClick={() => reveal(r)}>{t.reveal}</button>
                      </div>
                    ) : (
                      <button type="button" className="secondary" onClick={() => { setRevealing(r.id); setReason(""); }}>{t.showName}</button>
                    ))}
                  </td>
                  <td>
                    {r.signal ?? "–"}
                    {webUrl(r.signal_url) && <div className="muted"><a href={webUrl(r.signal_url) ?? undefined} target="_blank" rel="noopener noreferrer">{t.source}</a></div>}
                  </td>
                  <td title={r.instructions}>
                    {channelLabel(r.channel)}
                    {/* a search opened in your own browser, by hand: nothing is collected from LinkedIn */}
                    <div className="muted">
                      <a href={`https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(r.company)}`}
                        target="_blank" rel="noopener noreferrer" aria-label={t.linkedinFor.replace("{c}", r.company)}>{t.linkedin}</a>
                    </div>
                  </td>
                  <td>{new Date(r.created_at).toLocaleString(LOCALE)}</td>
                  <td>
                    {r.status === "open" ? (
                      <button type="button" className="secondary" onClick={() => { setClosing(r.id); setNote(""); setOutcome("done"); setError(null); }}>{t.close}</button>
                    ) : (
                      <span className="muted">{r.status}{r.outcome_note ? `: ${r.outcome_note}` : ""}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {current && (
        <div className="panel" role="dialog" aria-labelledby="close-title">
          <h2 id="close-title">{t.closeTitle.replace("{c}", current.company)}</h2>
          <p className="muted" style={{ maxWidth: "70ch" }}>{current.instructions}</p>
          <fieldset>
            <legend>{t.outcome}</legend>
            <label className="check"><input type="radio" name="outcome" checked={outcome === "done"} onChange={() => setOutcome("done")} />{t.done}</label>
            <label className="check"><input type="radio" name="outcome" checked={outcome === "skipped"} onChange={() => setOutcome("skipped")} />{t.skipped}</label>
          </fieldset>
          <label htmlFor="note">{t.note}</label>
          <textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} ref={noteRef} maxLength={1000} style={{ minHeight: "4rem", fontFamily: "var(--sans)" }} />
          {error && <p role="alert" className="error">{error}</p>}
          <div className="actionbar" style={{ position: "static", border: 0 }}>
            <button type="button" disabled={busy || note.trim().length < 3} onClick={() => close(current)}>{t.close}</button>
            <button type="button" className="secondary" onClick={() => setClosing(null)}>{t.cancel}</button>
          </div>
        </div>
      )}
    </>
  );
}
