import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {
  BRollSlide,
  ListSlide,
  LogoSlide,
  Palette,
  PersonSlide,
  StatSlide,
  TextSlide,
} from "./types";

const DEFAULT_BG = "linear-gradient(160deg, #0f2033 0%, #0a1828 55%, #07121c 100%)";
const FONT = '"Inter", "Helvetica Neue", Arial, sans-serif';

/** Fade/scale wrapper shared by all slides. `dur` is the slide length in frames. */
const SlideShell: React.FC<{
  background?: string;
  dur: number;
  children: React.ReactNode;
}> = ({ background, dur, children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 18, mass: 0.7 } });
  const inFade = interpolate(frame, [0, 6], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const outFade = interpolate(frame, [dur - 7, dur], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
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

const TextCard: React.FC<{ slide: TextSlide; dur: number }> = ({ slide, dur }) => {
  const color = slide.color ?? "#f5c542";
  return (
    <SlideShell background={slide.background} dur={dur}>
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

const StatCard: React.FC<{ slide: StatSlide; dur: number }> = ({ slide, dur }) => {
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
    <SlideShell background={slide.background} dur={dur}>
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

const ListCard: React.FC<{ slide: ListSlide; dur: number }> = ({ slide, dur }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const color = slide.color ?? "#37bdf8";
  return (
    <SlideShell background={slide.background} dur={dur}>
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
      <Img src={src} style={{ width: size * 0.7, height: size * 0.7, objectFit: "contain" }} />
    </div>
  );

/** Compact rounded card that pops into the upper area over the live base video.
 *  Stays clear of the caption band (0.6–0.8 height) and only plays inside free
 *  gaps, so no Hyperframes overlay is on screen to collide with. */
const LogoBadge: React.FC<{ slide: LogoSlide; dur: number }> = ({ slide, dur }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const color = slide.color ?? "#ffffff";
  const src = staticFile(slide.src);
  const pop = spring({ frame, fps, config: { damping: 16, mass: 0.7, stiffness: 130 } });
  const outFade = interpolate(frame, [dur - 7, dur], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const enter = interpolate(pop, [0, 1], [0, 1]);

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

const LogoCard: React.FC<{ slide: LogoSlide; dur: number }> = ({ slide, dur }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const color = slide.color ?? "#ffffff";
  const pop = spring({ frame, fps, config: { damping: 14, mass: 0.8, stiffness: 120 } });
  const src = staticFile(slide.src);

  if (slide.mode === "badge") {
    return <LogoBadge slide={slide} dur={dur} />;
  }

  const mark = (
    <LogoMark src={src} monochrome={slide.monochrome} color={color} size={slide.monochrome ? 520 : 560} />
  );

  return (
    <SlideShell background={slide.background} dur={dur}>
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

const PersonCard: React.FC<{ slide: PersonSlide; dur: number }> = ({ slide, dur }) => {
  const frame = useCurrentFrame();
  const color = slide.color ?? "#f5c542";
  const motion = slide.motion ?? "in";

  // Ken Burns: ease the transform linearly across the whole slide.
  const p = interpolate(frame, [0, dur], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scale = motion === "out" ? interpolate(p, [0, 1], [1.18, 1.0]) : interpolate(p, [0, 1], [1.0, 1.18]);
  let tx = 0;
  let ty = 0;
  const pan = interpolate(p, [0, 1], [-4, 4]); // percent
  if (motion === "left") tx = -pan;
  else if (motion === "right") tx = pan;
  else if (motion === "up") ty = -pan;
  else if (motion === "down") ty = pan;

  const inFade = interpolate(frame, [0, 6], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const outFade = interpolate(frame, [dur - 7, dur], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: "#000", opacity: Math.min(inFade, outFade) }}>
      <AbsoluteFill style={{ overflow: "hidden" }}>
        <Img
          src={staticFile(slide.src)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: `scale(${scale}) translate(${tx}%, ${ty}%)`,
          }}
        />
      </AbsoluteFill>
      {/* Bottom gradient scrim for the lower-third legibility. */}
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

/** Default accent per slide type, pulled from the short's palette so b-roll
 *  color-matches the Hyperframes overlays. An explicit slide.color always wins. */
function paletteColorFor(type: BRollSlide["type"], palette: Palette): string {
  switch (type) {
    case "text":
      return palette.primary;
    case "stat":
      return palette.accent;
    case "list":
      return palette.secondary;
    case "logo":
      return palette.primary;
    case "person":
      return palette.primary;
  }
}

export const BRoll: React.FC<{
  slide: BRollSlide;
  durationInFrames: number;
  palette: Palette;
}> = ({ slide, durationInFrames, palette }) => {
  const withColor = {
    ...slide,
    color: slide.color ?? paletteColorFor(slide.type, palette),
  } as BRollSlide;
  switch (withColor.type) {
    case "text":
      return <TextCard slide={withColor} dur={durationInFrames} />;
    case "stat":
      return <StatCard slide={withColor} dur={durationInFrames} />;
    case "list":
      return <ListCard slide={withColor} dur={durationInFrames} />;
    case "logo":
      return <LogoCard slide={withColor} dur={durationInFrames} />;
    case "person":
      return <PersonCard slide={withColor} dur={durationInFrames} />;
  }
};
