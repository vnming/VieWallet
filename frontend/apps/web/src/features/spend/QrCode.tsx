import { Skeleton } from "@viewallet/ui";
import QRCode from "qrcode";
import { useEffect, useState } from "react";

/** A QR code drawn locally (no third-party image service sees the payload). */
export function QrCode({
  value,
  label,
  size = 224,
}: {
  value: string;
  label: string;
  size?: number;
}) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    QRCode.toDataURL(value, { width: size, margin: 1, errorCorrectionLevel: "M" })
      .then((url) => {
        if (live) setSrc(url);
      })
      .catch(() => {
        if (live) setSrc(null);
      });
    return () => {
      live = false;
    };
  }, [value, size]);
  if (!src) return <Skeleton className="size-56" />;
  return (
    <img
      src={src}
      width={size}
      height={size}
      alt={label}
      className="rounded-md border border-border bg-white p-2"
    />
  );
}
