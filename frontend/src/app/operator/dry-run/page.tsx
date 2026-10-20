"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { getMessages, LOCALE } from "@/lib/i18n";

const t = getMessages().logs;
type Row = { created_at: string; effect_type: string; adapter: string; recipient: string | null; subject: string | null };

export default function DryRunLog() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { api<Row[]>("/api/v1/dry-run").then(setRows).catch(() => setError(true)); }, []);
  if (error) return <p role="alert" className="error">{getMessages().operator.loadError}</p>;
  if (!rows) return <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.dryRun}</h1>
      <p className="muted">{t.dryRunNote}</p>
      {rows.length === 0 ? <p className="muted">{t.emptyDryRun}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.dryRun}>
          <table>
            <thead><tr><th scope="col">{t.when}</th><th scope="col">{t.effect}</th><th scope="col">{t.recipient}</th>
              <th scope="col">{t.subject}</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}><td>{new Date(r.created_at).toLocaleString(LOCALE)}</td><td>{r.effect_type.replaceAll("_", " ")}</td>
                  <td>{r.recipient}</td><td>{r.subject}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
