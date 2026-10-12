/** Money is stored in minor units (pence/cents); format it for en-GB readers. */
export function money(minor: number, currency: "GBP" | "EUR" | string = "GBP"): string {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(minor / 100);
}

export function pct(ratio: number | null | undefined): string {
  return ratio === null || ratio === undefined ? "–" : `${Math.round(ratio * 1000) / 10}%`;
}

export function day(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** First day of the month containing `d`, as YYYY-MM-01. */
export function monthStart(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}
