/**
 * The OLBOS wordmark, as artwork rather than text.
 *
 * Manrope SemiBold outlines for O-L-B-O-S with 0.09em of tracking already
 * baked into the glyph positions. Drawn once, here, and used by every surface
 * that shows the wordmark, so the brand cannot drift between them.
 *
 * Why not a webfont, which is what this replaces:
 *
 *   - No FOUT. The @import carried `display=swap`, so on every cold load the
 *     wordmark painted in a fallback face and then snapped to Manrope. A
 *     visible reflow is the one thing a logo must never do, and it was
 *     happening on the brand element specifically.
 *   - No fourth font family. The app already loads three faces; pulling a
 *     whole Latin face down to set five letters cost 14,172 bytes against
 *     ~2,900 of outlines here.
 *   - Identical rendering everywhere. Hinting, synthetic weights and font
 *     substitution cannot alter a path.
 *
 * SIZED BY CAP HEIGHT, which is what the eye actually measures. `height` is
 * the height of the capitals themselves with no ascender or descender
 * padding, so it is NOT the font-size the text version used -- Manrope's caps
 * are 0.75em, so the 20px text wordmark is 15 here. Width is set explicitly
 * from the aspect ratio so the box is reserved before paint.
 *
 * Colour comes from `currentColor`: callers set `color`.
 */

const VIEWBOX_W = 7137;
const VIEWBOX_H = 1500;
/** Width / cap height. */
export const WORDMARK_ASPECT = VIEWBOX_W / VIEWBOX_H;
/** Manrope's cap height as a fraction of em, for converting old font-sizes. */
export const CAP_PER_EM = 0.75;

export default function BrandWordmark({ height, className }: {
  /** Cap height in px. */
  height: number;
  className?: string;
}) {
  return (
    <svg
      className={className}
      width={Math.round(height * WORDMARK_ASPECT)}
      height={height}
      viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
      fill="currentColor"
      role="img"
      aria-label="Olbos"
      style={{ display: "block", flexShrink: 0 }}
    >
      <g transform="translate(-60 1470)">
        <path d="M741 30Q525 30 373 -64Q221 -159 140 -328Q60 -497 60 -720Q60 -943 140 -1112Q221 -1281 373 -1376Q525 -1470 741 -1470Q956 -1470 1108 -1376Q1261 -1281 1341 -1112Q1421 -943 1421 -720Q1421 -497 1341 -328Q1261 -159 1108 -64Q956 30 741 30ZM741 -169Q894 -167 996 -236Q1097 -306 1148 -430Q1199 -555 1199 -720Q1199 -885 1148 -1008Q1097 -1132 996 -1201Q894 -1270 741 -1271Q588 -1273 486 -1204Q385 -1135 334 -1010Q283 -885 282 -720Q281 -555 332 -432Q383 -308 486 -239Q588 -170 741 -169Z" />
        <path d="M1821 0V-1440H2030V-197H2682V0Z" />
        <path d="M3032 0V-1440H3598Q3735 -1440 3828 -1384Q3921 -1328 3968 -1240Q4015 -1151 4015 -1053Q4015 -934 3956 -849Q3898 -764 3799 -733L3797 -782Q3935 -748 4009 -650Q4083 -551 4083 -420Q4083 -293 4032 -199Q3982 -105 3886 -52Q3789 0 3652 0ZM3244 -199H3620Q3691 -199 3748 -226Q3804 -253 3836 -304Q3869 -354 3869 -424Q3869 -489 3840 -542Q3812 -594 3758 -624Q3705 -655 3633 -655H3244ZM3244 -852H3595Q3653 -852 3700 -876Q3746 -899 3774 -944Q3801 -988 3801 -1051Q3801 -1135 3745 -1189Q3689 -1243 3595 -1243H3244Z" />
        <path d="M5063 30Q4847 30 4695 -64Q4543 -159 4462 -328Q4382 -497 4382 -720Q4382 -943 4462 -1112Q4543 -1281 4695 -1376Q4847 -1470 5063 -1470Q5278 -1470 5430 -1376Q5583 -1281 5663 -1112Q5743 -943 5743 -720Q5743 -497 5663 -328Q5583 -159 5430 -64Q5278 30 5063 30ZM5063 -169Q5216 -167 5318 -236Q5419 -306 5470 -430Q5521 -555 5521 -720Q5521 -885 5470 -1008Q5419 -1132 5318 -1201Q5216 -1270 5063 -1271Q4910 -1273 4808 -1204Q4707 -1135 4656 -1010Q4605 -885 4604 -720Q4603 -555 4654 -432Q4705 -308 4808 -239Q4910 -170 5063 -169Z" />
        <path d="M6645 30Q6490 30 6366 -24Q6241 -77 6160 -176Q6080 -276 6056 -413L6274 -446Q6307 -314 6412 -240Q6517 -167 6657 -167Q6744 -167 6817 -194Q6890 -222 6934 -274Q6979 -325 6979 -397Q6979 -436 6966 -466Q6952 -496 6928 -518Q6905 -541 6872 -558Q6838 -574 6798 -586L6429 -695Q6375 -711 6319 -736Q6263 -762 6216 -804Q6170 -845 6141 -906Q6112 -968 6112 -1056Q6112 -1189 6180 -1282Q6249 -1374 6366 -1422Q6483 -1469 6628 -1469Q6774 -1467 6890 -1417Q7005 -1367 7082 -1274Q7158 -1180 7187 -1047L6963 -1009Q6948 -1090 6899 -1148Q6850 -1207 6779 -1238Q6708 -1270 6625 -1271Q6545 -1273 6478 -1247Q6412 -1221 6372 -1174Q6333 -1127 6333 -1066Q6333 -1006 6368 -969Q6403 -932 6454 -910Q6506 -889 6557 -875L6823 -800Q6873 -786 6936 -762Q7000 -739 7060 -697Q7119 -655 7158 -586Q7197 -516 7197 -411Q7197 -302 7153 -220Q7109 -137 7032 -82Q6956 -26 6856 2Q6756 30 6645 30Z" />
      </g>
    </svg>
  );
}
