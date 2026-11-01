import { describe, expect, it } from "vitest";
import { isSendingLive } from "@/components/OperatorShell";
import type { GovernanceStatus } from "@/lib/types";

const base: GovernanceStatus = {
  global_go_live: true, golive_items: 10, golive_signed: 10, golive_complete: true,
  enabled_countries: ["GB"], live_clients: 1, adapter_modes: { email: "live" },
};

describe("isSendingLive (hides the dry-run banner)", () => {
  it("is live only when the global switch, the email adapter and a client are all live", () => {
    expect(isSendingLive(base)).toBe(true);
  });

  it("stays in dry-run when any one key is off", () => {
    expect(isSendingLive({ ...base, global_go_live: false })).toBe(false);
    expect(isSendingLive({ ...base, adapter_modes: { email: "mock" } })).toBe(false);
    expect(isSendingLive({ ...base, adapter_modes: { email: "sandbox" } })).toBe(false);
    expect(isSendingLive({ ...base, live_clients: 0 })).toBe(false);
  });

  it("stays in dry-run when the status is unknown", () => {
    expect(isSendingLive(null)).toBe(false);
  });
});
