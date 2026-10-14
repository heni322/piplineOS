import { createHmac } from "node:crypto";

/** RFC 6238 TOTP (SHA-1, 30 s, 6 digits) for the synthetic e2e operator. */
export function totp(base32: string, at = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of base32.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1]! & 0xf;
  const code = ((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).toString();
  return code.padStart(6, "0");
}

/** Signs the operator in. Login codes are single-use, so if this 30-second code was already used by another test,
 * wait for the next one and try again. Tests run in parallel, so several may wait for the same next code: each
 * window lets at least one in, hence up to four attempts. */
export async function signInOperator(page: import("@playwright/test").Page, email: string, password: string, secret: string) {
  for (let attempt = 0; attempt < 4; attempt++) {
    await page.goto("/login");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByLabel("Authenticator code").fill(totp(secret));
    await page.getByRole("button", { name: "Sign in" }).click();
    try {
      await page.waitForURL("**/operator", { timeout: 5000 });
      return;
    } catch {
      await page.waitForTimeout(30_000 - (Date.now() % 30_000) + 1000);
    }
  }
  throw new Error("operator sign-in failed four times");
}
