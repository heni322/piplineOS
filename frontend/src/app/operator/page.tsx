"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Campaign, Summary } from "@/lib/types";

const t = getMessages().operator;

export default function OperatorHome() {
  const [s, setS] = useState<Summary | null>(null);
  const [error, setError] = useState(false);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaign, setCampaign] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const load = useCallback(() => { api<Summary>("/api/v1/pipeline/summary").then(setS).catch(() => setError(true)); }, []);
  useEffect(() => {
    load();
    api<Campaign[]>("/api/v1/campaigns").then((cs) => { setCampaigns(cs); setCampaign((c) => c || cs[0]?.id || ""); }).catch(() => undefined);
  }, [load]);

  async function run(kind: "pipeline" | "send") {
    const c = campaigns.find((x) => x.id === campaign);
    if (!c) return;
    setBusy(true); setNotice(null); setRunError(null);
    try {
      if (kind === "pipeline") {
        const r = await api<{ discovered: number }>("/api/v1/pipeline/run", { method: "POST", body: JSON.stringify({ tenant_id: c.tenant_id, campaign_id: c.id }) });
        setNotice(t.ran.replace("{n}", String(r.discovered)));
      } else {
        const r = await api<{ results: Record<string, number> }>("/api/v1/send/run", { method: "POST", body: JSON.stringify({ tenant_id: c.tenant_id }) });
        const parts = Object.entries(r.results).map(([k, n]) => `${n} ${k.replaceAll("_", " ")}`);
        setNotice(t.sent.replace("{s}", parts.join(", ") || t.nothingDue));
      }
      load();
    } catch (e) { setRunError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

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
      <section aria-labelledby="run-h" style={{ marginTop: 28 }}>
        <h2 id="run-h">{t.runTitle}</h2>
        <p className="muted" style={{ maxWidth: "70ch" }}>{t.runIntro}</p>
        {campaigns.length === 0 ? <p className="muted">{t.noCampaigns}</p> : (
          <div className="toolbar">
            <div>
              <label htmlFor="campaign">{t.campaign}</label>
              <select id="campaign" value={campaign} onChange={(e) => setCampaign(e.target.value)}>
                {campaigns.map((c) => <option key={c.id} value={c.id}>{c.tenant_name}: {c.name}</option>)}
              </select>
            </div>
            <button type="button" disabled={busy || !campaign} onClick={() => run("pipeline")}>{t.runCampaign}</button>
            <button type="button" className="secondary" disabled={busy || !campaign} onClick={() => run("send")}>{t.sendDue}</button>
          </div>
        )}
        {notice && <p className="status" role="status">{notice}</p>}
        {runError && <p role="alert" className="error">{runError}</p>}
      </section>
      <h2 style={{ marginTop: 28 }}>{t.byState}</h2>
      {states.length === 0 ? <p className="muted">{t.emptyPipeline}</p> : (
        <ul className="states">
          {states.map(([state, n]) => <li key={state}><span>{state.replaceAll("_", " ")}</span><span>{n}</span></li>)}
        </ul>
      )}
    </>
  );
}
