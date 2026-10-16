/**
 * The complete Olbos Trade horizontal lockup.
 *
 * The artwork contains the angular silver O mark, the OLBOS wordmark and the
 * gold TRADE descriptor as one immutable asset. Keeping those proportions in
 * one file prevents the header, landing page and authentication shell from
 * assembling slightly different logos.
 *
 * `height` is the rendered height of the complete lockup. Width is reserved
 * from the cropped artwork's intrinsic aspect ratio so the surrounding chrome
 * never shifts while the image loads.
 */

const LOCKUP_W = 1086;
const LOCKUP_H = 280;
export const WORDMARK_ASPECT = LOCKUP_W / LOCKUP_H;

export default function BrandWordmark({ height, className }: {
  height: number;
  className?: string;
}) {
  return (
    <img
      className={className}
      src="/olbos-trade-lockup.webp"
      width={Math.round(height * WORDMARK_ASPECT)}
      height={height}
      alt="Olbos Trade"
      decoding="async"
      style={{ display: "block", flexShrink: 0, objectFit: "contain" }}
    />
  );
}
