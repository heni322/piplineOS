import { getMessages } from "@/lib/i18n";
import { money, pct } from "@/lib/format";
import type { Metrics } from "@/lib/types";

const t = getMessages().reports;
const reasons = getMessages().billing.reasons as Record<string, string>;
const esc = getMessages().escalations.reasons as Record<string, string>;

/** The funnel as a ledger: what was sent, what came back, what was held and billed. */
export function MetricsLedger({ m, detailed = false }: { m: Metrics; detailed?: boolean }) {
  const blocks = Object.entries(m.compliance_blocks);
  const escalations = Object.entries(m.escalations);
  return (
    <>
      <dl className="ledger">
        <div><dt>{t.contacted}</dt><dd>{m.contacted}</dd></div>
        <div><dt>{t.replied}</dt><dd>{m.replied} <small className="muted">{pct(m.reply_rate)}</small></dd></div>
        <div><dt>{t.positive}</dt><dd>{m.positive_replies}</dd></div>
        <div><dt>{t.booked}</dt><dd>{m.meetings_booked}</dd></div>
        <div><dt>{t.attended}</dt><dd>{m.meetings_attended} <small className="muted">{t.showRate} {pct(m.show_rate)}</small></dd></div>
        <div><dt>{t.billed}</dt><dd>{m.meetings_billed} <small className="muted">{money(m.billed_minor)}</small></dd></div>
      </dl>
      {detailed && (
        <>
          <p className="muted">{t.aiCost}: {m.ai_cost_per_meeting_gbp !== null ? money(Math.round(m.ai_cost_per_meeting_gbp * 100)) : t.costUnknown}</p>
          <h2 style={{ marginTop: 24 }}>{t.blocks}</h2>
          {blocks.length === 0 ? <p className="muted">{t.none}</p> : (
            <ul className="states">{blocks.map(([r, n]) => <li key={r}><span>{reasons[r] ?? r.replaceAll("_", " ").toLowerCase()}</span><span>{n}</span></li>)}</ul>
          )}
          <h2 style={{ marginTop: 24 }}>{t.escalations}</h2>
          {escalations.length === 0 ? <p className="muted">{t.none}</p> : (
            <ul className="states">{escalations.map(([r, n]) => <li key={r}><span>{esc[r] ?? r}</span><span>{n}</span></li>)}</ul>
          )}
        </>
      )}
    </>
  );
}
