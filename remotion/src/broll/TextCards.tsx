import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { TextSlide, StatSlide, ListSlide } from "../types";
import { FONT, SlideShell } from "./SlideShell";

const Eyebrow: React.FC<{ text: string; color: string }> = ({ text, color }) => (
  <div
    style={{
      fontFamily: FONT,
      fontWeight: 800,
      letterSpacing: 8,
      fontSize: 36,
      color,
      marginBottom: 30,
      textTransform: "uppercase",
    }}
  >
    {text}
  </div>
);

export const TextCard: React.FC<{ slide: TextSlide; durationInFrames: number }> = ({ slide, durationInFrames }) => {
  const color = slide.color ?? "#f5c542";
  return (
    <SlideShell background={slide.background} durationInFrames={durationInFrames}>
      {slide.eyebrow ? <Eyebrow text={slide.eyebrow} color={color} /> : null}
      <div
        style={{
          fontFamily: FONT,
          fontWeight: 900,
          fontSize: 108,
          lineHeight: 1.05,
          color: "#fff",
          whiteSpace: "pre-line",
        }}
      >
        {slide.title}
      </div>
      {slide.subtitle ? (
        <div
          style={{
            fontFamily: FONT,
            fontWeight: 600,
            fontSize: 52,
            lineHeight: 1.2,
            color: "rgba(255,255,255,0.78)",
            marginTop: 36,
            whiteSpace: "pre-line",
          }}
        >
          {slide.subtitle}
        </div>
      ) : null}
    </SlideShell>
  );
};

export const StatCard: React.FC<{ slide: StatSlide; durationInFrames: number }> = ({ slide, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const color = slide.color ?? "#34c759";

  let valueText = slide.value;
  if (typeof slide.to === "number") {
    const from = slide.from ?? 0;
    const progress = spring({ frame, fps, durationInFrames: Math.round(fps * 1.0), config: { damping: 200 } });
    const n = Math.round(interpolate(progress, [0, 1], [from, slide.to]));
    valueText = `${slide.prefix ?? ""}${n.toLocaleString()}${slide.suffix ?? ""}`;
  }

  return (
    <SlideShell background={slide.background} durationInFrames={durationInFrames}>
      <div
        style={{
          fontFamily: FONT,
          fontWeight: 900,
          fontSize: 230,
          lineHeight: 1,
          color,
          textShadow: `0 0 60px ${color}55`,
        }}
      >
        {valueText}
      </div>
      {slide.label ? (
        <div
          style={{
            fontFamily: FONT,
            fontWeight: 700,
            fontSize: 56,
            color: "#fff",
            marginTop: 30,
            whiteSpace: "pre-line",
          }}
        >
          {slide.label}
        </div>
      ) : null}
    </SlideShell>
  );
};

export const ListCard: React.FC<{ slide: ListSlide; durationInFrames: number }> = ({ slide, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const color = slide.color ?? "#37bdf8";
  return (
    <SlideShell background={slide.background} durationInFrames={durationInFrames}>
      {slide.title ? (
        <div
          style={{
            fontFamily: FONT,
            fontWeight: 900,
            fontSize: 88,
            color: "#fff",
            marginBottom: 56,
            whiteSpace: "pre-line",
          }}
        >
          {slide.title}
        </div>
      ) : null}
      <div style={{ display: "flex", flexDirection: "column", gap: 34, textAlign: "left" }}>
        {slide.items.map((item, i) => {
          const appear = spring({
            frame: frame - 8 - i * 7,
            fps,
            config: { damping: 18 },
          });
          return (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 26,
                opacity: appear,
                transform: `translateX(${interpolate(appear, [0, 1], [40, 0])}px)`,
              }}
            >
              <span style={{ fontFamily: FONT, fontWeight: 900, fontSize: 64, color }}>
                {i + 1}
              </span>
              <span style={{ fontFamily: FONT, fontWeight: 700, fontSize: 60, color: "#fff" }}>
                {item}
              </span>
            </div>
          );
        })}
      </div>
    </SlideShell>
  );
};
