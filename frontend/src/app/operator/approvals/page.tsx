"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CitedLetter } from "@/components/CitedLetter";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { ApprovalItem, ApprovalPage } from "@/lib/types";

const t = getMessages().approvals;
type Mode = "view" | "unmask" | "edit" | "reject";

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export default function ApprovalsPage() {
  const [page, setPage] = useState<ApprovalPage | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [detail, setDetail] = useState<ApprovalItem | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const opened = useRef<number>(Date.now());
  const key = useRef<string>(newKey());
  const unmaskRef = useRef<HTMLInputElement>(null);
  const rejectRef = useRef<HTMLTextAreaElement>(null);
  // Move focus into a panel when it opens (keyboard and screen-reader users land in the field they need).
  useEffect(() => {
    if (mode === "unmask") unmaskRef.current?.focus();
    if (mode === "reject") rejectRef.current?.focus();
  }, [mode]);

  const load = useCallback(async () => {
    try {
      const p = await api<ApprovalPage>("/api/v1/approvals");
      setPage(p);
      setSelected((cur) => (cur && p.items.some((i) => i.message_id === cur) ? cur : p.items[0]?.message_id ?? null));
    } catch {
      setError(getMessages().operator.loadError);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const item = useMemo(() => page?.items.find((i) => i.message_id === selected) ?? null, [page, selected]);
  useEffect(() => {
    setMode("view"); setDetail(null); setReason(""); setError(null);
    opened.current = Date.now(); key.current = newKey();
  }, [selected]);

  const shown = detail ?? item;
  const numbering = useMemo(() => {
    const m = new Map<string, number>();
    shown?.claims.forEach((c) => { if (!m.has(c.source_url)) m.set(c.source_url, m.size + 1); });
    return m;
  }, [shown]);

  async function decide(decision: "approve" | "edit" | "reject") {
    if (!item) return;
    setBusy(true); setError(null);
    const edits = decision === "edit" && detail
      ? detail.sequence.filter((m) => drafts[m.message_id] !== undefined && drafts[m.message_id] !== m.body)
          .map((m) => ({ message_id: m.message_id, body: drafts[m.message_id] }))
      : [];
    try {
      await api(`/api/v1/approvals/${item.message_id}/decision`, {
        method: "POST",
        headers: { "Idempotency-Key": key.current },
        body: JSON.stringify({
          decision: decision === "edit" && edits.length === 0 ? "approve" : decision,
          edits, reason: decision === "reject" ? reason : null,
          review_seconds: Math.round((Date.now() - opened.current) / 1000),
        }),
      });
      setStatus(decision === "reject" ? (item.kind === "outreach" ? t.rejected : t.replyRejected) : t.approved);
      await load();
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  async function unmaskForEdit() {
    if (!item || reason.trim().length < 3) return;
    setBusy(true); setError(null);
    try {
      const d = await api<ApprovalItem>(
        `/api/v1/approvals/${item.message_id}?unmask_reason=${encodeURIComponent(reason.trim())}`);
      setDetail(d);
      setDrafts(Object.fromEntries(d.sequence.map((m) => [m.message_id, m.body])));
      setMode("edit"); setReason("");
    } catch (e) {
      setError((e as Problem).detail ?? (e as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  // Keyboard shortcuts while reviewing (ignored while typing in a field).
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      const tag = (ev.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || mode !== "view" || !item || busy || ev.metaKey || ev.ctrlKey) return;
      if (ev.key === "a") { ev.preventDefault(); decide("approve"); }
      if (ev.key === "e") { ev.preventDefault(); setMode("unmask"); }
      if (ev.key === "r") { ev.preventDefault(); setMode("reject"); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!page) return error ? <p role="alert" className="error">{error}</p> : <p className="muted" aria-busy="true">Loading…</p>;

  return (
    <>
      <h1>{t.title} <span className="muted" style={{ fontSize: "1rem", fontWeight: 400 }}>({page.total_pending})</span></h1>
      {status && <p className="status" role="status">{status}</p>}
      {page.items.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="desk">
          <nav aria-label={t.queueLabel}>
            <ul className="queue">
              {page.items.map((i) => (
                <li key={i.message_id}>
                  <button type="button" aria-current={i.message_id === selected} onClick={() => setSelected(i.message_id)}>
                    <span className="co">{i.company}</span>
                    <span className="meta">
                      {i.kind === "outreach" ? `${i.prospect_name}, ${i.prospect_title}, quality ${i.quality_score ?? "–"}`
                        : `${t.replyKind}: ${i.prospect_name}`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          {shown && (
            <article aria-labelledby="subject">
              <dl className="facts">
                <div>
                  <dt>{t.sources}</dt>
                  <dd>
                    <ol className="sources">
                      {[...numbering.entries()].map(([url, n]) => (
                        <li key={url} value={n}><a href={url} target="_blank" rel="noopener noreferrer">{new URL(url).host}</a></li>
                      ))}
                    </ol>
                  </dd>
                  <dt>{t.signal}</dt>
                  <dd>{shown.signal_summary} <span className="muted">({t.score.replace("{n}", String(shown.relevance_score ?? "–"))})</span></dd>
                </div>
                <div>
                  <dt>{t.compliance}</dt>
                  <dd>
                    <span className={`outcome-${shown.compliance?.outcome}`}>{shown.compliance?.outcome}</span>{" "}
                    <span className="muted">{shown.compliance?.reason_codes.join(", ")}; {Object.entries(shown.compliance?.rules_applied ?? {}).map(([c, r]) => `${c} ${r.split("@")[1]}`).join(", ")}</span>
                  </dd>
                  <dt>{t.quality}</dt>
                  <dd>{shown.quality_score}/10{shown.critique?.rewrites ? ` after ${shown.critique.rewrites} rewrite(s)` : ""}</dd>
                  {shown.masked && <dd className="muted">{t.masked}</dd>}
                </div>
              </dl>
              {shown.prospect_reply && (
                <figure className="their-reply">
                  <figcaption>{t.theirReply}</figcaption>
                  <blockquote>{shown.prospect_reply}</blockquote>
                  <p className="hint">{t.theirReplyHint}</p>
                </figure>
              )}
              <div className="sheet">
                {mode === "edit" && detail ? (
                  detail.sequence.map((m) => (
                    <div key={m.message_id}>
                      <label htmlFor={`b-${m.message_id}`}>{m.step === 0 ? m.subject : t.followUp.replace("{n}", String(m.step))}</label>
                      <textarea id={`b-${m.message_id}`} value={drafts[m.message_id] ?? ""}
                                onChange={(e) => setDrafts({ ...drafts, [m.message_id]: e.target.value })} />
                    </div>
                  ))
                ) : (
                  <>
                    <h2 id="subject" className="subject">{shown.sequence[0]?.subject}</h2>
                    {shown.subject_options.length > 1 && (
                      <>
                        <p className="muted" style={{ margin: "0 0 2px", fontSize: ".85rem" }}>{t.subjectOptions}</p>
                        <ol className="alts">{shown.subject_options.slice(1).map((s) => <li key={s}>{s}</li>)}</ol>
                      </>
                    )}
                    <CitedLetter text={shown.sequence[0]?.body ?? ""} claims={shown.claims} numbering={numbering} />
                    {shown.sequence.slice(1).map((m) => (
                      <details key={m.message_id}>
                        <summary>{t.followUp.replace("{n}", String(m.step))}</summary>
                        <CitedLetter text={m.body} claims={shown.claims} numbering={numbering} />
                      </details>
                    ))}
                    <p className="footer-preview" aria-label={t.footer}>{shown.footer_preview}</p>
                  </>
                )}
              </div>


              {mode === "unmask" && (
                <div className="panel" role="dialog" aria-labelledby="unmask-title">
                  <h3 id="unmask-title" style={{ marginTop: 0 }}>{t.showForEdit}</h3>
                  <label htmlFor="unmask-reason">{t.unmaskReason}</label>
                  <input id="unmask-reason" ref={unmaskRef} value={reason} onChange={(e) => setReason(e.target.value)} aria-describedby="unmask-hint" />
                  <p id="unmask-hint" className="hint">{t.unmaskHint}</p>
                  <div className="actionbar" style={{ position: "static", border: 0 }}>
                    <button type="button" disabled={busy || reason.trim().length < 3} onClick={unmaskForEdit}>{t.showForEdit}</button>
                    <button type="button" className="secondary" onClick={() => setMode("view")}>{t.cancel}</button>
                  </div>
                </div>
              )}
              {mode === "reject" && (
                <div className="panel" role="dialog" aria-labelledby="reject-title">
                  <h3 id="reject-title" style={{ marginTop: 0 }}>{t.reject}</h3>
                  <label htmlFor="reject-reason">{t.rejectReason}</label>
                  <textarea id="reject-reason" ref={rejectRef} value={reason} onChange={(e) => setReason(e.target.value)} style={{ minHeight: "4rem" }} />
                  <div className="actionbar" style={{ position: "static", border: 0 }}>
                    <button type="button" className="danger" disabled={busy || reason.trim().length < 3} onClick={() => decide("reject")}>{item?.kind === "outreach" ? t.rejectConfirm : t.rejectReplyConfirm}</button>
                    <button type="button" className="secondary" onClick={() => setMode("view")}>{t.cancel}</button>
                  </div>
                </div>
              )}
              {error && <p role="alert" className="error">{error}</p>}

              {(mode === "view" || mode === "edit") && (
                <div className="actionbar">
                  {mode === "edit" ? (
                    <>
                      <button type="button" disabled={busy} onClick={() => decide("edit")}>{t.save}</button>
                      <button type="button" className="secondary" onClick={() => { setMode("view"); setDetail(null); }}>{t.cancel}</button>
                    </>
                  ) : (
                    <>
                      <button type="button" disabled={busy} onClick={() => decide("approve")}>{t.approve}</button>
                      <button type="button" className="secondary" onClick={() => setMode("unmask")}>{t.edit}</button>
                      <button type="button" className="danger" onClick={() => setMode("reject")}>{t.reject}</button>
                      <span className="hint">{t.shortcuts}</span>
                    </>
                  )}
                </div>
              )}
            </article>
          )}
        </div>
      )}
    </>
  );
}
