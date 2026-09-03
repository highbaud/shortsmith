import React, { useEffect, useMemo, useState } from "react";
import {
  AbsoluteFill,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  delayRender,
  continueRender,
} from "remotion";
// @remotion/media's <Video> supersedes core's <OffthreadVideo> as of 4.0.491 and
// is the recommended tag for new work. Same frame-accurate extraction, without
// the per-frame headless-Chrome screenshot round-trip OffthreadVideo relies on.
import { Video } from "@remotion/media";
import { loadFont } from "@remotion/google-fonts/Anton";
import { BRoll } from "./BRoll";
import { Flash, Glare, useZoomPunchScale } from "./VFX";
import { BRollSlide, CaptionBand, Palette, PanelRect, ShortProps, SpeakerLabel, TimeWindow, Word } from "./types";

// Anton: heavy condensed display face, matches the Hyperframes hook/callouts.
// waitUntilDone() resolves once the webfont is actually parsed/ready — we block
// the render on it below so headless Chrome never paints a fallback-font frame.
const { fontFamily: ANTON, waitUntilDone: waitForAnton } = loadFont();

type Chunk = { words: Word[]; start: number; end: number };

function chunkWords(words: Word[], maxWords: number): Chunk[] {
  const chunks: Chunk[] = [];
  for (let i = 0; i < words.length; i += maxWords) {
    const slice = words.slice(i, i + maxWords);
    if (slice.length === 0) continue;
    chunks.push({
      words: slice,
      start: slice[0].start,
      end: slice[slice.length - 1].end,
    });
  }
  return chunks;
}

/** Opacity multiplier (0..1): 0 inside a yield window, ramping over `fade`
 *  seconds at each edge so captions cross-fade rather than hard-cut. */
function yieldOpacity(t: number, windows: TimeWindow[], fade: number): number {
  let op = 1;
  for (const w of windows) {
    if (t <= w.start - fade || t >= w.end + fade) continue;
    let local = 1;
    if (t < w.start) local = interpolate(t, [w.start - fade, w.start], [1, 0]);
    else if (t > w.end) local = interpolate(t, [w.end, w.end + fade], [0, 1]);
    else local = 0;
    op = Math.min(op, local);
  }
  return op;
}

