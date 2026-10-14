"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Inbox, SendingDomain } from "@/lib/types";
import { day } from "@/lib/format";

const t = getMessages().inboxes;
const pct = (x: number) => `${(x * 100).toFixed(x * 100 < 1 ? 1 : 0)}%`;
const lbl = (map: Record<string, string>, k: string) => map[k] ?? k.replaceAll("_", " ");

function Rate({ count, rate, max, enough }: { count: number; rate: number | null; max: number; enough: boolean }) {
  return (
    <span className={`rate${rate !== null && enough && rate >= max ? " over" : ""}`}>
      {count}{rate !== null && ` (${pct(rate)})`}
      <span className="hint" style={{ display: "block" }}>{t.limit.replace("{pct}", pct(max))}</span>
    </span>
  );
}

function Domains() {
  const [rows, setRows] = useState<SendingDomain[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<SendingDomain[]>("/api/v1/sending-domains").then(setRows).catch(() => setError(getMessages().operator.loadError));
  }, []);
  useEffect(load, [load]);

  async function check(d: SendingDomain) {
    setBusy(d.id); setError(null); setNotice(null);
    try {
      const r = await api<SendingDomain>(`/api/v1/sending-domains/${d.id}/check`, { method: "POST" });
      setRows((cur) => (cur ?? []).map((x) => (x.id === r.id ? r : x)));
      setNotice(t.checkedNotice.replace("{d}", r.domain).replace("{n}", String(r.issues.length)));
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(null); }
  }

  const mark = (ok: boolean) => <span className={ok ? "outcome-allow" : "outcome-block"}>{ok ? t.pass : t.fail}</span>;
  return (
    <section aria-labelledby="domains-h" style={{ marginTop: 36 }}>
      <h2 id="domains-h">{t.domainsTitle}</h2>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.domainsIntro}</p>
      {notice && <p className="status" role="status">{notice}</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {!rows ? <p className="muted" aria-busy="true">{getMessages().operator.loading}</p> : rows.length === 0 ? <p className="muted">{t.noDomains}</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">{t.domain}</th><th scope="col">{t.client}</th><th scope="col">{t.spf}</th><th scope="col">{t.dkim}</th>
              <th scope="col">{t.dmarc}</th><th scope="col"><span className="sr-only">{t.check}</span></th></tr></thead>
            <tbody>
              {rows.map((d) => (
                <Fragment key={d.id}>
                  <tr>
                    <th scope="row">{d.domain}<span className="hint" style={{ display: "block" }}>{d.checked_at ? t.checked.replace("{d}", day(d.checked_at)) : t.neverChecked}</span></th>
                    <td>{d.tenant_name}</td><td>{mark(d.spf_ok)}</td><td>{mark(d.dkim_ok)}</td>
                    <td>{mark(d.dmarc_ok)}{d.dmarc_policy && <span className="hint" style={{ display: "block" }}>p={d.dmarc_policy}</span>}</td>
                    <td><button type="button" className="secondary" disabled={busy !== null} onClick={() => check(d)}>{busy === d.id ? t.checking : t.check}</button></td>
                  </tr>
                  {d.checked_at && (
                    <tr><td colSpan={6} className="muted">{d.issues.length === 0 ? t.allGood : (
                      <ul style={{ margin: 0, paddingLeft: 18 }}>{d.issues.map((i) => <li key={i}>{i}</li>)}</ul>)}</td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function InboxesPage() {
  const [rows, setRows] = useState<Inbox[] | null>(null);
  const [open, setOpen] = useState<{ id: string; action: "pause" | "resume" } | null>(null);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (open) reasonRef.current?.focus(); }, [open]);

  const load = useCallback(() => {
    api<Inbox[]>("/api/v1/inboxes").then(setRows).catch(() => setError(getMessages().operator.loadError));
  }, []);
  useEffect(load, [load]);

  async function submit(box: Inbox) {
    if (!open) return;
    setBusy(true); setError(null);
    try {
      await api(`/api/v1/inboxes/${box.id}/${open.action}`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) });
      setNotice((open.action === "pause" ? t.paused : t.resumed).replace("{a}", box.address)); setOpen(null); setReason(""); load();
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  if (!rows) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  const first = rows[0];
  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>
        {t.intro.replace("{days}", String(first?.window_days ?? 7)).replace("{min}", String(first?.min_sent ?? 50))}
      </p>
      {notice && <p className="status" role="status">{notice}</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">{t.inbox}</th><th scope="col">{t.client}</th><th scope="col">{t.status}</th><th scope="col">{t.today}</th>
              <th scope="col">{t.bounces}</th><th scope="col">{t.complaints}</th><th scope="col"><span className="sr-only">{t.pause}</span></th></tr></thead>
            <tbody>
              {rows.map((b) => {
                const enough = b.live_sent >= b.min_sent;
                const reasons = b.last_pause_reason.map((r) => lbl(t.reasons, r)).join("; ");
                return (
                  <Fragment key={b.id}>
                    <tr>
                      <td>{b.address}<span className="hint" style={{ display: "block" }}>{b.provider}</span></td>
                      <td>{b.tenant_name}</td>
                      <td>
                        <strong className={b.status === "paused" ? "error" : undefined} style={{ margin: 0 }}>{lbl(t.statuses, b.status)}</strong>
                        {b.status === "warming" && <span className="hint" style={{ display: "block" }}>{t.warmup.replace("{n}", String(b.warmup_day))}</span>}
                        {b.status === "paused" && reasons && <span className="hint" style={{ display: "block" }}>{t.pausedBecause.replace("{reason}", reasons)}</span>}
                      </td>
                      <td className="rate">{b.sent_today} / {b.daily_cap}</td>
                      <td><Rate count={b.hard_bounces} rate={b.hard_bounce_rate} max={b.max_hard_bounce_rate} enough={enough} /></td>
                      <td>
                        <Rate count={b.complaints} rate={b.complaint_rate} max={b.max_complaint_rate} enough={enough} />
                        {!enough && <span className="hint" style={{ display: "block" }}>{t.notEnough}</span>}
                      </td>
                      <td>
                        {b.status === "paused"
                          ? <button type="button" className="secondary" onClick={() => { setOpen({ id: b.id, action: "resume" }); setReason(""); }}>{t.resume}</button>
                          : <button type="button" className="danger" onClick={() => { setOpen({ id: b.id, action: "pause" }); setReason(""); }}>{t.pause}</button>}
                      </td>
                    </tr>
                    {open?.id === b.id && (
                      <tr><td colSpan={7}>
                        <div className="panel" role="dialog" aria-labelledby={`ib-${b.id}`} style={{ marginTop: 0 }}>
                          <h3 id={`ib-${b.id}`} style={{ marginTop: 0 }}>{(open.action === "pause" ? t.pauseTitle : t.resumeTitle).replace("{a}", b.address)}</h3>
                          <label htmlFor={`ibr-${b.id}`}>{open.action === "pause" ? t.pauseReason : t.resumeReason}</label>
                          <textarea id={`ibr-${b.id}`} ref={reasonRef} value={reason} onChange={(e) => setReason(e.target.value)}
                            style={{ minHeight: "4rem", fontFamily: "var(--sans)" }} />
                          <div className="row-actions" style={{ marginTop: 10 }}>
                            <button type="button" className={open.action === "pause" ? "danger" : undefined} disabled={busy || reason.trim().length < 10}
                              onClick={() => submit(b)}>{open.action === "pause" ? t.pause : t.resume}</button>
                            <button type="button" className="secondary" onClick={() => setOpen(null)}>{t.cancel}</button>
                          </div>
                        </div>
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <Domains />
    </>
  );
}
