import enGB from "../../messages/en-GB.json";
import frFR from "../../messages/fr-FR.json";

/** All UI strings live in messages/<locale>.json. The language is chosen per deployment, at build time, with
 * NEXT_PUBLIC_LOCALE (en-GB by default; fr-FR for a French site such as voyz.fr). Every catalogue must have exactly
 * the keys and placeholders of en-GB (tests/i18n.test.ts). */
export type Messages = typeof enGB;
const fr: Messages = frFR;
export const catalogues = { "en-GB": enGB, "fr-FR": fr } as const;
export type Locale = keyof typeof catalogues;

const requested = process.env.NEXT_PUBLIC_LOCALE;
export const LOCALE: Locale = requested && requested in catalogues ? (requested as Locale) : "en-GB";
/** Times are shown in the operator's own time zone (the business's base), not the browser's. */
export const TIME_ZONE = process.env.NEXT_PUBLIC_TIME_ZONE || (LOCALE === "fr-FR" ? "Europe/Paris" : "Europe/London");

export function getMessages(locale: Locale = LOCALE): Messages {
  return catalogues[locale];
}
