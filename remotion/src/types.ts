export type Word = {
  text: string;
  start: number;
  end: number;
};

/** A time range (seconds) during which captions must yield (Hyperframes
 *  overlay active, or a b-roll cutaway covering the frame). */
export type TimeWindow = {
  start: number;
  end: number;
};

export type CaptionBand = {
  /** Fraction of height (0..1). Captions are centered between top and bottom. */
  top: number;
  bottom: number;
};

type SlideCommon = {
  start: number;
  end: number;
  /** Accent color for this slide; defaults per slide type. */
  color?: string;
  /** Background; defaults to a dark gradient. */
  background?: string;
};

export type TextSlide = SlideCommon & {
  type: "text";
  title: string;
  subtitle?: string;
  eyebrow?: string;
};

export type StatSlide = SlideCommon & {
  type: "stat";
  /** The final value text shown after the count-up, e.g. "$1.4M" or "100%". */
  value: string;
  /** Numeric value to count up to; if set, animates from `from` to this. */
  to?: number;
  from?: number;
  prefix?: string;
  suffix?: string;
  label?: string;
};

export type ListSlide = SlideCommon & {
  type: "list";
  title?: string;
  items: string[];
};

export type LogoSlide = SlideCommon & {
  type: "logo";
  /** Public-dir-relative path to the downloaded SVG/PNG, e.g. "assets/broll/ripple.svg". */
  src: string;
  /** Brand/company name shown under the mark. */
  name?: string;
  /** Optional short tagline under the name. */
  caption?: string;
  /** True if the asset is a monochrome silhouette that should be tinted to `color`. */
  monochrome?: boolean;
  /** "full" (default) is a full-frame cutaway that hides the base video.
   *  "badge" pops a small rounded card into the upper area, leaving the base
   *  video and captions visible underneath. */
  mode?: "full" | "badge";
};

export type PersonSlide = SlideCommon & {
  type: "person";
  /** Public-dir-relative path to the downloaded photo, e.g. "assets/broll/brad.jpg". */
  src: string;
  /** Person's name (rendered as a lower-third over the photo). */
  name?: string;
  /** Role/title line under the name. */
  role?: string;
  /** Ken Burns direction; defaults to "in" (slow zoom in). */
  motion?: "in" | "out" | "left" | "right" | "up" | "down";
};

export type BRollSlide =
  | TextSlide
  | StatSlide
  | ListSlide
  | LogoSlide
  | PersonSlide;

/** Accent palette derived from the short's Hyperframes style preset, so b-roll
 *  slides color-match the overlays. Slides without an explicit `color` fall back
 *  to one of these by slide type. */
export type Palette = {
  primary: string;
  secondary: string;
  accent: string;
  bg: string;
};

/** One visual transition event — a single Glare sweep, Flash flicker, or
 *  ZoomPunch scale-bump anchored at a moment in the clip. The Python planner
 *  (shortsmith/vfx.py) emits these in lockstep with the audio SFX so audio +
 *  visual punctuation hit the same beat. */
export type VFXEvent = {
  t: number;          // seconds into the final clip
  effect: "glare" | "zoom-punch" | "flash";
  color: string;      // hex tint (ignored by zoom-punch — geometry-only)
  intensity: number;  // 0..1 opacity / scale-bump multiplier
  durationMs: number; // total length of this single event
};

/** A speaker square's position on the 1080x1920 canvas, in px. */
export type PanelRect = { x: number; y: number; w: number; h: number };

export type SpeakerLabel = {
  name: string;
  /** Which stacked square the chip belongs to. */
  position: "top" | "bottom";
};

/** Where a logo badge sits on a split-stack short: a mark-only tile on the
 *  blurred backdrop beside the top speaker's square, in px. Absent on
 *  single-speaker shorts, where the badge pops into the upper center. */
export type BadgeAnchor = { x: number; y: number; size: number };

export type ShortProps = {
  /** Base video, resolved relative to Remotion's public dir (the short folder).
   *  Normally the Hyperframes "renders/final.mp4" so its overlays stay intact. */
  baseVideo: string;
  durationInSeconds: number;
  fps: number;

  // Captions
  captionsEnabled: boolean;
  words: Word[];
  captionBand: CaptionBand;
  captionMaxWords: number;
  captionFadeSeconds: number;
  /** Windows where Hyperframes overlays are active — captions yield here. */
  overlayWindows: TimeWindow[];

  // B-roll cutaways (manual authored list, already validated against free gaps).
  broll: BRollSlide[];

  /** Accent palette from the short's style preset (color-matches Hyperframes). */
  palette: Palette;

  /** Optional visual transitions (Capcut-style glare, zoom-punch, flash).
   *  Empty / omitted = no VFX layer rendered. */
  vfxEvents?: VFXEvent[];

  /** Optional ambient punch-in times (seconds). Each fires a gentle ~1.0->1.045
   *  scale pulse on the base video to reset attention during long talking-head
   *  stretches with no other motion. Planned in the free gaps between overlays,
   *  b-roll, and VFX so it never competes with a hook/callout. Empty = off. */
  /** Caption type size in px. Defaults to 96. Lowered when the caption band is
   *  tight, a split-stack band is bracketed by a face above and below, so
   *  captions have to fit rather than overflow. */
  captionFontSize?: number;

  ambientPunches?: number[];

  /** Speaker name chips for split-stack (two-up) shorts. The source's own Zoom
   *  name badges sit in the corner of each tile and get cropped away by the
   *  square crop, so the layout re-adds them on its own terms. Each chip is
   *  drawn inside its panel, never in the caption band. */
  speakerLabels?: SpeakerLabel[];

  /** Where the two speaker squares sit, top first. Supplied by the layout
   *  preset, the caption band alone does not imply the panel geometry once the
   *  layout carries safe-area margins. */
  speakerPanels?: PanelRect[];

  /** Split-stack only: where the logo badge goes (see BadgeAnchor). */
  logoBadgeAnchor?: BadgeAnchor;
};

export const defaultShortProps: ShortProps = {
  baseVideo: "renders/final.mp4",
  durationInSeconds: 10,
  fps: 30,
  captionsEnabled: true,
  words: [],
  captionBand: { top: 0.6, bottom: 0.8 },
  captionMaxWords: 4,
  captionFadeSeconds: 0.2,
  overlayWindows: [],
  broll: [],
  palette: {
    primary: "#f5c542",
    secondary: "#37bdf8",
    accent: "#34c759",
    bg: "#07121c",
  },
  vfxEvents: [],
  captionFontSize: 96,
  ambientPunches: [],
  speakerLabels: [],
  speakerPanels: [],
};
