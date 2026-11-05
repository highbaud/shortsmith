import { interpolate } from "remotion";
import type { TimeWindow, Word } from "./types";

export type CaptionChunk = { words: Word[]; start: number; end: number };

export function isPositiveCaptionWordLimit(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

export function chunkWords(words: Word[], maxWords: number): CaptionChunk[] {
  if (!isPositiveCaptionWordLimit(maxWords)) {
    throw new RangeError("captionMaxWords must be a positive integer");
  }
  const chunks: CaptionChunk[] = [];
  for (let i = 0; i < words.length; i += maxWords) {
    const slice = words.slice(i, i + maxWords);
    chunks.push({ words: slice, start: slice[0].start, end: slice[slice.length - 1].end });
  }
  return chunks;
}

export function activeCaptionChunk(t: number, chunks: CaptionChunk[]): CaptionChunk | null {
  if (!chunks.length || t > chunks[chunks.length - 1].end + 0.25) return null;
  let active: CaptionChunk | null = null;
  for (const chunk of chunks) {
    if (t >= chunk.start - 0.05 && t <= chunk.end + 0.25) return chunk;
    // Hold the previous chunk between phrases, but never after the final hold.
    if (t > chunk.end) active = chunk;
  }
  return active;
}

export function yieldOpacity(t: number, windows: TimeWindow[], fade: number): number {
  let opacity = 1;
  for (const window of windows) {
    if (t <= window.start - fade || t >= window.end + fade) continue;
    const local = t < window.start
      ? interpolate(t, [window.start - fade, window.start], [1, 0])
      : t > window.end
        ? interpolate(t, [window.end, window.end + fade], [0, 1])
        : 0;
    opacity = Math.min(opacity, local);
  }
  return opacity;
}
