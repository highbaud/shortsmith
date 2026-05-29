import React, { useEffect, useMemo, useState } from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  delayRender,
  continueRender,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/Anton";
import { BRoll } from "./BRoll";
import { Flash, Glare, useZoomPunchScale } from "./VFX";
import { CaptionBand, Palette, ShortProps, TimeWindow, Word } from "./types";

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
}> = ({ words, maxWords, band, yieldWindows, fade, palette }) => {
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
                  fontSize: 96,
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
  } = props;
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

  const isBadge = (s: typeof broll[number]) => s.type === "logo" && s.mode === "badge";
  const brollWindows: TimeWindow[] = broll
    .filter((s) => !isBadge(s))
    .map((s) => ({ start: s.start, end: s.end }));
  const yieldWindows = [...overlayWindows, ...brollWindows];

  // Zoom-punch scales the base video container. Multiple overlapping punches
  // take the max (not the sum) so stacked hooks don't compound.
  const zoomScale = useZoomPunchScale(vfxEvents);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <AbsoluteFill style={{
        transform: `scale(${zoomScale})`,
        transformOrigin: "center 40%",  // anchor at face zone (face_target_y=0.40)
      }}>
        <OffthreadVideo src={staticFile(baseVideo)} />
      </AbsoluteFill>

      {captionsEnabled ? (
        <Captions
          words={words}
          maxWords={captionMaxWords}
          band={captionBand}
          yieldWindows={yieldWindows}
          fade={captionFadeSeconds}
          palette={palette}
        />
      ) : null}

      {broll.map((slide, i) => {
        const from = Math.round(slide.start * fps);
        const durationInFrames = Math.max(1, Math.round((slide.end - slide.start) * fps));
        return (
          <Sequence key={i} from={from} durationInFrames={durationInFrames}>
            <BRoll slide={slide} durationInFrames={durationInFrames} palette={palette} />
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
