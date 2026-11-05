import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import type { PersonSlide, ImageSlide } from "../types";
import { DEFAULT_BG, FONT, PREMOUNT_FRAMES, fadeIn, fadeOut } from "./SlideShell";

export const PersonCard: React.FC<{ slide: PersonSlide; durationInFrames: number }> = ({ slide, durationInFrames }) => {
  const frame = useCurrentFrame();
  const color = slide.color ?? "#f5c542";
  const motion = slide.motion ?? "in";

  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scale = motion === "out" ? interpolate(progress, [0, 1], [1.18, 1.0]) : interpolate(progress, [0, 1], [1.0, 1.18]);
  let translateXPercent = 0;
  let translateYPercent = 0;
  const panPercent = interpolate(progress, [0, 1], [-4, 4]);
  if (motion === "left") translateXPercent = -panPercent;
  else if (motion === "right") translateXPercent = panPercent;
  else if (motion === "up") translateYPercent = -panPercent;
  else if (motion === "down") translateYPercent = panPercent;

  const inFade = fadeIn(frame);
  const outFade = fadeOut(frame, durationInFrames);

  return (
    <AbsoluteFill style={{ background: "#000", opacity: Math.min(inFade, outFade) }}>
      <AbsoluteFill style={{ overflow: "hidden" }}>
        <Img
          src={staticFile(slide.src)}
          // Decode ahead of the cutaway's first frame. Without this the photo
          // pops in a frame or two late, which reads as a stutter at the cut.
          premountFor={PREMOUNT_FRAMES}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: `scale(${scale}) translate(${translateXPercent}%, ${translateYPercent}%)`,
          }}
        />
      </AbsoluteFill>
      <AbsoluteFill
        style={{
          background: "linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0) 38%)",
        }}
      />
      {slide.name ? (
        <div style={{ position: "absolute", left: 80, right: 80, bottom: 150, textAlign: "left" }}>
          <div style={{ width: 90, height: 8, background: color, marginBottom: 28, borderRadius: 4 }} />
          <div style={{ fontFamily: FONT, fontWeight: 900, fontSize: 92, color: "#fff", lineHeight: 1.05 }}>
            {slide.name}
          </div>
          {slide.role ? (
            <div
              style={{
                fontFamily: FONT,
                fontWeight: 600,
                fontSize: 48,
                color: "rgba(255,255,255,0.82)",
                marginTop: 16,
              }}
            >
              {slide.role}
            </div>
          ) : null}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

/** Keep attribution readable without printing a long URL over the source. */
function imageSourceLabel(slide: ImageSlide): string {
  const label = slide.source_label?.trim();
  const url = label && /^(https?:\/\/|www\.)/i.test(label)
    ? label
    : label ? undefined : slide.source_url;
  if (!url) return label || "Source";
  try {
    const parsed = new URL(/^www\./i.test(url) ? `https://${url}` : url);
    return parsed.hostname.replace(/^www\./i, "") || label || "Source";
  } catch {
    return label || "Source";
  }
}

type ImageCardProps = { slide: ImageSlide; durationInFrames: number };

export function ImageCard({ slide, durationInFrames }: ImageCardProps): React.ReactElement {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{
      background: slide.background ?? DEFAULT_BG,
      opacity: Math.min(fadeIn(frame), fadeOut(frame, durationInFrames)),
      padding: "140px 72px 160px",
      boxSizing: "border-box",
      fontFamily: FONT,
      color: "#fff",
    }}>
      {slide.title ? (
        <div style={{
          flexShrink: 0, marginBottom: 32, fontWeight: 900,
          fontSize: 58, lineHeight: 1.12, whiteSpace: "pre-line", overflowWrap: "anywhere",
        }}>
          {slide.title}
        </div>
      ) : null}
      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", borderRadius: 20 }}>
        <Img
          src={staticFile(slide.src)}
          premountFor={PREMOUNT_FRAMES}
          style={{ width: "100%", height: "100%", objectFit: slide.fit ?? "contain" }}
        />
      </div>
      <div style={{
        flexShrink: 0, marginTop: 28, borderLeft: `5px solid ${slide.color}`,
        paddingLeft: 22, fontSize: 32, lineHeight: 1.3, overflowWrap: "anywhere",
      }}>
        <div style={{ fontWeight: 800 }}>{imageSourceLabel(slide)}</div>
        {slide.date ? (
          <div style={{ color: "rgba(255,255,255,0.72)", marginTop: 6 }}>{slide.date}</div>
        ) : null}
        {slide.attribution || slide.license ? (
          <div style={{ color: "rgba(255,255,255,0.72)", fontSize: 28, marginTop: 6 }}>
            {[slide.attribution, slide.license].filter(Boolean).join(" | ")}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
}
