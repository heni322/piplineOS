"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Summary } from "@/lib/types";

const t = getMessages().operator;

export default function OperatorHome() {
  const [s, setS] = useState<Summary | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { api<Summary>("/api/v1/pipeline/summary").then(setS).catch(() => setError(true)); }, []);

  if (error) return <p role="alert" className="error">{t.loadError}</p>;
  if (!s) return <p className="muted" aria-busy="true">{t.loading}</p>;
  const states = Object.entries(s.by_state).sort((a, b) => b[1] - a[1]);
  const ai = Object.entries(s.llm_calls).map(([m, n]) => `${n} ${m}`).join(", ") || "0";
  return (
    <>
      <h1>{t.title}</h1>
      <dl className="ledger">
        <div><dt>{getMessages().nav.approvals}</dt><dd>{s.pending_approval}</dd></div>
        <div><dt>{t.dryRunSends}</dt><dd>{s.dry_run_sends}</dd></div>
        <div><dt>{t.realSends}</dt><dd>{s.real_sends}</dd></div>
        <div><dt>{t.altTasks}</dt><dd>{s.open_alternative_tasks}</dd></div>
        <div><dt>{t.openEscalations}</dt><dd>{s.open_escalations}</dd></div>
        <div><dt>{t.llmCalls}</dt><dd style={{ fontSize: "1rem", paddingTop: 8 }}>{ai}</dd></div>
      </dl>
      {s.pending_approval > 0 && <p><Link href="/operator/approvals">{t.pendingLink}</Link></p>}
      <h2 style={{ marginTop: 28 }}>{t.byState}</h2>
      {states.length === 0 ? <p className="muted">{t.emptyPipeline}</p> : (
        <ul className="states">
          {states.map(([state, n]) => <li key={state}><span>{state.replaceAll("_", " ")}</span><span>{n}</span></li>)}
        </ul>
      )}
    </>
  );
}
