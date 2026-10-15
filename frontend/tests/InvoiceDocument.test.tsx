import { render, screen, within } from "@testing-library/react";
import { EvidenceChain, InvoiceDocument } from "@/components/InvoiceDocument";
import { money } from "@/lib/format";
import type { Invoice } from "@/lib/types";

const inv: Invoice = {
  id: "i1", tenant_id: "t1", number: "DRAFT-ABC", status: "draft", period_start: "2026-10-01", period_end: "2026-10-31",
  issue_date: "2026-11-02", due_date: "2026-11-16", currency: "GBP", subtotal_minor: 105000, tax_minor: 0, total_minor: 105000,
  tax_wording: "Not registered for VAT.", blocked_reasons: [], disputes_open: 0, paid_minor: 0, due_minor: 0, overdue: false, payments: [],
  supplier: { legal_name: "Example Supplier Ltd", registered_address: "1 Test St", company_number: "123", vat_number: null, payment_details: null },
  customer: { legal_name: "Northwind Demo Services Ltd", registered_address: "1 Example Street", company_number: "000" },
  lines: [
    { id: "a", kind: "setup", description: "Set-up fee", amount_minor: 25000, meeting_id: null, evidence: {}, voided: false },
    { id: "b", kind: "meeting", description: "Qualified meeting attended: Contoso", amount_minor: 30000, meeting_id: "m",
      evidence: { signal_id: "s", message_ids: ["1", "2"], reply_ids: ["r"], qualification_id: "q", meeting_id: "m", attendance_event_id: "a" }, voided: false },
    { id: "c", kind: "meeting", description: "Voided line", amount_minor: 30000, meeting_id: "n", evidence: {}, voided: true },
  ],
};

describe("InvoiceDocument", () => {
  it("shows live lines, totals and a draft marker, and hides voided lines", () => {
    render(<InvoiceDocument inv={inv} />);
    const doc = screen.getByRole("article", { name: "Invoice DRAFT-ABC" });
    expect(doc).toHaveClass("is-draft");
    expect(within(doc).getByText("Set-up fee")).toBeInTheDocument();
    expect(within(doc).queryByText("Voided line")).toBeNull();
    expect(within(doc).getAllByText("£1,050.00").length).toBeGreaterThan(0);
  });

  it("lays out the evidence chain in order and marks missing steps", () => {
    render(<EvidenceChain evidence={{ signal_id: "s", message_ids: ["1", "2"], reply_ids: [], meeting_id: "m" }} />);
    const steps = screen.getAllByRole("listitem");
    expect(steps.map((s) => s.textContent)).toEqual(["Public signal found", "Emails sent (2)", "Their replies",
      "Qualified against your criteria", "Meeting booked", "Attendance confirmed"]);
    expect(steps[2]).toHaveAttribute("data-done", "false");
    expect(steps[1]).toHaveAttribute("data-done", "true");
  });

  it("formats minor units as en-GB money", () => {
    expect(money(-30000, "GBP")).toBe("-£300.00");
    expect(money(123456, "EUR")).toBe("€1,234.56");
  });
});
