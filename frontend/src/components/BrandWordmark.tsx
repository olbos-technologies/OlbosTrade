/**
 * The horizontal logo lockup for whichever brand this page is.
 *
 * Each brand's mark, name and descriptor are one finished image. Keeping the
 * proportions in the brand registry (and this one component) prevents the
 * header, landing page and authentication shell from assembling slightly
 * different logos.
 *
 * `height` is the rendered height of the lockup. Width is reserved from the
 * brand's aspect ratio, so the surrounding chrome never shifts while the image
 * loads and callers need no per-brand layout.
 */

import { getBrand } from "../brand/brands";

export default function BrandWordmark({ height, className }: {
  height: number;
  className?: string;
}) {
  const { wordmark } = getBrand();
  return (
    <img
      className={className}
      src={wordmark.src}
      width={Math.round(height * wordmark.aspect)}
      height={height}
      alt={wordmark.alt}
      decoding="async"
      style={{ display: "block", flexShrink: 0, objectFit: "contain" }}
    />
  );
}

/** The standalone mark (no wordmark), at a square `size`. */
export function BrandMark({ size, className }: { size: number; className?: string }) {
  const { standaloneMark } = getBrand();
  return <img className={className} src={standaloneMark.src} alt="" width={size} height={size} />;
}
