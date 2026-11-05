import { interpolate } from "remotion";
import type { VideoConfig } from "remotion";
import type { ShortProps } from "./types";

type SequenceTiming = { from: number; durationInFrames: number };
type CompositionMetadata = Pick<VideoConfig, "durationInFrames" | "fps" | "width" | "height">;

export function sequenceTiming(startSeconds: number, durationSeconds: number, fps: number): SequenceTiming {
  return {
    from: Math.round(startSeconds * fps),
    durationInFrames: Math.max(1, Math.round(durationSeconds * fps)),
  };
}

export function compositionMetadata(props: Pick<ShortProps, "fps" | "durationInSeconds">): CompositionMetadata {
  const fps = props.fps || 30;
  return {
    durationInFrames: Math.max(1, Math.ceil(props.durationInSeconds * fps)),
    fps,
    width: 1080,
    height: 1920,
  };
}

export function ambientPunchScale(t: number, punches: number[], peak = 1.045): number {
  let scale = 1;
  for (const punch of punches) {
    if (t < punch || t > punch + 0.9) continue;
    const local = t <= punch + 0.28
      ? interpolate(t, [punch, punch + 0.28], [1, peak], {
          extrapolateLeft: "clamp", extrapolateRight: "clamp",
        })
      : interpolate(t, [punch + 0.28, punch + 0.9], [peak, 1], {
          extrapolateLeft: "clamp", extrapolateRight: "clamp",
        });
    scale = Math.max(scale, local);
  }
  return scale;
}
