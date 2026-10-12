import { catalogues } from "@/lib/i18n";

function keys(obj: object, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([k, v]) => (typeof v === "object" ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`]));
}

describe("i18n catalogues", () => {
  it("has no empty strings", () => {
    for (const cat of Object.values(catalogues)) {
      for (const k of keys(cat)) {
        const value = k.split(".").reduce<unknown>((o, p) => (o as Record<string, unknown>)[p], cat);
        expect(value).not.toBe("");
      }
    }
  });
});
