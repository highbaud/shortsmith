const assert = require("node:assert/strict");
const { test } = require("node:test");
const { isRenderableSlide } = require("../src/broll-validation.ts");
const { sequenceTiming, compositionMetadata, ambientPunchScale } = require("../src/timeline.ts");
const { isHexColor, isPositiveDurationMs, isVFXEvent } = require("../src/vfx-validation.ts");
const { normalizeVisualInputs } = require("../src/visual-inputs.ts");

const timed = (slide) => ({ start: 1, end: 2, ...slide });

test("visual input normalization retains accepted records and exact rejected indexes", () => {
  const accepted = timed({ type: "text", title: "Keep this" });
  const input = [null, accepted, timed({ type: "list", items: [] })];
  const result = normalizeVisualInputs(input, isRenderableSlide);
  assert.deepEqual(result.rejectedIndexes, [0, 2]);
  assert.deepEqual(result.values, [accepted]);
  assert.equal(result.values[0], accepted);
  assert.equal(result.isArray, true);
  assert.equal(input[0], null);
  for (const invalid of [null, "invalid", {}]) {
    assert.deepEqual(normalizeVisualInputs(invalid, isRenderableSlide), {
      values: [], rejectedIndexes: [], isArray: false,
    });
  }
});

test("all serialized card variants narrow before rendering", () => {
  for (const slide of [
    { type: "text", title: "A title" },
    { type: "stat", value: "42%" },
    { type: "stat", to: 0 },
    { type: "list", items: ["First", "Second"] },
    { type: "logo", src: "assets/logo.svg", mode: "badge" },
    { type: "person", src: "assets/person.png", motion: "left" },
    { type: "image", src: "assets/source.png" },
    { type: "image", src: "assets/source.png", source_kind: "press", source_label: "Source", date: "2026-11-03" },
  ]) assert.equal(isRenderableSlide(timed(slide)), true, JSON.stringify(slide));
});

test("malformed JSON cannot reach card reads or caption-yield windows", () => {
  for (const slide of [
    null, [], "text", {},
    timed({ type: "unknown" }),
    timed({ type: "text", title: {} }),
    timed({ type: "text", title: " " }),
    timed({ type: "stat" }),
    timed({ type: "stat", to: NaN }),
    timed({ type: "stat", value: "10", from: "zero" }),
    timed({ type: "list", items: [] }),
    timed({ type: "list", items: ["valid", {}] }),
    timed({ type: "logo", src: 42 }),
    timed({ type: "logo", src: "a.png", monochrome: "false" }),
    timed({ type: "person", src: "a.png", motion: ["left"] }),
    timed({ type: "person", src: "a.png", name: {} }),
    timed({ type: "image", src: "a.png", source_label: [] }),
    timed({ type: "image", src: "a.png", source_kind: ["press"] }),
    timed({ type: "image", src: "a.png", fit: "stretch" }),
    { type: "text", title: "Bad time", start: NaN, end: 2 },
    { type: "text", title: "Bad time", start: -1, end: 2 },
    { type: "text", title: "Bad time", start: 2, end: 1 },
  ]) assert.equal(isRenderableSlide(slide), false, JSON.stringify(slide));
});

test("invalid VFX durations and records are rejected before scheduling", () => {
  const event = { t: 2, effect: "flash", color: "#fff", intensity: 1, durationMs: 300 };
  for (const durationMs of [0, -1, NaN, Infinity, -Infinity, undefined, null, "300"]) {
    assert.equal(isPositiveDurationMs(durationMs), false);
    assert.equal(isVFXEvent({ ...event, durationMs }), false);
  }
  for (const effect of ["glare", "flash", "zoom-punch"]) {
    assert.equal(isVFXEvent({ ...event, effect }), true);
  }
  assert.equal(isVFXEvent({ ...event, intensity: 1.3 }), true);
  assert.equal(isVFXEvent({ ...event, intensity: 0 }), true);
  assert.equal(isVFXEvent({ ...event, intensity: -0.1 }), false);
  for (const color of ["#abc", "#ABCDEF", "#012345"]) {
    assert.equal(isHexColor(color), true);
    assert.equal(isVFXEvent({ ...event, color }), true);
  }
  for (const color of ["red", "invalid", "#12", "#abcd", "#aabbccdd", "#ggg", "", null]) {
    assert.equal(isHexColor(color), false);
    assert.equal(isVFXEvent({ ...event, color }), false);
  }
  for (const invalid of [null, [], {}, { ...event, t: NaN }, { ...event, intensity: Infinity },
    { ...event, effect: "unknown" }, { ...event, color: 42 }]) {
    assert.equal(isVFXEvent(invalid), false);
  }
});

test("sequence timing rounds once and keeps short events renderable", () => {
  assert.deepEqual(sequenceTiming(1.05, 0.09, 30), { from: 32, durationInFrames: 3 });
  assert.deepEqual(sequenceTiming(0, 0.001, 30), { from: 0, durationInFrames: 1 });
});

test("composition metadata rounds up to include the final partial frame", () => {
  assert.equal(compositionMetadata({ fps: 30, durationInSeconds: 1.01 }).durationInFrames, 31);
  assert.equal(compositionMetadata({ fps: 60, durationInSeconds: 1.01 }).durationInFrames, 61);
  assert.equal(compositionMetadata({ fps: 0, durationInSeconds: 1 }).fps, 30);
  assert.equal(compositionMetadata({ fps: 30, durationInSeconds: 0 }).durationInFrames, 1);
});

test("ambient punches return to rest and overlaps use the strongest bump", () => {
  assert.equal(ambientPunchScale(0, [1]), 1);
  assert.equal(ambientPunchScale(1.28, [1]), 1.045);
  assert.equal(ambientPunchScale(2, [1]), 1);
  assert.equal(ambientPunchScale(1.28, [1, 1]), ambientPunchScale(1.28, [1]));
});
