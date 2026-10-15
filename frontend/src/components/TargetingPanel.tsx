"use client";

import { useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Targeting, Watched } from "@/lib/types";

const t = getMessages().clients;
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
const empty: Watched = { name: "", company_number: null, domain: null, country: null, job_board: null };

/** The free sources' settings for one client: keywords, SIC codes, countries and the watchlist. */
export function TargetingPanel({ id, name, onClose, onSaved }: {
  id: string; name: string; onClose: () => void; onSaved: (message: string) => void;
}) {
  const [data, setData] = useState<Targeting | null>(null);
  const [text, setText] = useState({ industry: "", exclude: "", hiring: "", sic: "" });
  const [countries, setCountries] = useState<string[]>([]);
  const [watch, setWatch] = useState<Watched[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Targeting>(`/api/v1/clients/${id}/targeting`).then((d) => {
      setData(d);
      setText({ industry: d.industry_keywords.join("\n"), exclude: d.exclude_keywords.join("\n"),
        hiring: d.hiring_keywords.join("\n"), sic: d.sic_codes.join("\n") });
      setCountries(d.countries); setWatch(d.watchlist);
    }).catch((e) => setError((e as Problem).status === 409 ? t.noConfig : getMessages().operator.loadError));
  }, [id]);

  const setRow = (i: number, patch: Partial<Watched>) => setWatch((w) => w.map((r, k) => (k === i ? { ...r, ...patch } : r)));

  async function save() {
    setBusy(true); setError(null);
    try {
      const r = await api<{ version: number }>(`/api/v1/clients/${id}/targeting`, {
        method: "PUT",
        body: JSON.stringify({
          industry_keywords: lines(text.industry), exclude_keywords: lines(text.exclude),
          hiring_keywords: lines(text.hiring), sic_codes: lines(text.sic), countries,
          watchlist: watch.filter((w) => w.name.trim()), config_version: data?.config_version,
        }),
      });
      onSaved(t.targetingSaved.replace("{v}", String(r.version)));
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel targeting" role="dialog" aria-labelledby="targeting-title">
      <h2 id="targeting-title">{t.targetingTitle.replace("{c}", name)}</h2>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.targetingIntro}</p>
      {!data ? (error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>) : (
        <>
          <div className="targeting-grid">
            <div>
              <label htmlFor="tg-industry">{t.industryKeywords}</label>
              <textarea id="tg-industry" value={text.industry} onChange={(e) => setText({ ...text, industry: e.target.value })} aria-describedby="tg-industry-hint" />
              <p id="tg-industry-hint" className="hint">{t.industryHint}</p>
            </div>
            <div>
              <label htmlFor="tg-exclude">{t.excludeKeywords}</label>
              <textarea id="tg-exclude" value={text.exclude} onChange={(e) => setText({ ...text, exclude: e.target.value })} />
            </div>
            <div>
              <label htmlFor="tg-hiring">{t.hiringKeywords}</label>
              <textarea id="tg-hiring" value={text.hiring} onChange={(e) => setText({ ...text, hiring: e.target.value })} aria-describedby="tg-hiring-hint" />
              <p id="tg-hiring-hint" className="hint">{t.hiringHint}</p>
            </div>
            <div>
              <label htmlFor="tg-sic">{t.sicCodes}</label>
              <textarea id="tg-sic" value={text.sic} inputMode="numeric" onChange={(e) => setText({ ...text, sic: e.target.value })} aria-describedby="tg-sic-hint" />
              <p id="tg-sic-hint" className="hint">{t.sicHint}</p>
            </div>
          </div>

          <fieldset aria-describedby="tg-countries-hint">
            <legend>{t.countries}</legend>
            <div className="row-actions">
              {data.available_countries.map((c) => (
                <label key={c} className="check">
                  <input type="checkbox" checked={countries.includes(c)}
                    onChange={(e) => setCountries((x) => (e.target.checked ? [...x, c] : x.filter((y) => y !== c)))} />
                  {c}
                </label>
              ))}
            </div>
            <p id="tg-countries-hint" className="hint">{t.countriesHint}</p>
          </fieldset>

          <h3>{t.watchlist}</h3>
          <p className="hint">{t.watchlistHint}</p>
          {watch.length > 0 && (
            <div className="table-wrap" tabIndex={0} role="region" aria-label={t.watchlist}>
              <table>
                <thead><tr><th scope="col">{t.wName}</th><th scope="col">{t.wNumber}</th><th scope="col">{t.wBoard}</th>
                  <th scope="col">{t.wDomain}</th><th scope="col"><span className="skip">{t.remove}</span></th></tr></thead>
                <tbody>
                  {watch.map((w, i) => (
                    <tr key={i}>
                      <td><input aria-label={`${t.wName} ${i + 1}`} value={w.name} onChange={(e) => setRow(i, { name: e.target.value })} /></td>
                      <td><input aria-label={`${t.wNumber} ${i + 1}`} value={w.company_number ?? ""} maxLength={8} onChange={(e) => setRow(i, { company_number: e.target.value || null })} /></td>
                      <td><input aria-label={`${t.wBoard} ${i + 1}`} value={w.job_board ?? ""} onChange={(e) => setRow(i, { job_board: e.target.value || null })} /></td>
                      <td><input aria-label={`${t.wDomain} ${i + 1}`} value={w.domain ?? ""} onChange={(e) => setRow(i, { domain: e.target.value || null })} /></td>
                      <td><button type="button" className="secondary" aria-label={t.removeCompany.replace("{c}", w.name || String(i + 1))}
                        onClick={() => setWatch((x) => x.filter((_, k) => k !== i))}>{t.remove}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <button type="button" className="secondary" onClick={() => setWatch((x) => [...x, { ...empty }])}>{t.addCompany}</button>

          {error && <p role="alert" className="error">{error}</p>}
          <div className="actionbar" style={{ position: "static", border: 0 }}>
            <button type="button" disabled={busy || lines(text.industry).length === 0 || countries.length === 0} onClick={save}>{t.saveTargeting}</button>
            <button type="button" className="secondary" onClick={onClose}>{t.cancel}</button>
          </div>
        </>
      )}
    </div>
  );
}
