import { getMessages } from "@/lib/i18n";
import { day, money } from "@/lib/format";
import type { Invoice } from "@/lib/types";

const t = getMessages();
const STEPS = ["signal", "messages", "replies", "qualification", "meeting", "attendance"] as const;

function count(v: unknown): number {
  return Array.isArray(v) ? v.length : v ? 1 : 0;
}

/** The evidence behind a billed meeting, as the sequence it happened in. Ids only: no personal data. */
export function EvidenceChain({ evidence }: { evidence: Record<string, unknown> }) {
  const present: Record<(typeof STEPS)[number], number> = {
    signal: count(evidence.signal_id), messages: count(evidence.message_ids), replies: count(evidence.reply_ids),
    qualification: count(evidence.qualification_id), meeting: count(evidence.meeting_id),
    attendance: count(evidence.attendance_event_id),
  };
  return (
    <ol className="chain" aria-label={t.client.chain}>
      {STEPS.map((s) => (
        <li key={s} data-done={present[s] > 0}>
          {t.client.chainSteps[s]}{present[s] > 1 ? ` (${present[s]})` : ""}
        </li>
      ))}
    </ol>
  );
}

/** An invoice laid out as the paper document the client receives. Drafts are watermarked. */
export function InvoiceDocument({ inv }: { inv: Invoice }) {
  const sup = inv.supplier, cus = inv.customer;
  const live = inv.lines.filter((l) => !l.voided);
  return (
    <article className={`invoice sheet${inv.status === "draft" ? " is-draft" : ""}`} aria-label={`Invoice ${inv.number}`}>
      <header className="invoice-head">
        <div>
          <p className="invoice-from">{sup.legal_name ?? "–"}</p>
          <p className="muted">{sup.registered_address}{sup.company_number ? `. Company ${sup.company_number}` : ""}
            {sup.vat_number ? `. VAT ${sup.vat_number}` : ""}</p>
        </div>
        <dl className="invoice-meta">
          <div><dt>{t.billing.number}</dt><dd>{inv.number}</dd></div>
          <div><dt>Date</dt><dd>{day(inv.issue_date)}</dd></div>
          <div><dt>{t.billing.due}</dt><dd>{day(inv.due_date)}</dd></div>
        </dl>
      </header>
      <p className="invoice-to"><span className="muted">Bill to</span><br />{cus.legal_name}<br />
        <span className="muted">{cus.registered_address}{cus.company_number ? `. Company ${cus.company_number}` : ""}</span></p>
      <table className="invoice-lines">
        <thead><tr><th scope="col">Description</th><th scope="col" className="num">Amount</th></tr></thead>
        <tbody>
          {live.map((l) => (
            <tr key={l.id}>
              <td>
                {l.description}
                {l.kind === "meeting" && <EvidenceChain evidence={l.evidence} />}
              </td>
              <td className="num">{money(l.amount_minor, inv.currency)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr><th scope="row">{t.billing.subtotal}</th><td className="num">{money(inv.subtotal_minor, inv.currency)}</td></tr>
          <tr><th scope="row">{t.billing.tax}{inv.tax_wording ? <span className="muted">. {inv.tax_wording}</span> : null}</th>
            <td className="num">{money(inv.tax_minor, inv.currency)}</td></tr>
          <tr className="grand"><th scope="row">{t.billing.total}</th><td className="num">{money(inv.total_minor, inv.currency)}</td></tr>
        </tfoot>
      </table>
      {sup.payment_details && <p className="muted invoice-pay">{sup.payment_details}</p>}
    </article>
  );
}
