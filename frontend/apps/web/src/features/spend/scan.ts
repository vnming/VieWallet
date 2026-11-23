/**
 * What a scanned QR code (or a pasted link/code) points to. Only VieWallet links and bare
 * payment codes are accepted; anything else is ignored, so a QR cannot send the user to an
 * arbitrary site from the scanner.
 */
export type ScanTarget = { kind: "pay"; code: string } | { kind: "transfer"; to: string };

const CODE = /^[0-9A-Za-z]{16}$/;
const WALLET = /^[1-9]\d{9}$/;

export function parseScanned(raw: string, origin: string): ScanTarget | null {
  const text = raw.trim();
  if (CODE.test(text)) return { kind: "pay", code: text.toUpperCase() };
  if (WALLET.test(text)) return { kind: "transfer", to: text };
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.origin !== origin) return null;
  const pay = /^\/pay\/([0-9A-Za-z]{16})\/?$/.exec(url.pathname);
  if (pay?.[1]) return { kind: "pay", code: pay[1].toUpperCase() };
  const to = url.searchParams.get("to");
  if (url.pathname === "/transfer" && to && WALLET.test(to)) return { kind: "transfer", to };
  return null;
}

export function pathFor(target: ScanTarget): string {
  return target.kind === "pay" ? `/pay/${target.code}` : `/transfer?to=${target.to}`;
}

/** Minimal typing for the Barcode Detection API (Chromium/Android; absent in Safari/Firefox). */
interface DetectedBarcode {
  rawValue: string;
}
interface BarcodeDetectorLike {
  detect(source: CanvasImageSource): Promise<DetectedBarcode[]>;
}
type BarcodeDetectorCtor = new (options: { formats: string[] }) => BarcodeDetectorLike;

export function barcodeDetector(): BarcodeDetectorLike | null {
  const ctor = (globalThis as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector;
  return ctor ? new ctor({ formats: ["qr_code"] }) : null;
}
