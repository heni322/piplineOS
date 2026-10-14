import { mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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

const LOCK_DIR = join(tmpdir(), "pipelineos-e2e-totp.lock");
const LAST_FILE = join(tmpdir(), "pipelineos-e2e-totp.last");
const window = (at = Date.now()) => Math.floor(at / 30_000);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** One sign-in at a time across all test workers (mkdir is atomic), so no two use the same single-use code. */
async function withLock<T>(fn: () => Promise<T>): Promise<T> {
  for (;;) {
    try { mkdirSync(LOCK_DIR); break; } catch {
      // a lock left behind by a crashed run is stale: a sign-in never holds it this long
      try { if (Date.now() - statSync(LOCK_DIR).mtimeMs > 90_000) rmSync(LOCK_DIR, { recursive: true, force: true }); } catch { /* gone */ }
      await sleep(250);
    }
  }
  try { return await fn(); } finally { rmSync(LOCK_DIR, { recursive: true, force: true }); }
}

/** Signs the operator in. Login codes are single-use, so each sign-in waits for a 30-second window no earlier
 * sign-in has used (recorded in a shared file), then signs in once. */
export async function signInOperator(page: import("@playwright/test").Page, email: string, password: string, secret: string) {
  await withLock(async () => {
    let last = -1;
    try { last = Number(readFileSync(LAST_FILE, "utf8")); } catch { /* first sign-in of the run */ }
    while (window() <= last) await sleep(30_000 - (Date.now() % 30_000) + 500);
    const used = window();
    await page.goto("/login");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByLabel("Authenticator code").fill(totp(secret));
    await page.getByRole("button", { name: "Sign in" }).click();
    writeFileSync(LAST_FILE, String(used));
    await page.waitForURL("**/operator", { timeout: 15_000 });
  });
}
