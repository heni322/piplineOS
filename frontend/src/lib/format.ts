import { LOCALE } from "@/lib/i18n";

/** Money is stored in minor units (pence/cents); format it for this site's readers. */
export function money(minor: number, currency: "GBP" | "EUR" | string = "GBP"): string {
  return new Intl.NumberFormat(LOCALE, { style: "currency", currency }).format(minor / 100);
}

export function pct(ratio: number | null | undefined): string {
  return ratio === null || ratio === undefined
    ? "–"
    : new Intl.NumberFormat(LOCALE, { style: "percent", maximumFractionDigits: 1 }).format(ratio);
}

export function day(iso: string): string {
  return new Date(iso).toLocaleDateString(LOCALE, { day: "numeric", month: "short", year: "numeric" });
}

/** First day of the month containing `d`, as YYYY-MM-01. */
export function monthStart(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

/** A link from source data, only when it is a web address: never render a javascript: or data: link. */
export function webUrl(u: string | null | undefined): string | null {
  return u && /^https?:\/\//i.test(u) ? u : null;
}
