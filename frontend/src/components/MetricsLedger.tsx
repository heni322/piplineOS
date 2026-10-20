import { getMessages } from "@/lib/i18n";
import { money, pct } from "@/lib/format";
import type { Metrics } from "@/lib/types";

const t = getMessages().reports;
const reasons = getMessages().billing.reasons as Record<string, string>;
const esc = getMessages().escalations.reasons as Record<string, string>;
const per = (n: number | null) => (n === null ? "–" : n.toFixed(1));

/** The funnel as a ledger: what was sent, what came back, what was held and billed. The business view is per 100
 * people reached, and a dry-run funnel is always labelled as such so demo numbers are never read as results. */
export function MetricsLedger({ m, detailed = false }: { m: Metrics; detailed?: boolean }) {
  const blocks = Object.entries(m.compliance_blocks);
  const escalations = Object.entries(m.escalations);
  const p = m.per_100_reached;
  const over = (r: number | null, max: number) => (r !== null && m.emails_sent >= 50 && r >= max ? " outcome-block" : "");
  return (
    <>
      {m.data_mode === "dry_run" && <p className="banner" role="note">{t.dryRunWarning}</p>}
      {m.data_mode === "mixed" && <p className="banner" role="note">{t.mixedWarning}</p>}
      <dl className="ledger">
        <div><dt>{t.reached}</dt><dd>{m.people_reached} <small className="muted">{t.reachedHint}</small></dd></div>
        <div><dt>{t.replied}</dt><dd>{m.replied} <small className="muted">{pct(m.reply_rate)}</small></dd></div>
        <div><dt>{t.positive}</dt><dd>{m.positive_replies}</dd></div>
        <div><dt>{t.booked}</dt><dd>{m.meetings_booked}</dd></div>
        <div><dt>{t.attended}</dt><dd>{m.meetings_attended} <small className="muted">{t.showRate} {pct(m.show_rate)}</small></dd></div>
        <div><dt>{t.qualified}</dt><dd>{m.qualified_meetings}</dd></div>
        <div><dt>{t.billed}</dt><dd>{m.meetings_billed} <small className="muted">{money(m.billed_minor)}</small></dd></div>
      </dl>
      <h2 style={{ marginTop: 24 }}>{t.per100}</h2>
      <dl className="ledger">
        <div><dt>{t.per100Replies}</dt><dd>{per(p.replies)}</dd></div>
        <div><dt>{t.per100Positive}</dt><dd>{per(p.positive_replies)}</dd></div>
        <div><dt>{t.per100Booked}</dt><dd>{per(p.meetings_booked)}</dd></div>
        <div><dt>{t.per100Qualified}</dt><dd>{per(p.qualified_meetings)}</dd></div>
      </dl>
      <h2 style={{ marginTop: 24 }}>{t.deliverability}</h2>
      <dl className="ledger">
        <div><dt>{t.sent}</dt><dd>{m.emails_sent}</dd></div>
        <div><dt>{t.delivered}</dt><dd>{m.emails_delivered}</dd></div>
        <div><dt>{t.bounces}</dt><dd className={over(m.bounce_rate, 0.03)}>{m.bounces} <small className="muted">{pct(m.bounce_rate)} · {t.bounceLimit}</small></dd></div>
        <div><dt>{t.complaints}</dt><dd className={over(m.complaint_rate, 0.003)}>{m.complaints} <small className="muted">{pct(m.complaint_rate)} · {t.complaintLimit}</small></dd></div>
      </dl>
      {m.subject_test && (
        <>
          <h2 style={{ marginTop: 24 }}>{t.abTitle}</h2>
          <dl className="ledger">
            {m.subject_test.variants.map((v) => (
              <div key={v.variant}><dt>{t.abVariant.replace("{n}", String(v.variant + 1))}</dt>
                <dd>{per(v.positive_per_100)} <small className="muted">{t.per100Positive} · {v.reached} {t.abReached}</small></dd></div>
            ))}
          </dl>
          <p className="muted">{((t.abVerdicts as Record<string, string>)[m.subject_test.verdict] ?? "")
            .replace("{n}", String((m.subject_test.leader ?? 0) + 1)).replace("{p}", String(m.subject_test.p_value ?? "–"))
            .replace("{m}", String(m.subject_test.min_reached_per_variant))}</p>
        </>
      )}
      {m.roi && m.roi.status === "estimate" && (
        <>
          <h2 style={{ marginTop: 24 }}>{t.roiTitle}</h2>
          <p className="muted">{t.roiHint}</p>
          <dl className="ledger">
            <div><dt>{t.pipeline}</dt><dd>{money(m.roi.pipeline_value_minor, m.roi.currency)}</dd></div>
            <div><dt>{t.expected}</dt><dd>{money(m.roi.expected_revenue_minor, m.roi.currency)} <small className="muted">{m.roi.close_rate_pct}%</small></dd></div>
            <div><dt>{t.invoiced}</dt><dd>{money(m.roi.invoiced_minor, m.roi.currency)}</dd></div>
            <div><dt>{t.multiple}</dt><dd>{m.roi.roi_multiple === null ? "–" : `${m.roi.roi_multiple}×`}</dd></div>
          </dl>
        </>
      )}
      {detailed && m.by_source && m.by_source.length > 0 && (
        <>
          <h2 style={{ marginTop: 24 }} id="quality-h">{t.qualityTitle}</h2>
          <p className="muted" style={{ maxWidth: "70ch" }}>{t.qualityHint}</p>
          <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="quality-h">
            <table>
              <thead><tr>
                <th scope="col">{t.qSource}</th><th scope="col">{t.qLeads}</th><th scope="col">{t.qRegister}</th>
                <th scope="col">{t.qWebsite}</th><th scope="col">{t.qEmail}</th><th scope="col">{t.qBlocked}</th>
                <th scope="col">{t.qTasks}</th><th scope="col">{t.qReached}</th><th scope="col">{t.qBounced}</th>
                <th scope="col">{t.qReplied}</th>
              </tr></thead>
              <tbody>
                {m.by_source.map((s) => (
                  <tr key={s.source}>
                    <th scope="row">{(t.sourceNames as Record<string, string>)[s.source] ?? s.source}</th>
                    <td>{s.leads}</td><td>{s.register_confirmed}</td><td>{s.with_website}</td>
                    <td>{s.with_email} <small className="muted">{pct(s.email_rate)}</small></td>
                    <td>{s.blocked}</td><td>{s.contact_tasks}</td><td>{s.reached}</td>
                    <td className={over(s.bounce_rate, 0.03)}>{s.bounced} <small className="muted">{pct(s.bounce_rate)}</small></td>
                    <td>{s.replied} <small className="muted">{pct(s.reply_rate)}</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
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
