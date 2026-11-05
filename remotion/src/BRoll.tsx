import React from "react";
import type { BRollSlide, LogoSlide, Palette, BadgeAnchor } from "./types";
import { TextCard, StatCard, ListCard } from "./broll/TextCards";
import { LogoBadge, LogoCard } from "./broll/LogoCard";
import { PersonCard, ImageCard } from "./broll/ImageCards";

export function isLogoBadge(slide: BRollSlide): slide is LogoSlide & { mode: "badge" } {
  return slide.type === "logo" && slide.mode === "badge";
}

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
    case "image":
      return palette.secondary;
  }
}

export const BRoll: React.FC<{
  slide: BRollSlide;
  durationInFrames: number;
  palette: Palette;
  /** Split-stack only: where a logo badge goes. */
  badgeAnchor?: BadgeAnchor;
}> = ({ slide, durationInFrames, palette, badgeAnchor }) => {
  const withColor = {
    ...slide,
    color: slide.color ?? paletteColorFor(slide.type, palette),
  } as BRollSlide;
  switch (withColor.type) {
    case "text":
      return <TextCard slide={withColor} durationInFrames={durationInFrames} />;
    case "stat":
      return <StatCard slide={withColor} durationInFrames={durationInFrames} />;
    case "list":
      return <ListCard slide={withColor} durationInFrames={durationInFrames} />;
    case "logo":
      return isLogoBadge(withColor)
        ? <LogoBadge slide={withColor} durationInFrames={durationInFrames} anchor={badgeAnchor} />
        : <LogoCard slide={withColor} durationInFrames={durationInFrames} />;
    case "person":
      return <PersonCard slide={withColor} durationInFrames={durationInFrames} />;
    case "image":
      return <ImageCard slide={withColor} durationInFrames={durationInFrames} />;
  }
};