const Captions: React.FC<{
  words: Word[];
  maxWords: number;
  band: CaptionBand;
  yieldWindows: TimeWindow[];
  fade: number;
  palette: Palette;
  fontSize: number;
}> = ({ words, maxWords, band, yieldWindows, fade, palette, fontSize }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const t = frame / fps;
  const chunks = useMemo(() => chunkWords(words, maxWords), [words, maxWords]);

  const suppress = yieldOpacity(t, yieldWindows, fade);
  if (suppress <= 0) return null;

  let active: Chunk | null = null;
  for (const c of chunks) {
    if (t >= c.start - 0.05 && t <= c.end + 0.25) {
      active = c;
      break;
    }
    if (t > c.end) active = c;
  }
  if (!active) return null;

  const appear = interpolate(
    t,
    [active.start - 0.08, active.start + 0.12],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const bandTopPx = band.top * height;
  const bandHeightPx = (band.bottom - band.top) * height;

  return (
    <AbsoluteFill style={{ opacity: suppress }}>
      <div
        style={{
          position: "absolute",
          top: bandTopPx,
          height: bandHeightPx,
          left: 70,
          right: 70,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          // Hard guarantee for the split-stack layout: the band is bracketed by
          // a face above AND below, so a caption that grew an unexpected extra
          // line would land on someone's chin. Clip instead.
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
            // Per-span margins (below) handle word spacing — flex `gap` is not
            // honored reliably in the headless-Chrome render, so don't depend on it.
            transform: `scale(${interpolate(appear, [0, 1], [0.92, 1])})`,
            opacity: appear,
          }}
        >
          {active.words.map((w, i) => {
            // Three states: the word being spoken right now (accent + pop),
            // words already spoken (solid white), words not yet spoken (dim).
            const isActive = t >= w.start - 0.03 && t < w.end + 0.10;
            const spoken = t >= w.start - 0.02;
            const pop = isActive
              ? interpolate(t, [w.start - 0.03, w.start + 0.10], [1.0, 1.14], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                })
              : 1;
            const color = isActive
              ? palette.primary
              : spoken
                ? "#ffffff"
                : "rgba(255,255,255,0.5)";
            return (
              <span
                key={i}
                style={{
                  display: "inline-block",
                  // Longhand single-value margins: the space-separated `margin`
                  // shorthand (and flex `gap`) get dropped in this render env.
                  marginLeft: 28,
                  marginRight: 28,
                  marginTop: 8,
                  marginBottom: 8,
                  fontFamily: ANTON,
                  fontWeight: 400, // Anton is single-weight
                  fontSize,
                  lineHeight: 1.05,
                  color,
                  transform: `scale(${pop})`,
                  transformOrigin: "center 60%",
                  // Outline via layered text-shadow (NOT -webkit-text-stroke,
                  // which self-intersects on t/l/a/s glyphs in headless Chrome
                  // and renders as little boxes over the letters).
                  textShadow: [
                    "2px 0 0 #000",
                    "-2px 0 0 #000",
                    "0 2px 0 #000",
                    "0 -2px 0 #000",
                    "1.5px 1.5px 0 #000",
                    "-1.5px 1.5px 0 #000",
                    "1.5px -1.5px 0 #000",
                    "-1.5px -1.5px 0 #000",
                    "0 4px 16px rgba(0,0,0,0.7)",
                  ].join(", "),
                }}
              >
                {w.text}
              </span>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};

/** Name chips for the split-stack layout, drawn INSIDE each speaker square.
 *
 *  Panel rectangles come from the layout preset. They used to be inferred from
 *  the caption band, which only held while the panels ran edge to edge, once
 *  the layout gained safe-area margins that inference put the lower chip well
 *  inside the speaker's face.
 *
 *  Chips hug the band-facing edge of their own panel and are left-aligned,
 *  while captions are centered in the band, so the two never collide.
 */
const SpeakerLabels: React.FC<{
  labels: SpeakerLabel[];
  band: CaptionBand;
  panels: PanelRect[];
  palette: Palette;
}> = ({ labels, band, panels, palette }) => {
  const { width, height } = useVideoConfig();
  const inset = 22;
  // Fall back to the band-derived geometry only when no panels were supplied
  // (an older clip spec); correct for a zero-margin layout.
  const fallbackH = band.top * height;
  const fallback: PanelRect[] = [
    { x: Math.max(0, (width - fallbackH) / 2), y: 0, w: fallbackH, h: fallbackH },
    {
      x: Math.max(0, (width - fallbackH) / 2),
      y: height - fallbackH,
      w: fallbackH,
      h: fallbackH,
    },
  ];

  return (
    <>
      {labels.map((label, i) => {
        const isTop = label.position === "top";
        const panel = (panels.length >= 2 ? panels : fallback)[isTop ? 0 : 1];
        const style: React.CSSProperties = isTop
          ? { top: panel.y + panel.h - inset, transform: "translateY(-100%)" }
          : { top: panel.y + inset };
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: panel.x + inset,
              ...style,
              paddingLeft: 20,
              paddingRight: 20,
              paddingTop: 8,
              paddingBottom: 8,
              borderRadius: 8,
              backgroundColor: "rgba(0,0,0,0.62)",
              borderLeft: `4px solid ${palette.primary}`,
              fontFamily: ANTON,
              fontWeight: 400,
              fontSize: 34,
              lineHeight: 1,
              letterSpacing: 1.2,
              textTransform: "uppercase",
              color: "#ffffff",
              textShadow: "0 2px 8px rgba(0,0,0,0.8)",
            }}
          >
            {label.name}
          </div>
        );
      })}
    </>
  );
};

/** Gentle ambient punch-in scale (1.0..~1.045) at the current time, given the
 *  planned punch start times. Each punch eases up to its peak in ~0.28s then
 *  back to rest by ~0.9s. Overlapping punches take the max. Pure — testable and
 *  frame-driven via the `t` the caller passes from useCurrentFrame(). */
function ambientPunchScale(t: number, punches: number[], peak = 1.045): number {
  let scale = 1;
  for (const p of punches) {
    if (t < p || t > p + 0.9) continue;
    const local =
      t <= p + 0.28
        ? interpolate(t, [p, p + 0.28], [1, peak], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          })
        : interpolate(t, [p + 0.28, p + 0.9], [peak, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
    scale = Math.max(scale, local);
  }
  return scale;
}

/** True when a slide carries the fields its card actually reads. The slide list
 *  is hand-authored or LLM-generated JSON and only its start/end are checked on
 *  the Python side, so a `list` with no `items` or a `logo`/`person` with no
 *  `src` arrives intact and takes the whole render down (`.map` of undefined,
 *  `staticFile(undefined)`). A `text` with no title or a `stat` with no number
 *  survives, but hides the base video behind a blank card, which is worse than
 *  not cutting away at all. */
function isRenderable(slide: BRollSlide): boolean {
  switch (slide.type) {
    case "text":
      return Boolean(slide.title);
    case "stat":
      return Boolean(slide.value) || typeof slide.to === "number";
    case "list":
      return Array.isArray(slide.items) && slide.items.length > 0;
    case "logo":
    case "person":
      return Boolean(slide.src);
  }
}

/** Inner component so the useCurrentFrame() inside useZoomPunchScale runs in a
 *  context that's already inside the Composition. Keeps the parent Short
 *  unchanged for callers that pass no vfxEvents. */
const ShortInner: React.FC<ShortProps> = (props) => {
  const {
    baseVideo,
    fps,
    captionsEnabled,
    words,
    captionBand,
    captionMaxWords,
    captionFadeSeconds,
    overlayWindows,
    broll,
    palette,
    vfxEvents = [],
    ambientPunches = [],
    speakerLabels = [],
    speakerPanels = [],
    logoBadgeAnchor,
  } = props;
  const frame = useCurrentFrame();
  // Captions yield during full-frame b-roll cutaways (a slide that covers the
  // frame would otherwise have karaoke text on top of it). Logo *badges* are a
  // small upper-area overlay that leaves the base video and captions visible,
  // so they do NOT suppress captions.
  // Block the render until Anton is loaded so the first frames aren't drawn in a
  // fallback font (headless Chrome would otherwise paint before the webfont is
  // ready). Lazy useState handle => one delayRender per mount; always continue,
  // even on failure, so a font hiccup can't hang the whole render.
  const [fontHandle] = useState(() => delayRender("Loading Anton font"));
  useEffect(() => {
    waitForAnton()
      .then(() => continueRender(fontHandle))
      .catch(() => continueRender(fontHandle));
  }, [fontHandle]);

  // Drop malformed slides before anything reads them, so one bad entry costs
  // its own cutaway rather than the render. Dropping it here also drops its
  // caption-yield window below, so the captions keep running through the gap.
  const slides = useMemo(() => {
    const ok = broll.filter(isRenderable);
    if (ok.length < broll.length) {
      console.warn(
        `Skipped ${broll.length - ok.length} b-roll slide(s) missing required fields`,
      );
    }
    return ok;
  }, [broll]);
  const isBadge = (s: BRollSlide) => s.type === "logo" && s.mode === "badge";
  const brollWindows: TimeWindow[] = slides
    .filter((s) => !isBadge(s))
    .map((s) => ({ start: s.start, end: s.end }));
  const yieldWindows = [...overlayWindows, ...brollWindows];

  // Zoom-punch scales the base video container. Multiple overlapping punches
  // take the max (not the sum) so stacked hooks don't compound.
  const zoomScale = useZoomPunchScale(vfxEvents);
  // Ambient punch-ins reset attention in dead talking-head stretches. Composed
  // multiplicatively with the semantic zoom-punch; both rest at 1.0.
  const ambientScale = ambientPunchScale(frame / fps, ambientPunches);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <AbsoluteFill style={{
        transform: `scale(${zoomScale * ambientScale})`,
        transformOrigin: "center 40%",  // anchor at face zone (face_target_y=0.40)
      }}>
        <Video src={staticFile(baseVideo)} />
      </AbsoluteFill>

      {captionsEnabled ? (
        <Captions
          words={words}
          maxWords={captionMaxWords}
          band={captionBand}
          yieldWindows={yieldWindows}
          fade={captionFadeSeconds}
          palette={palette}
          fontSize={props.captionFontSize ?? 96}
        />
      ) : null}

      {speakerLabels.length > 0 ? (
        <SpeakerLabels labels={speakerLabels} band={captionBand}
                       panels={speakerPanels} palette={palette} />
      ) : null}

      {slides.map((slide, i) => {
        const from = Math.round(slide.start * fps);
        const durationInFrames = Math.max(1, Math.round((slide.end - slide.start) * fps));
        return (
          <Sequence key={i} from={from} durationInFrames={durationInFrames}>
            <BRoll slide={slide} durationInFrames={durationInFrames} palette={palette}
                   badgeAnchor={logoBadgeAnchor} />
          </Sequence>
        );
      })}

      {/* VFX overlays (glare + flash) — rendered last so they sit on top of
          everything else. zoom-punch is applied at the base layer above; not
          repeated here. */}
      {vfxEvents.map((ev, i) => {
        const from = Math.round(ev.t * fps);
        const durationInFrames = Math.max(
          1, Math.round((ev.durationMs / 1000) * fps),
        );
        if (ev.effect === "glare") {
          return (
            <Sequence key={`v${i}`} from={from} durationInFrames={durationInFrames}>
              <Glare startSec={ev.t} durationMs={ev.durationMs}
                     color={ev.color} intensity={ev.intensity} />
            </Sequence>
          );
        }
        if (ev.effect === "flash") {
          return (
            <Sequence key={`v${i}`} from={from} durationInFrames={durationInFrames}>
              <Flash startSec={ev.t} durationMs={ev.durationMs}
                     color={ev.color} intensity={ev.intensity} />
            </Sequence>
          );
        }
        return null;  // zoom-punch handled by useZoomPunchScale above
      })}
    </AbsoluteFill>
  );
};

export const Short: React.FC<ShortProps> = (props) => <ShortInner {...props} />;
