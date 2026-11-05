import React, { useMemo } from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { fontFamily as ANTON } from "@remotion/google-fonts/Anton";
import type { CaptionBand, Palette, PanelRect, SpeakerLabel, TimeWindow, Word } from "./types";
import { activeCaptionChunk, chunkWords, yieldOpacity } from "./caption-timing";

export const Captions: React.FC<{
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

  const captionOpacity = yieldOpacity(t, yieldWindows, fade);
  if (captionOpacity <= 0) return null;

  const active = activeCaptionChunk(t, chunks);
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
    <AbsoluteFill style={{ opacity: captionOpacity }}>
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
export const SpeakerLabels: React.FC<{
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
