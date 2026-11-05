import type { VFXEvent } from "./types";

export function isPositiveDurationMs(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

export function isHexColor(value: unknown): value is string {
  return typeof value === "string" && /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value);
}

export function isVFXEvent(value: unknown): value is VFXEvent {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const event = value as Record<string, unknown>;
  return isPositiveDurationMs(event.durationMs)
    && typeof event.t === "number" && Number.isFinite(event.t)
    && typeof event.intensity === "number" && Number.isFinite(event.intensity) && event.intensity >= 0
    && isHexColor(event.color)
    && (event.effect === "glare" || event.effect === "flash" || event.effect === "zoom-punch");
}
