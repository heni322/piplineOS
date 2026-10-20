"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { getMessages, LOCALE } from "@/lib/i18n";

const t = getMessages().logs;
type Row = { decided_at: string; lead_id: string; company: string; prospect: string | null; stage: string; outcome: string;
  reason_codes: string[]; rules_applied: Record<string, string> };

export default function ComplianceLog() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { api<Row[]>("/api/v1/compliance/log").then(setRows).catch(() => setError(true)); }, []);
  if (error) return <p role="alert" className="error">{getMessages().operator.loadError}</p>;
  if (!rows) return <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.compliance}</h1>
      {rows.length === 0 ? <p className="muted">{t.emptyCompliance}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.compliance}>
          <table>
            <thead><tr><th scope="col">{t.when}</th><th scope="col">{t.company}</th><th scope="col">{t.prospect}</th>
              <th scope="col">{t.stage}</th><th scope="col">{t.outcome}</th><th scope="col">{t.reasons}</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.lead_id}-${i}`}>
                  <td>{new Date(r.decided_at).toLocaleString(LOCALE)}</td><td>{r.company}</td><td>{r.prospect ?? "–"}</td>
                  <td>{r.stage}</td><td className={`outcome-${r.outcome}`}>{r.outcome.replaceAll("_", " ")}</td>
                  <td>{r.reason_codes.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
