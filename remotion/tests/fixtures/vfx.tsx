import React from "react";
import { AbsoluteFill, Composition, registerRoot, Sequence } from "remotion";
import { Flash, Glare, useZoomPunchScale } from "../../src/VFX";
import { BRoll } from "../../src/BRoll";
import { RemotionRoot } from "../../src/Root";
import type { BRollSlide, VFXEvent } from "../../src/types";
import { getFontLoadCount } from "./font";

const fontLoadsAtImport = getFontLoadCount();
const ImportAudit: React.FC<{ fontLoadsAtImport: number }> = () => null;

type Props = { effect: "glare" | "flash"; startFrame: number; durationMs?: number };

const Effects: React.FC<Props> = ({ effect, startFrame, durationMs = 300 }) => (
  <AbsoluteFill style={{ backgroundColor: "#000000" }}>
    <Sequence from={startFrame} durationInFrames={9}>
      {effect === "glare" ? (
        <Glare durationMs={durationMs} color="#ffffff" intensity={1} />
      ) : (
        <Flash durationMs={durationMs} color="#ffffff" intensity={1} />
      )}
    </Sequence>
  </AbsoluteFill>
);

const Zoom: React.FC<{ events: VFXEvent[] }> = ({ events }) => {
  const scale = useZoomPunchScale(events);
  return <AbsoluteFill style={{ backgroundColor: "black", justifyContent: "center", alignItems: "center" }}>
    <div style={{ width: 120, height: 90, backgroundColor: "white", transform: `scale(${scale})` }} />
  </AbsoluteFill>;
};

const palette = { primary: "#e81e63", secondary: "#0471a6", accent: "#10a37f", bg: "#07121c" };
const slides: BRollSlide[] = [
  { type: "text", title: "A title", eyebrow: "NEWS", start: 0, end: 3 },
  { type: "stat", value: "42%", label: "A stat", start: 0, end: 3 },
  { type: "list", items: ["First", "Second"], start: 0, end: 3 },
  { type: "logo", src: "card.svg", monochrome: true, name: "Logo", start: 0, end: 3 },
  { type: "person", src: "card.svg", name: "Person", role: "Speaker", start: 0, end: 3 },
  { type: "image", src: "card.svg", start: 0, end: 3 },
];
const Cards: React.FC<{ variant: number; color?: string; badge?: boolean }> = ({ variant, color, badge }) => {
  const slide = { ...slides[variant], color, ...(badge ? { mode: "badge" } : {}) } as BRollSlide;
  return <BRoll slide={slide} durationInFrames={90} palette={palette} />;
};

registerRoot(() => <>
  <Composition id="ImportAudit" component={ImportAudit} width={32} height={32} fps={30}
    durationInFrames={1} defaultProps={{ fontLoadsAtImport }} />
  <Composition id="VFX" component={Effects} width={320} height={180} fps={30}
    durationInFrames={90} defaultProps={{ effect: "glare", startFrame: 60 }} />
  <Composition id="Zoom" component={Zoom} width={320} height={180} fps={30}
    durationInFrames={90} defaultProps={{ events: [] }} />
  <Composition id="Cards" component={Cards} width={1080} height={1920} fps={30}
    durationInFrames={90} defaultProps={{ variant: 0 }} />
  <RemotionRoot />
</>);
