import enGB from "../../messages/en-GB.json";

/** All UI strings live in messages/<locale>.json. French (fr-FR) is added at Rollout Stage 3. */
export const catalogues = { "en-GB": enGB } as const;
export type Locale = keyof typeof catalogues;
export type Messages = typeof enGB;

export function getMessages(locale: Locale = "en-GB"): Messages {
  return catalogues[locale];
}
