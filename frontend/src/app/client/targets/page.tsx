"use client";

import { useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Watched } from "@/lib/types";

const t = getMessages().targets;
type Progress = { status: string; done: number; total: number };
const blank: Watched = { name: "", company_number: null, domain: null, country: null, job_board: null };

/** The client's own target accounts (companies only), watched for buying signals, and their setup progress. */
export default function TargetsPage() {
  const [rows, setRows] = useState<Watched[] | null>(null);
  const [admin, setAdmin] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [paste, setPaste] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ watchlist: Watched[] }>("/api/v1/account/watchlist").then((r) => setRows(r.watchlist)).catch(() => setRows([]));
    api<Progress>("/api/v1/account/setup-progress").then(setProgress).catch(() => undefined);
    api<{ role: string }>("/api/v1/auth/me").then((me) => setAdmin(me.role === "tenant_admin")).catch(() => undefined);
  }, []);

  const set = (i: number, patch: Partial<Watched>) => setRows((r) => (r ?? []).map((x, k) => (k === i ? { ...x, ...patch } : x)));
  function addPasted() {
    const parsed = paste.split("\n").map((l) => l.split(/\t|,/).map((c) => c.trim())).filter((c) => c[0])
      .map(([name = "", number, domain]) => ({ ...blank, name, company_number: number || null, domain: domain || null }));
    setRows((r) => [...(r ?? []), ...parsed]); setPaste("");
  }
  async function save() {
    setBusy(true); setMsg(null);
    try {
      const r = await api<{ watchlist: Watched[] }>("/api/v1/account/watchlist", { method: "PUT", body: JSON.stringify({ watchlist: rows }) });
      setRows(r.watchlist); setMsg({ ok: true, text: t.saved.replace("{n}", String(r.watchlist.length)) });
    } catch (e) { setMsg({ ok: false, text: (e as Problem).detail ?? (e as Problem).title }); } finally { setBusy(false); }
  }

  if (!rows) return <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.title}</h1>
      {progress && <p className="status" role="status">{progress.done === progress.total ? t.progressDone
        : t.progress.replace("{d}", String(progress.done)).replace("{t}", String(progress.total))}</p>}
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro} {t.limit.replace("{m}", "500")}</p>
      {msg && <p className={msg.ok ? "status" : "error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      {!admin && <p className="hint">{t.readOnly}</p>}
      {rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
          <table>
            <thead><tr><th scope="col">{t.name}</th><th scope="col">{t.number}</th><th scope="col">{t.domain}</th><th scope="col">{t.board}</th>
              {admin && <th scope="col"><span className="sr-only">{t.remove}</span></th>}</tr></thead>
            <tbody>
              {rows.map((w, i) => (
                <tr key={i}>
                  <td><input aria-label={`${t.name} ${i + 1}`} value={w.name} disabled={!admin} onChange={(e) => set(i, { name: e.target.value })} /></td>
                  <td><input aria-label={`${t.number} ${i + 1}`} value={w.company_number ?? ""} disabled={!admin} onChange={(e) => set(i, { company_number: e.target.value || null })} /></td>
                  <td><input aria-label={`${t.domain} ${i + 1}`} value={w.domain ?? ""} disabled={!admin} onChange={(e) => set(i, { domain: e.target.value || null })} /></td>
                  <td><input aria-label={`${t.board} ${i + 1}`} value={w.job_board ?? ""} disabled={!admin} onChange={(e) => set(i, { job_board: e.target.value || null })} /></td>
                  {admin && <td><button type="button" className="secondary" onClick={() => setRows(rows.filter((_, k) => k !== i))}>{t.remove}</button></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {admin && (
        <>
          <div className="row-actions" style={{ marginTop: 12 }}>
            <button type="button" className="secondary" onClick={() => setRows([...rows, { ...blank }])}>{t.add}</button>
            <button type="button" disabled={busy} onClick={save}>{t.save}</button>
          </div>
          <section className="panel" aria-labelledby="paste-h" style={{ marginTop: 20, maxWidth: 640 }}>
            <h2 id="paste-h">{t.paste}</h2>
            <label htmlFor="paste">{t.pasteHint}</label>
            <textarea id="paste" rows={4} value={paste} onChange={(e) => setPaste(e.target.value)} />
            <div className="row-actions" style={{ marginTop: 8 }}>
              <button type="button" className="secondary" disabled={!paste.trim()} onClick={addPasted}>{t.pasteAdd}</button>
            </div>
          </section>
        </>
      )}
    </>
  );
}
