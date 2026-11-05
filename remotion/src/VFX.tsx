import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { VFXEvent } from "./types";
import { isHexColor, isPositiveDurationMs } from "./vfx-validation";

type OverlayProps = {
  durationMs: number;
  color: string;
  intensity: number;
};

// The parent Sequence schedules overlays, so their frame clock starts at zero.
export const Glare: React.FC<OverlayProps> = ({ durationMs, color, intensity }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  if (!isPositiveDurationMs(durationMs) || !isHexColor(color)) return null;
  const durationSeconds = durationMs / 1000;
  const progress = frame / fps / durationSeconds;
  if (progress < 0 || progress > 1) return null;

  // Pixel translation keeps the existing viewport-relative sweep without layout animation.
  const offsetX = interpolate(progress, [0, 1], [-0.4 * width, 1.4 * width]);
  const opacity = Math.sin(progress * Math.PI) * 0.7 * intensity;
  const skewDeg = -18;

  return (
    <AbsoluteFill style={{ pointerEvents: "none", overflow: "hidden",
                           mixBlendMode: "screen" }}>
      <div style={{
        position: "absolute",
        top: "-10%",
        left: 0,
        width: "32%",
        height: "120%",
        background: `linear-gradient(90deg, ` +
                    `transparent 0%, ` +
                    `${hexWithAlpha(color, 0.25)} 25%, ` +
                    `${hexWithAlpha(color, 0.95)} 50%, ` +
                    `${hexWithAlpha(color, 0.25)} 75%, ` +
                    `transparent 100%)`,
        transform: `translateX(${offsetX}px) skewX(${skewDeg}deg)`,
        filter: "blur(18px)",
        opacity,
      }} />
    </AbsoluteFill>
  );
};

export const Flash: React.FC<OverlayProps> = ({ durationMs, color, intensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!isPositiveDurationMs(durationMs) || !isHexColor(color)) return null;
  const durationSeconds = durationMs / 1000;
  const progress = frame / fps / durationSeconds;
  if (progress < 0 || progress > 1) return null;

  const envelope =
    progress < 0.35
      ? interpolate(progress, [0, 0.35], [0, 1])
      : interpolate(progress, [0.35, 1], [1, 0]);
  const opacity = envelope * 0.42 * intensity;

  return (
    <AbsoluteFill style={{
      backgroundColor: color,
      opacity,
      pointerEvents: "none",
      mixBlendMode: "screen",
    }} />
  );
};

/** Use the maximum active bump so simultaneous hits do not compound. */
export function useZoomPunchScale(events: VFXEvent[]): number {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  let bump = 0;
  for (const event of events) {
    if (event.effect !== "zoom-punch" || !isPositiveDurationMs(event.durationMs)) continue;
    const durationSeconds = event.durationMs / 1000;
    const progress = (t - event.t) / durationSeconds;
    if (progress < 0 || progress > 1) continue;
    const eventBump = Math.sin(progress * Math.PI) * 0.04 * event.intensity;
    if (eventBump > bump) bump = eventBump;
  }
  return 1 + bump;
}

function hexWithAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
