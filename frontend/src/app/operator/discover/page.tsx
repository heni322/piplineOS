"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { day } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Client, CompanySearch, CompanySearches, FoundCompany, Targeting } from "@/lib/types";

const t = getMessages().discover;
const fill = (s: string, v: Record<string, string | number>) =>
  Object.entries(v).reduce((acc, [k, x]) => acc.replaceAll(`{${k}}`, String(x)), s);
const pending = (s: CompanySearch) => s.status === "queued" || s.status === "running";

/** Find companies with Google Places (paid, gated), then add the confirmed ones to a client's watchlist. */
export default function DiscoverPage() {
  const [clients, setClients] = useState<Client[] | null>(null);
  const [tenant, setTenant] = useState("");
  const [data, setData] = useState<CompanySearches | null>(null);
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const [form, setForm] = useState({ query: "", country: "GB" as "GB" | "FR", pages: 1 });
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Client[]>("/api/v1/clients").then((cs) => {
      const ready = cs.filter((c) => c.checklist.some((i) => i.key === "config" && i.done));
      setClients(ready); setTenant((cur) => cur || ready[0]?.id || "");
    }).catch(() => setError(getMessages().operator.loadError));
  }, []);

  const load = useCallback(() => {
    if (!tenant) return;
    api<CompanySearches>(`/api/v1/clients/${tenant}/company-searches`).then(setData)
      .catch(() => setError(getMessages().operator.loadError));
    api<Targeting>(`/api/v1/clients/${tenant}/targeting`)
      .then((tg) => setWatched(new Set(tg.watchlist.map((w) => w.company_number).filter((n): n is string => !!n))))
      .catch(() => setWatched(new Set()));
  }, [tenant]);
  useEffect(() => { setData(null); setPicked({}); load(); }, [load]);

  // while a search is waiting or running, refresh every 5 seconds
  useEffect(() => {
    if (!data?.searches.some(pending)) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [data, load]);

  async function search(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setNotice(null);
    try {
      await api(`/api/v1/clients/${tenant}/company-searches`, {
        method: "POST", body: JSON.stringify({ ...form, query: form.query.trim() }),
      });
      setNotice(t.started); load();
    } catch (err) {
      setError((err as Problem).detail ?? (err as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  async function addSelected(found: FoundCompany[]) {
    setBusy(true); setError(null); setNotice(null);
    try {
      const tg = await api<Targeting>(`/api/v1/clients/${tenant}/targeting`);
      const have = new Set(tg.watchlist.map((w) => w.company_number));
      const add = found.filter((f) => picked[f.company_number] && !have.has(f.company_number));
      if (add.length === 0) { setNotice(t.nothingNew); return; }
      const r = await api<{ version: number }>(`/api/v1/clients/${tenant}/targeting`, {
        method: "PUT",
        body: JSON.stringify({
          industry_keywords: tg.industry_keywords, exclude_keywords: tg.exclude_keywords,
          hiring_keywords: tg.hiring_keywords, sic_codes: tg.sic_codes, countries: tg.countries,
          watchlist: [...tg.watchlist, ...add.map((f) => ({
            name: f.name, company_number: f.company_number, domain: f.domain, country: f.country, job_board: null,
          }))],
          config_version: tg.config_version,
        }),
      });
      setNotice(fill(t.added, { n: add.length, v: r.version })); setPicked({}); load();
    } catch (err) {
      setError((err as Problem).detail ?? (err as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  if (!clients) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  const setup = data?.setup;
  const setupNote = !setup ? null : setup.free_only ? t.setupFreeOnly : !setup.key_set ? t.setupNoKey
    : setup.adapter_mode === "live" && setup.global_go_live ? t.setupLive : t.setupDryRun;

  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      {clients.length === 0 ? <p className="muted">{t.noClients}</p> : (
        <>
          <form onSubmit={search} className="panel" aria-labelledby="discover-form">
            <h2 id="discover-form" className="skip">{t.search}</h2>
            {setupNote && <p className="status" role="note">{setupNote}</p>}
            {setup && <p className="muted">{fill(t.usage, { n: setup.calls_this_month, cap: setup.monthly_cap })}</p>}
            <label>{t.client}
              <select value={tenant} onChange={(e) => setTenant(e.target.value)}>
                {clients.map((c) => <option key={c.id} value={c.id}>{c.legal_name}</option>)}
              </select>
            </label>
            <label>{t.query}
              <input value={form.query} required minLength={3} maxLength={200} aria-describedby="query-hint"
                onChange={(e) => setForm({ ...form, query: e.target.value })} />
              <span id="query-hint" className="muted">{t.queryHint}</span>
            </label>
            <label>{t.country}
              <select value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value as "GB" | "FR" })}>
                <option value="GB">{t.countries.GB}</option>
                <option value="FR">{t.countries.FR}</option>
              </select>
            </label>
            <label>{t.pages}
              <select value={form.pages} onChange={(e) => setForm({ ...form, pages: Number(e.target.value) })}>
                {[1, 2, 3].map((p) => (
                  <option key={p} value={p}>{fill(p === 1 ? t.pagesOption : t.pagesOptionMany, { n: p * 20, p })}</option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={busy || !tenant || form.query.trim().length < 3}>{t.search}</button>
          </form>
          {notice && <p className="status" role="status">{notice}</p>}
          {error && <p role="alert" className="error">{error}</p>}

          <h2>{t.history}</h2>
          {!data ? <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>
            : data.searches.length === 0 ? <p className="muted">{t.empty}</p>
            : data.searches.map((s) => (
              <section key={s.id} className="panel" aria-labelledby={`s-${s.id}`}>
                <h3 id={`s-${s.id}`}>{s.query} <span className="muted">({s.country})</span></h3>
                <p>
                  <span aria-busy={pending(s)}>{t.states[s.status]}</span>
                  <span className="muted"> · {t.when} {day(s.created_at)}</span>
                </p>
                {s.status === "failed" && s.error && <p className="error">{s.error}</p>}
                {s.status === "stuck" && <p className="error">{t.stuckHelp}</p>}
                {(s.status === "done" || s.status === "dry_run") && (
                  <p className="muted">{fill(t.summary, {
                    s: s.counts.searched ?? 0, w: s.counts.with_website ?? 0, f: s.found.length,
                  })}</p>
                )}
                {s.stopped_early && <p className="muted">{t.stoppedEarly}</p>}
                {s.status === "done" && s.found.length === 0 && <p className="muted">{t.noneFound}</p>}
                {s.found.length > 0 && (
                  <>
                    <div className="table-wrap" tabIndex={0} role="region" aria-label={s.query}>
                      <table>
                        <thead><tr>
                          <th scope="col"><span className="skip">{fill(t.select, { c: "" })}</span></th>
                          <th scope="col">{t.company}</th><th scope="col">{t.number}</th>
                          <th scope="col">{t.website}</th><th scope="col">{t.address}</th>
                        </tr></thead>
                        <tbody>
                          {s.found.map((f) => {
                            const already = watched.has(f.company_number);
                            return (
                              <tr key={f.company_number}>
                                <td>
                                  <input type="checkbox" aria-label={fill(t.select, { c: f.name })} disabled={already}
                                    checked={already || !!picked[f.company_number]}
                                    onChange={(e) => setPicked({ ...picked, [f.company_number]: e.target.checked })} />
                                </td>
                                <td>{f.name}{already && <span className="muted"> · {t.inWatchlist}</span>}</td>
                                <td>{f.company_number}</td>
                                <td><a href={`https://${f.domain}`} target="_blank" rel="noopener noreferrer">{f.domain}</a></td>
                                <td>{f.address ?? <span className="muted">{t.noAddress}</span>}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <button type="button" disabled={busy || !s.found.some((f) => picked[f.company_number] && !watched.has(f.company_number))}
                      onClick={() => addSelected(s.found)}>{t.add}</button>
                  </>
                )}
              </section>
            ))}
        </>
      )}
    </>
  );
}
