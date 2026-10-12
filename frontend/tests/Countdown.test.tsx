import { render, screen } from "@testing-library/react";
import { Countdown } from "@/components/Countdown";
import type { DataRequest } from "@/lib/types";

const base: DataRequest = {
  id: "1", tenant_id: "t", lead_id: null, company: "Blue Yonder", request_type: "access", regime: "uk_gdpr",
  controller: "tenant", status: "open", received_at: "2026-10-13T10:00:00Z", deadline_at: "2026-11-13T10:00:00Z",
  days_remaining: 31, overdue: false, response_draft: null, completed_at: null,
};

describe("Countdown", () => {
  it("shows days left, and warns in the last week", () => {
    const { rerender } = render(<Countdown d={base} />);
    expect(screen.getByText("31 days left")).not.toHaveClass("soon");
    rerender(<Countdown d={{ ...base, days_remaining: 5 }} />);
    expect(screen.getByText("5 days left")).toHaveClass("soon");
  });
  it("marks overdue and due-today requests loudly", () => {
    const { rerender } = render(<Countdown d={{ ...base, days_remaining: -2, overdue: true }} />);
    expect(screen.getByText("Overdue by 2 days")).toHaveClass("late");
    rerender(<Countdown d={{ ...base, days_remaining: 0 }} />);
    expect(screen.getByText("Due today")).toHaveClass("late");
  });
  it("shows the outcome once closed", () => {
    render(<Countdown d={{ ...base, status: "completed", days_remaining: -40, overdue: false }} />);
    expect(screen.getByText("completed")).toBeInTheDocument();
  });
});
