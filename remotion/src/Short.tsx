import React, { useMemo } from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
} from "remotion";
import { BRoll } from "./BRoll";
import { CaptionBand, ShortProps, TimeWindow, Word } from "./types";

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
}> = ({ words, maxWords, band, yieldWindows, fade }) => {
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
            gap: "0 18px",
            transform: `scale(${interpolate(appear, [0, 1], [0.92, 1])})`,
            opacity: appear,
          }}
        >
          {active.words.map((w, i) => {
            const spoken = t >= w.start - 0.02;
            return (
              <span
                key={i}
                style={{
                  fontFamily: '"Inter", "Helvetica Neue", Arial, sans-serif',
                  fontWeight: 800,
                  fontSize: 92,
                  lineHeight: 1.08,
                  color: spoken ? "#ffffff" : "rgba(255,255,255,0.55)",
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

export const Short: React.FC<ShortProps> = ({
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
}) => {
  // Captions yield during full-frame b-roll cutaways (a slide that covers the
  // frame would otherwise have karaoke text on top of it). Logo *badges* are a
  // small upper-area overlay that leaves the base video and captions visible,
  // so they do NOT suppress captions.
  const isBadge = (s: typeof broll[number]) => s.type === "logo" && s.mode === "badge";
  const brollWindows: TimeWindow[] = broll
    .filter((s) => !isBadge(s))
    .map((s) => ({ start: s.start, end: s.end }));
  const yieldWindows = [...overlayWindows, ...brollWindows];

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <OffthreadVideo src={staticFile(baseVideo)} />

      {captionsEnabled ? (
        <Captions
          words={words}
          maxWords={captionMaxWords}
          band={captionBand}
          yieldWindows={yieldWindows}
          fade={captionFadeSeconds}
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
    </AbsoluteFill>
  );
};
