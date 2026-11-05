import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

export const DEFAULT_BG = "linear-gradient(160deg, #0f2033 0%, #0a1828 55%, #07121c 100%)";
export const FONT = '"Inter", "Helvetica Neue", Arial, sans-serif';
// Mount b-roll images this many frames before their Sequence starts so they are
// decoded by the time the cutaway is on screen. ~0.5s at 30fps; premounted
// frames render nothing, they just warm the asset.
export const PREMOUNT_FRAMES = 15;

// Every slide shares the same fade: 6 frames in at the head, 7 frames out at
// the tail. `durationInFrames` is the slide length in frames.
const CLAMP = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
export const fadeIn = (frame: number): number => interpolate(frame, [0, 6], [0, 1], CLAMP);
export const fadeOut = (frame: number, durationInFrames: number): number =>
  interpolate(frame, [durationInFrames - 7, durationInFrames], [1, 0], CLAMP);

export const SlideShell: React.FC<{
  background?: string;
  durationInFrames: number;
  children: React.ReactNode;
}> = ({ background, durationInFrames, children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 18, mass: 0.7 } });
  const inFade = fadeIn(frame);
  const outFade = fadeOut(frame, durationInFrames);
  return (
    <AbsoluteFill
      style={{
        background: background ?? DEFAULT_BG,
        opacity: Math.min(inFade, outFade),
        justifyContent: "center",
        alignItems: "center",
        padding: "0 90px",
      }}
    >
      <div
        style={{
          transform: `scale(${interpolate(enter, [0, 1], [0.92, 1])})`,
          textAlign: "center",
          width: "100%",
        }}
      >
        {children}
      </div>
    </AbsoluteFill>
  );
};
