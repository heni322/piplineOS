import { getMessages } from "@/lib/i18n";
import type { DataRequest } from "@/lib/types";

const t = getMessages().dataRequests;
export const closed = (d: DataRequest) => d.status === "completed" || d.status === "refused_with_reason";

export function Countdown({ d }: { d: DataRequest }) {
  if (closed(d)) return <span className="muted">{d.status.replaceAll("_", " ")}</span>;
  if (d.overdue) return <span className="days late">{t.overdue.replace("{n}", String(Math.abs(d.days_remaining)))}</span>;
  if (d.days_remaining === 0) return <span className="days late">{t.dueToday}</span>;
  return <span className={`days${d.days_remaining <= 7 ? " soon" : ""}`}>{t.daysLeft.replace("{n}", String(d.days_remaining))}</span>;
}

