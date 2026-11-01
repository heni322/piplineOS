"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { LOCALE, getMessages } from "@/lib/i18n";
import type { SendingProblems, SystemChecks } from "@/lib/types";

const t = getMessages().checks;
const fill = (s: string, v: Record<string, string | number>) =>
  Object.entries(v).reduce((acc, [k, x]) => acc.replaceAll(`{${k}}`, String(x)), s);
const when = (iso: string) =>
  new Date(iso).toLocaleString(LOCALE, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** Run all technical checks on demand, and list the emails that failed or are held, with what to do. */
export default function ChecksPage() {
  const [checks, setChecks] = useState<SystemChecks | null>(null);
  const [problems, setProblems] = useState<SendingProblems | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setRunning(true); setError(null);
    try {
      setChecks(await api<SystemChecks>("/api/v1/system-checks", { method: "POST" }));
      setProblems(await api<SendingProblems>("/api/v1/sending-problems"));
    } catch (err) {
      setError((err as Problem).detail ?? t.loadError);
    } finally {
      setRunning(false);
    }
  }, []);
  useEffect(() => { void run(); }, [run]);

  const failed = checks?.checks.filter((c) => c.status === "fail").length ?? 0;

  return (
    <div className="checks">
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      <button type="button" onClick={run} disabled={running} aria-busy={running}>{running ? t.running : t.run}</button>
      {error && <p role="alert" className="error">{error}</p>}

      {checks && (
        <section aria-labelledby="checks-result">
          <h2 id="checks-result" className="skip">{t.run}</h2>
          <p role="status" className={failed ? "alert" : "status"}>
            {failed ? fill(t.someFail, { n: failed }) : t.allOk} <span className="muted">{fill(t.lastRun, { when: when(checks.checked_at) })}</span>
          </p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label={t.title}>
            <table>
              <tbody>
                {checks.checks.map((c, i) => (
                  <tr key={`${c.key}-${i}`} data-status={c.status}>
                    <td><span className="pill">{t.statuses[c.status]}</span></td>
                    <th scope="row">{c.title}</th>
                    <td>
                      {c.detail}
                      {c.fix && <p className="fix"><strong>{t.fix}</strong> {c.fix}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <h2>{t.problemsTitle}</h2>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.problemsIntro}</p>
      {problems && problems.problems.length === 0 && <p className="status">{t.noProblems}</p>}
      {problems?.problems.map((p) => (
        <section key={p.id} className="panel" data-severity={p.severity} aria-labelledby={`p-${p.id}`}>
          <h3 id={`p-${p.id}`}>
            <span className="pill">{t.severities[p.severity]}</span> {p.company}
            <span className="muted"> · {p.client} · {p.campaign}</span>
          </h3>
          <p>{p.what}</p>
          {p.reasons.length > 0 && (
            <>
              <p><strong>{t.why}</strong></p>
              <ul className="reasons">
                {p.reasons.map((r) => (
                  <li key={r.code}>{r.text}{r.fix && <> — <em>{r.fix}</em></>} <span className="muted">({r.code})</span></li>
                ))}
              </ul>
            </>
          )}
          <p className="fix"><strong>{t.fix}</strong> {p.fix}</p>
          <p className="muted">{fill(t.when, { when: when(p.when) })}</p>
        </section>
      ))}
    </div>
  );
}
