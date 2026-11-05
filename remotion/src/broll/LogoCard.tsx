import React from "react";
import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { LogoSlide, BadgeAnchor } from "../types";
import { FONT, PREMOUNT_FRAMES, SlideShell, fadeOut } from "./SlideShell";

/** The logo mark itself, sized to `size` px. Monochrome silhouettes are tinted
 *  to `color`; full-color marks sit on a white rounded tile so dark/brand-colored
 *  logos stay visible. */
const LogoMark: React.FC<{
  src: string;
  monochrome?: boolean;
  color: string;
  size: number;
}> = ({ src, monochrome, color, size }) =>
  monochrome ? (
    <div
      style={{
        width: size,
        height: size,
        backgroundColor: color,
        WebkitMaskImage: `url(${src})`,
        maskImage: `url(${src})`,
        WebkitMaskRepeat: "no-repeat",
        maskRepeat: "no-repeat",
        WebkitMaskPosition: "center",
        maskPosition: "center",
        WebkitMaskSize: "contain",
        maskSize: "contain",
      }}
    />
  ) : (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: size * 0.18,
        backgroundColor: "#ffffff",
        boxShadow: "0 12px 40px rgba(0,0,0,0.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Img
        src={src}
        premountFor={PREMOUNT_FRAMES}
        style={{ width: size * 0.7, height: size * 0.7, objectFit: "contain" }}
      />
    </div>
  );

/** Compact rounded card that pops into the upper area over the live base video.
 *  Stays clear of the caption band (0.6-0.8 height) and only plays inside free
 *  gaps, so no Hyperframes overlay is on screen to collide with. */
export const LogoBadge: React.FC<{ slide: LogoSlide; durationInFrames: number; anchor?: BadgeAnchor }> = ({
  slide,
  durationInFrames,
  anchor,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const color = slide.color ?? "#ffffff";
  const src = staticFile(slide.src);
  const pop = spring({ frame, fps, config: { damping: 16, mass: 0.7, stiffness: 130 } });
  const outFade = fadeOut(frame, durationInFrames);
  const enter = interpolate(pop, [0, 1], [0, 1]);

  if (anchor) {
    // Split-stack: a mark-only tile beside the top speaker's square. There is
    // no room for the name there; the captions carry it.
    return (
      <div
        style={{
          position: "absolute",
          left: anchor.x,
          top: anchor.y,
          width: anchor.size,
          height: anchor.size,
          padding: 6,
          boxSizing: "border-box",
          borderRadius: anchor.size * 0.22,
          background: "rgba(10,18,28,0.82)",
          border: `2px solid ${color}`,
          boxShadow: "0 18px 50px rgba(0,0,0,0.5)",
          opacity: Math.min(enter, outFade),
          transform: `translateY(${interpolate(pop, [0, 1], [-30, 0])}px) scale(${interpolate(
            pop,
            [0, 1],
            [0.8, 1]
          )})`,
        }}
      >
        <LogoMark src={src} monochrome={slide.monochrome} color={color} size={anchor.size - 16} />
      </div>
    );
  }

  return (
    <AbsoluteFill style={{ justifyContent: "flex-start", alignItems: "center" }}>
      <div
        style={{
          marginTop: "11%",
          display: "flex",
          alignItems: "center",
          gap: 28,
          padding: "24px 40px 24px 24px",
          borderRadius: 36,
          background: "rgba(10,18,28,0.82)",
          border: `2px solid ${color}`,
          boxShadow: "0 24px 70px rgba(0,0,0,0.5)",
          opacity: Math.min(enter, outFade),
          transform: `translateY(${interpolate(pop, [0, 1], [-40, 0])}px) scale(${interpolate(
            pop,
            [0, 1],
            [0.8, 1]
          )})`,
        }}
      >
        <LogoMark src={src} monochrome={slide.monochrome} color={color} size={132} />
        {slide.name ? (
          <div style={{ display: "flex", flexDirection: "column", textAlign: "left" }}>
            <div style={{ fontFamily: FONT, fontWeight: 900, fontSize: 60, color: "#fff" }}>
              {slide.name}
            </div>
            {slide.caption ? (
              <div
                style={{
                  fontFamily: FONT,
                  fontWeight: 600,
                  fontSize: 34,
                  color: "rgba(255,255,255,0.72)",
                  marginTop: 4,
                }}
              >
                {slide.caption}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

export const LogoCard: React.FC<{ slide: LogoSlide; durationInFrames: number }> = ({
  slide,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const color = slide.color ?? "#ffffff";
  const pop = spring({ frame, fps, config: { damping: 14, mass: 0.8, stiffness: 120 } });
  const src = staticFile(slide.src);

  const mark = (
    <LogoMark src={src} monochrome={slide.monochrome} color={color} size={slide.monochrome ? 520 : 560} />
  );

  return (
    <SlideShell background={slide.background} durationInFrames={durationInFrames}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          transform: `scale(${interpolate(pop, [0, 1], [0.4, 1])})`,
          opacity: interpolate(pop, [0, 0.6], [0, 1], { extrapolateRight: "clamp" }),
        }}
      >
        {mark}
        {slide.name ? (
          <div
            style={{
              fontFamily: FONT,
              fontWeight: 900,
              fontSize: 96,
              color: "#fff",
              marginTop: 50,
            }}
          >
            {slide.name}
          </div>
        ) : null}
        {slide.caption ? (
          <div
            style={{
              fontFamily: FONT,
              fontWeight: 600,
              fontSize: 46,
              color: "rgba(255,255,255,0.72)",
              marginTop: 18,
            }}
          >
            {slide.caption}
          </div>
        ) : null}
      </div>
    </SlideShell>
  );
};
