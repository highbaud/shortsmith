import type { BRollSlide } from "./types";

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const isNonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

function optionalStrings(slide: Record<string, unknown>, fields: string[]): boolean {
  return fields.every((field) => slide[field] === undefined || typeof slide[field] === "string");
}

function isValidTextSlide(slide: Record<string, unknown>): boolean {
  return isNonEmptyString(slide.title) && optionalStrings(slide, ["subtitle", "eyebrow"]);
}

function isValidStatSlide(slide: Record<string, unknown>): boolean {
  return (isNonEmptyString(slide.value) || isFiniteNumber(slide.to))
    && (slide.value === undefined || typeof slide.value === "string")
    && (slide.to === undefined || isFiniteNumber(slide.to))
    && (slide.from === undefined || isFiniteNumber(slide.from))
    && optionalStrings(slide, ["prefix", "suffix", "label"]);
}

function isValidListSlide(slide: Record<string, unknown>): boolean {
  return Array.isArray(slide.items) && slide.items.length > 0 && slide.items.every(isNonEmptyString)
    && optionalStrings(slide, ["title"]);
}

function isValidLogoSlide(slide: Record<string, unknown>): boolean {
  return isNonEmptyString(slide.src) && optionalStrings(slide, ["name", "caption"])
    && (slide.monochrome === undefined || typeof slide.monochrome === "boolean")
    && (slide.mode === undefined || slide.mode === "full" || slide.mode === "badge");
}

function isValidPersonSlide(slide: Record<string, unknown>): boolean {
  return isNonEmptyString(slide.src) && optionalStrings(slide, ["name", "role"])
    && (slide.motion === undefined || (typeof slide.motion === "string"
      && ["in", "out", "left", "right", "up", "down"].includes(slide.motion)));
}

function isValidImageSlide(slide: Record<string, unknown>): boolean {
  return isNonEmptyString(slide.src)
    && optionalStrings(slide, ["title", "source_label", "source_url", "date", "attribution", "license"])
    && (slide.source_kind === undefined || (typeof slide.source_kind === "string"
      && ["places", "images", "press", "tweets"].includes(slide.source_kind)))
    && (slide.fit === undefined || slide.fit === "cover" || slide.fit === "contain");
}

/** Narrow serialized JSON before card rendering or caption-yield calculations. */
export function isRenderableSlide(value: unknown): value is BRollSlide {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const slide = value as Record<string, unknown>;
  if (!isFiniteNumber(slide.start) || !isFiniteNumber(slide.end) || slide.start < 0 || slide.end <= slide.start) {
    return false;
  }
  if (!optionalStrings(slide, ["color", "background"])) return false;
  switch (slide.type) {
    case "text": return isValidTextSlide(slide);
    case "stat": return isValidStatSlide(slide);
    case "list": return isValidListSlide(slide);
    case "logo": return isValidLogoSlide(slide);
    case "person": return isValidPersonSlide(slide);
    case "image": return isValidImageSlide(slide);

    default:
      return false;
  }
}
