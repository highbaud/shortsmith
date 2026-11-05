import React, { useEffect, useState } from "react";
import {
  AbsoluteFill,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  delayRender,
  continueRender,
} from "remotion";
// @remotion/media's <Video> supersedes core's <OffthreadVideo> as of 4.0.491 and
// is the recommended tag for new work. Same frame-accurate extraction, without
// the per-frame headless-Chrome screenshot round-trip OffthreadVideo relies on.
import { Video } from "@remotion/media";
import { loadFont } from "@remotion/google-fonts/Anton";
import { BRoll, isLogoBadge } from "./BRoll";
import { Captions, SpeakerLabels } from "./Captions";
import { Flash, Glare, useZoomPunchScale } from "./VFX";
import type { ShortProps, TimeWindow } from "./types";
import { defaultShortProps } from "./types";
import { isPositiveCaptionWordLimit } from "./caption-timing";
import { isRenderableSlide } from "./broll-validation";
import { ambientPunchScale, sequenceTiming } from "./timeline";
import { isVFXEvent } from "./vfx-validation";
import { useVisualInputs } from "./visual-inputs";

export const Short: React.FC<ShortProps> = (props) => {
  const {
    baseVideo,
    captionsEnabled,
    words,
    captionBand,
    captionMaxWords,
    captionFadeSeconds,
    overlayWindows,
    broll,
    palette,
    vfxEvents = [],
    ambientPunches = [],
    speakerLabels = [],
    speakerPanels = [],
    logoBadgeAnchor,
  } = props;
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // Recover at the composition boundary; chunkWords stays strict for direct callers.
  const validWordLimit = isPositiveCaptionWordLimit(captionMaxWords);
  const maxCaptionWords = validWordLimit ? captionMaxWords : defaultShortProps.captionMaxWords;
  useEffect(() => {
    if (captionsEnabled && !validWordLimit) console.warn(
      `Invalid captionMaxWords ${String(captionMaxWords)}; using default ${maxCaptionWords}`,
    );
  }, [captionMaxWords, captionsEnabled, validWordLimit, maxCaptionWords]);
  // Delay each mount until the lazily started font load finishes, including failures.
  const [fontHandle] = useState(() => delayRender("Loading Anton font"));
  useEffect(() => {
    Promise.resolve()
      .then(() => loadFont().waitUntilDone())
      .catch((error: unknown) => console.warn(
        `Anton font could not load; using fallback text: ${error instanceof Error ? error.message : String(error)}`,
        error,
      ))
      .finally(() => continueRender(fontHandle));
  }, [fontHandle]);

  const slides = useVisualInputs("b-roll", broll, isRenderableSlide);
  const brollWindows: TimeWindow[] = slides
    .filter((slide) => !isLogoBadge(slide))
    .map((s) => ({ start: s.start, end: s.end }));
  const yieldWindows = [...overlayWindows, ...brollWindows];

  // Zoom-punch scales the base video container. Multiple overlapping punches
  // take the max (not the sum) so stacked hooks don't compound.
  const effects = useVisualInputs("VFX", vfxEvents, isVFXEvent);
  const zoomScale = useZoomPunchScale(effects);
  // Ambient punch-ins reset attention in dead talking-head stretches. Composed
  // multiplicatively with the semantic zoom-punch; both rest at 1.0.
  const ambientScale = ambientPunchScale(frame / fps, ambientPunches);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <AbsoluteFill style={{
        transform: `scale(${zoomScale * ambientScale})`,
        transformOrigin: "center 40%",  // anchor at face zone (face_target_y=0.40)
      }}>
        <Video src={staticFile(baseVideo)} />
      </AbsoluteFill>

      {captionsEnabled ? (
        <Captions
          words={words}
          maxWords={maxCaptionWords}
          band={captionBand}
          yieldWindows={yieldWindows}
          fade={captionFadeSeconds}
          palette={palette}
          fontSize={props.captionFontSize ?? 96}
        />
      ) : null}

      {speakerLabels.length > 0 ? (
        <SpeakerLabels labels={speakerLabels} band={captionBand}
                       panels={speakerPanels} palette={palette} />
      ) : null}

      {slides.map((slide, i) => {
        const { from, durationInFrames } = sequenceTiming(slide.start, slide.end - slide.start, fps);
        return (
          <Sequence key={i} from={from} durationInFrames={durationInFrames}>
            <BRoll slide={slide} durationInFrames={durationInFrames} palette={palette}
                   badgeAnchor={logoBadgeAnchor} />
          </Sequence>
        );
      })}

      {/* VFX overlays (glare + flash) — rendered last so they sit on top of
          everything else. zoom-punch is applied at the base layer above; not
          repeated here. */}
      {effects.map((ev, i) => {
        const { from, durationInFrames } = sequenceTiming(ev.t, ev.durationMs / 1000, fps);
        if (ev.effect === "glare") {
          return (
            <Sequence key={`v${i}`} from={from} durationInFrames={durationInFrames}>
              <Glare durationMs={ev.durationMs}
                     color={ev.color} intensity={ev.intensity} />
            </Sequence>
          );
        }
        if (ev.effect === "flash") {
          return (
            <Sequence key={`v${i}`} from={from} durationInFrames={durationInFrames}>
              <Flash durationMs={ev.durationMs}
                     color={ev.color} intensity={ev.intensity} />
            </Sequence>
          );
        }
        return null;  // zoom-punch handled by useZoomPunchScale above
      })}
    </AbsoluteFill>
  );
};
