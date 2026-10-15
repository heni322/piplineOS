"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Escalation } from "@/lib/types";

const t = getMessages().escalations;
const reasonLabel = (r: string) => (t.reasons as Record<string, string>)[r] ?? r.replaceAll("_", " ");
const labels = t.detailLabels as Record<string, string>;
/** Human-readable context: internal ids are left out, values are shown in plain words. */
function describe(detail: Record<string, unknown>): string {
  return Object.entries(detail)
    .filter(([k]) => k in labels)
    .map(([k, v]) => {
      const value = typeof v === "boolean" ? (v ? t.yes : t.no)
        : Array.isArray(v) ? v.map((x) => reasonLabel(String(x))).join(", ") : String(v).replaceAll("_", " ");
      return `${labels[k]}: ${value}`;
    })
    .join("; ");
}

export default function EscalationsPage() {
  const [rows, setRows] = useState<Escalation[] | null>(null);
  const [showResolved, setShowResolved] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [resolution, setResolution] = useState("");
  const [target, setTarget] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const resolutionRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (open) resolutionRef.current?.focus(); }, [open]);

  const load = useCallback(() => {
    api<Escalation[]>(`/api/v1/escalations?status=${showResolved ? "all" : "open"}`)
      .then(setRows).catch(() => setError(getMessages().operator.loadError));
  }, [showResolved]);
  useEffect(load, [load]);

  async function resolve(id: string) {
    setBusy(true); setError(null);
    try {
      await api(`/api/v1/escalations/${id}/resolve`, {
        method: "POST", body: JSON.stringify({ resolution: resolution.trim(), target_state: target || null }),
      });
      setStatus(t.resolved); setOpen(null); setResolution(""); setTarget(""); load();
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  if (!rows) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.title}</h1>
      <label className="check"><input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />{t.showResolved}</label>
      {status && <p className="status" role="status">{status}</p>}
      {rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
          <table>
            <thead><tr><th scope="col">{t.reason}</th><th scope="col">{t.company}</th><th scope="col">{t.when}</th>
              <th scope="col">{t.leadState}</th><th scope="col">{t.details}</th><th scope="col"><span className="skip">{t.resolve}</span></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><strong>{reasonLabel(r.reason)}</strong></td>
                  <td>{r.company}</td>
                  <td>{new Date(r.created_at).toLocaleString("en-GB")}</td>
                  <td>{r.lead_state.replaceAll("_", " ")}</td>
                  <td className="muted">{describe(r.detail) || "–"}</td>
                  <td>
                    {r.status !== "resolved" && open !== r.id && (
                      <button type="button" className="secondary" onClick={() => { setOpen(r.id); setResolution(""); setTarget(""); }}>{t.resolve}</button>
                    )}
                    {r.status === "resolved" && <span className="muted">{r.status}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && (() => {
        const r = rows.find((x) => x.id === open);
        if (!r) return null;
        return (
          <div className="panel" role="dialog" aria-labelledby="resolve-title">
            <h2 id="resolve-title">{t.resolve}: {reasonLabel(r.reason)}, {r.company}</h2>
            <label htmlFor="resolution">{t.resolution}</label>
            <textarea id="resolution" value={resolution} onChange={(e) => setResolution(e.target.value)} ref={resolutionRef} style={{ minHeight: "4rem", fontFamily: "var(--sans)" }} />
            {r.allowed_targets.length > 0 && (
              <>
                <label htmlFor="target">{t.target}</label>
                <select id="target" value={target} onChange={(e) => setTarget(e.target.value)}>
                  <option value="">{t.keepState}</option>
                  {r.allowed_targets.map((s) => <option key={s} value={s}>{s.replaceAll("_", " ")}</option>)}
                </select>
              </>
            )}
            {error && <p role="alert" className="error">{error}</p>}
            <div className="actionbar" style={{ position: "static", border: 0 }}>
              <button type="button" disabled={busy || resolution.trim().length < 3} onClick={() => resolve(r.id)}>{t.resolve}</button>
              <button type="button" className="secondary" onClick={() => setOpen(null)}>{t.cancel}</button>
            </div>
          </div>
        );
      })()}
    </>
  );
}
