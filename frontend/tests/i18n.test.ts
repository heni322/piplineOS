import { catalogues } from "@/lib/i18n";
import { pct } from "@/lib/format";

// every leaf with its path as a list (a key may itself contain a dot, e.g. "outlook.com")
function leaves(obj: object, path: string[] = []): [string[], unknown][] {
  return Object.entries(obj).flatMap(([k, v]) =>
    v !== null && typeof v === "object" ? leaves(v, [...path, k]) : [[[...path, k], v] as [string[], unknown]],
  );
}
const placeholders = (s: unknown) => (typeof s === "string" ? (s.match(/\{[a-z]+\}/gi) ?? []).sort() : []);

describe("i18n catalogues", () => {
  it("has no empty strings", () => {
    for (const cat of Object.values(catalogues)) {
      for (const [path, value] of leaves(cat)) {
        expect(value, path.join(" > ")).not.toBe("");
      }
    }
  });

  it("every language has exactly the English keys and placeholders", () => {
    const english = new Map(leaves(catalogues["en-GB"]).map(([p, v]) => [p.join("\u0000"), v]));
    for (const [locale, cat] of Object.entries(catalogues)) {
      const own = new Map(leaves(cat).map(([p, v]) => [p.join("\u0000"), v]));
      expect([...own.keys()].sort(), `${locale}: same keys`).toEqual([...english.keys()].sort());
      for (const [key, value] of english) {
        expect(placeholders(own.get(key)), `${locale}: ${key.replaceAll("\u0000", " > ")}`).toEqual(placeholders(value));
      }
    }
  });

  it("formats percentages for the site's language", () => {
    expect(pct(0.125)).toBe("12.5%");  // tests run with the default language, en-GB
    expect(pct(null)).toBe("–");
  });
});
