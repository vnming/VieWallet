/**
 * E2E helpers: Mailpit API, staff provisioning through the CLI inside the api container,
 * and RFC 6238 TOTP (implemented with node:crypto to avoid a dependency).
 */
import { execFileSync } from "node:child_process";
import { createHmac, randomBytes } from "node:crypto";
import path from "node:path";

const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..");
const MAILPIT = process.env.VW_E2E_MAILPIT_URL ?? "http://127.0.0.1:8025";
const DOCKER = process.env.VW_E2E_DOCKER ?? "docker";

export const PASSWORD = "mây-trắng-bay-2026";
export const PIN = "270419";

export function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${randomBytes(3).toString("hex")}@example.com`;
}

function compose(args: string[], input?: string): string {
  return execFileSync(DOCKER, ["compose", ...args], {
    cwd: REPO_ROOT,
    input,
    encoding: "utf-8",
    stdio: ["pipe", "pipe", "pipe"],
  });
}

/** Rate-limit counters live in Redis; a fresh suite must not inherit them from earlier runs. */
export function resetRateLimits(): void {
  compose(["exec", "-T", "redis", "redis-cli", "FLUSHDB"]);
}

export function createStaff(email: string, roles: string[]): void {
  const args = [
    "exec",
    "-T",
    "api",
    "python",
    "-m",
    "app.scripts.create_staff",
    "--email",
    email,
    "--name",
    "E2E Staff",
  ];
  for (const role of roles) args.push("--role", role);
  compose(args, `${PASSWORD}\n`);
}

interface MailpitMessage {
  ID: string;
  Subject: string;
}

/** Wait for the newest e-mail to `to` whose subject matches, and return its text body. */
export async function waitForEmail(
  to: string,
  subject: RegExp,
  timeoutMs = 20_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const query = encodeURIComponent(`to:"${to}"`);
    const response = await fetch(`${MAILPIT}/api/v1/search?query=${query}`);
    if (response.ok) {
      const { messages } = (await response.json()) as { messages: MailpitMessage[] };
      const match = messages.find((m) => subject.test(m.Subject));
      if (match) {
        const message = await fetch(`${MAILPIT}/api/v1/message/${match.ID}`);
        const { Text } = (await message.json()) as { Text: string };
        return Text;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`no e-mail to ${to} matching ${subject} within ${timeoutMs} ms`);
}

export function sixDigitCode(text: string): string {
  const match = /\b(\d{6})\b/.exec(text);
  if (!match?.[1]) throw new Error(`no 6-digit code in: ${text}`);
  return match[1];
}

function base32Decode(input: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of input.replace(/=+$/, "").toUpperCase()) {
    const value = alphabet.indexOf(char);
    if (value < 0) throw new Error("invalid base32");
    bits += value.toString(2).padStart(5, "0");
  }
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8)
    bytes.push(Number.parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

export function totp(secret: string, at = Date.now(), stepOffset = 0): string {
  const counter = Math.floor(at / 1000 / 30) + stepOffset;
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const digest = createHmac("sha1", base32Decode(secret)).update(message).digest();
  const offset = digest[digest.length - 1]! & 0x0f;
  const code = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, "0");
}

/** Simulated KYC (Phase 4 stand-in for the Phase 6 flow): raise a customer to tier 1. */
export function setKycTier(email: string, tier: 0 | 1): void {
  compose([
    "exec",
    "-T",
    "api",
    "python",
    "-m",
    "app.scripts.set_kyc_tier",
    "--email",
    email,
    "--tier",
    String(tier),
  ]);
}
