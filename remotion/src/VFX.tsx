import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { VFXEvent } from "./types";

/** Capcut-style horizontal light streak. A diagonally-skewed gradient bar
 *  sweeps from left-off-screen to right-off-screen, with a bell-curve opacity
 *  envelope so it eases in and out rather than hard-cutting. */
export const Glare: React.FC<{
  startSec: number;
  durationMs: number;
  color: string;
  intensity: number;
}> = ({ startSec, durationMs, color, intensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const dur = durationMs / 1000;
  const progress = (t - startSec) / dur;
  if (progress < 0 || progress > 1) return null;

  // Sweep across: bar's left edge moves from -40% to 140% of viewport.
  const leftPct = interpolate(progress, [0, 1], [-40, 140]);
  // Bell curve opacity — sin(0→π) peaks at midpoint of sweep.
  const opacity = Math.sin(progress * Math.PI) * 0.7 * intensity;
  // Slight diagonal skew for cinematic glare feel.
  const skewDeg = -18;

  return (
    <AbsoluteFill style={{ pointerEvents: "none", overflow: "hidden",
                           mixBlendMode: "screen" }}>
      <div style={{
        position: "absolute",
        top: "-10%",
        left: `${leftPct}%`,
        width: "32%",
        height: "120%",
        background: `linear-gradient(90deg, ` +
                    `transparent 0%, ` +
                    `${hexWithAlpha(color, 0.25)} 25%, ` +
                    `${hexWithAlpha(color, 0.95)} 50%, ` +
                    `${hexWithAlpha(color, 0.25)} 75%, ` +
                    `transparent 100%)`,
        transform: `skewX(${skewDeg}deg)`,
        filter: "blur(18px)",
        opacity,
      }} />
    </AbsoluteFill>
  );
};

/** Brief full-frame color flash — punctuates the moment. Very short
 *  (~80-100ms) so it reads as an accent rather than a wash. Uses screen
 *  blend mode so colored flashes (gold / red) tint highlights but don't
 *  black out darker areas. */
export const Flash: React.FC<{
  startSec: number;
  durationMs: number;
  color: string;
  intensity: number;
}> = ({ startSec, durationMs, color, intensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const dur = durationMs / 1000;
  const progress = (t - startSec) / dur;
  if (progress < 0 || progress > 1) return null;

  // Front-loaded curve — peaks fast (35% in), trails off. Reads as a snap.
  const env =
    progress < 0.35
      ? interpolate(progress, [0, 0.35], [0, 1])
      : interpolate(progress, [0.35, 1], [1, 0]);
  const opacity = env * 0.42 * intensity;

  return (
    <AbsoluteFill style={{
      backgroundColor: color,
      opacity,
      pointerEvents: "none",
      mixBlendMode: "screen",
    }} />
  );
};

/** Compute a combined scale factor from all active zoom-punch events at the
 *  current frame. Multiple overlapping punches take the MAX bump rather than
 *  summing, so 3 hooks at t=0 don't compound into a 12% zoom. */
export function useZoomPunchScale(events: VFXEvent[]): number {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  let bump = 0;
  for (const ev of events) {
    if (ev.effect !== "zoom-punch") continue;
    const dur = ev.durationMs / 1000;
    const progress = (t - ev.t) / dur;
    if (progress < 0 || progress > 1) continue;
    // Bell-curve scale bump — 0 → max → 0. Max = 4% by default.
    const here = Math.sin(progress * Math.PI) * 0.04 * ev.intensity;
    if (here > bump) bump = here;
  }
  return 1 + bump;
}

/** Convert "#rrggbb" -> "rgba(r,g,b,a)" so we can vary alpha inside gradients
 *  without precomputing every stop. */
function hexWithAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
