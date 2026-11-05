const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { after, before, test } = require("node:test");
// Resolve the tools owned by the declared CLI dependency without assuming hoisting.
const cliRequire = createRequire(require.resolve("@remotion/cli"));
const { bundle } = cliRequire("@remotion/bundler");
const { openBrowser, renderStill, selectComposition } = cliRequire("@remotion/renderer");

const projectRoot = path.resolve(__dirname, "..");
const scratchRoot = path.join(projectRoot, ".desloppify");
let scratch;
let browser;
let serveUrl;
let composition;

before(async () => {
  fs.mkdirSync(scratchRoot, { recursive: true });
  assert.equal(fs.lstatSync(scratchRoot).isSymbolicLink(), false, "Scratch root must not be a link");
  scratch = fs.mkdtempSync(path.join(scratchRoot, "vfx-regressions-"));
  const publicDir = path.join(scratch, "public");
  fs.mkdirSync(publicDir);
  fs.writeFileSync(path.join(publicDir, "card.svg"), '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#456789"/><circle cx="200" cy="200" r="100" fill="white"/></svg>');
  const candidates = [
    process.env.REMOTION_BROWSER_EXECUTABLE,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
  ].filter(Boolean);
  const executable = candidates.find((candidate) => fs.existsSync(candidate));
  assert.ok(executable, "Set REMOTION_BROWSER_EXECUTABLE to an existing Chrome or Chromium executable");
  serveUrl = await bundle({
    entryPoint: path.join(__dirname, "fixtures", "vfx.tsx"),
    outDir: path.join(scratch, "bundle"),
    publicDir,
    enableCaching: false,
    webpackOverride: (config) => ({
      ...config,
      resolve: { ...config.resolve, alias: {
        ...config.resolve?.alias,
        "@remotion/google-fonts/Anton": path.join(__dirname, "fixtures", "font.ts"),
        "@remotion/media": path.join(__dirname, "fixtures", "media.tsx"),
      } },
    }),
  });
  browser = await openBrowser("chrome", { browserExecutable: executable, logLevel: "error" });
  composition = await selectComposition({ serveUrl, id: "VFX", puppeteerInstance: browser, logLevel: "error" });
});

after(async () => {
  if (browser) await browser.close({ silent: true });
  if (!scratch) return;
  const resolved = fs.realpathSync(scratch);
  assert.equal(path.dirname(resolved), fs.realpathSync(scratchRoot));
  const pending = [resolved];
  while (pending.length) {
    const current = pending.pop();
    const stat = fs.lstatSync(current);
    assert.equal(stat.isSymbolicLink(), false, `Refusing linked scratch path: ${current}`);
    if (stat.isDirectory()) {
      for (const child of fs.readdirSync(current)) pending.push(path.join(current, child));
    }
  }
  fs.rmSync(resolved, { recursive: true });
});

async function frame(props, frameNumber, id = "VFX", logs = []) {
  const selected = id === "VFX" ? composition : await selectComposition({
    serveUrl, id, inputProps: props, puppeteerInstance: browser, logLevel: "error",
  });
  const result = await renderStill({
    serveUrl,
    composition: { ...selected, props },
    inputProps: props,
    frame: frameNumber,
    output: null,
    scale: id === "Cards" || id === "Short" ? 0.2 : 1,
    onBrowserLog: (entry) => logs.push(entry),
    imageFormat: "png",
    puppeteerInstance: browser,
    logLevel: "error",
  });
  assert.ok(result.buffer);
  return result.buffer;
}

test("importing Short does not start font loading", async () => {
  const audit = await selectComposition({ serveUrl, id: "ImportAudit", puppeteerInstance: browser, logLevel: "error" });
  assert.equal(audit.props.fontLoadsAtImport, 0);
});

for (const effect of ["glare", "flash"]) {
  test(`${effect} uses its Sequence clock at a nonzero start frame`, async () => {
    const delayed = { effect, startFrame: 60 };
    const beforeStart = await frame(delayed, 59);
    const afterEnd = await frame(delayed, 69);
    const active = await frame(delayed, 64);
    const atZero = await frame({ effect, startFrame: 0 }, 4);
    assert.deepEqual(beforeStart, afterEnd, "The effect must be hidden outside its Sequence");
    assert.notDeepEqual(active, beforeStart, "The scheduled effect must be visible during its Sequence");
    assert.deepEqual(active, atZero, "The same local frame must look identical at any timeline offset");
    assert.deepEqual(await frame(delayed, 64), active, "Rendering the same frame must be deterministic");
  });
  test(`${effect} ignores zero duration at its exact start without NaN styles`, async () => {
    const beforeStart = await frame({ effect, startFrame: 60 }, 59);
    assert.deepEqual(await frame({ effect, startFrame: 60, durationMs: 0 }, 60), beforeStart);
  });
}

test("overlapping zoom punches use their maximum and return to rest", async () => {
  const event = { t: 1, effect: "zoom-punch", color: "#fff", intensity: 1, durationMs: 300 };
  const active = await frame({ events: [event] }, 34, "Zoom");
  assert.deepEqual(active, await frame({ events: [event, event] }, 34, "Zoom"));
  assert.notDeepEqual(active, await frame({ events: [] }, 34, "Zoom"));
  assert.deepEqual(await frame({ events: [event] }, 40, "Zoom"), await frame({ events: [] }, 40, "Zoom"));
});

test("every B-roll variant renders with its palette fallback and an explicit override", async () => {
  const colors = ["#e81e63", "#10a37f", "#0471a6", "#e81e63", "#e81e63", "#0471a6"];
  for (let variant = 0; variant < colors.length; variant++) {
    const fallback = await frame({ variant }, 25, "Cards");
    assert.deepEqual(fallback, await frame({ variant, color: colors[variant] }, 25, "Cards"));
    assert.notDeepEqual(fallback, await frame({ variant, color: "#ff9900" }, 25, "Cards"));
  }
  assert.notDeepEqual(await frame({ variant: 3, badge: true }, 25, "Cards"),
    await frame({ variant: 3, badge: false }, 25, "Cards"));
});

test("Short filters malformed slides without hiding captions and schedules cards and VFX", async () => {
  const { defaultShortProps } = require("../src/types.ts");
  const valid = { type: "text", title: "A cutaway", start: 2, end: 3 };
  const invalid = { type: "list", start: 0, end: 10 };
  const event = { t: 0.5, effect: "flash", color: "#fff", intensity: 1, durationMs: 300 };
  const props = { ...defaultShortProps, durationInSeconds: 4,
    words: [{ text: "VISIBLE", start: 0, end: 1 }], broll: [valid, invalid], vfxEvents: [event] };
  const logs = [];
  const caption = await frame(props, 10, "Short", logs);
  assert.deepEqual(caption, await frame({ ...props, broll: [valid] }, 10, "Short"));
  assert.notDeepEqual(caption, await frame({ ...props, captionsEnabled: false }, 10, "Short"));
  assert.deepEqual(await frame(props, 45, "Short"),
    await frame({ ...props, captionsEnabled: false }, 45, "Short"));
  assert.notDeepEqual(await frame(props, 75, "Short"), await frame({ ...props, broll: [] }, 75, "Short"));
  assert.notDeepEqual(await frame(props, 18, "Short"), await frame({ ...props, vfxEvents: [] }, 18, "Short"));
  assert.match(JSON.stringify(logs), /Skipped 1 invalid b-roll/);
  assert.match(JSON.stringify(logs), /at indexes \[1\]/);
});

test("split-stack labels and logo badge use explicit geometry independently of the caption band", async () => {
  const { defaultShortProps } = require("../src/types.ts");
  const props = { ...defaultShortProps, captionsEnabled: false, durationInSeconds: 4,
    captionBand: { top: 0.4, bottom: 0.6 },
    speakerLabels: [{ name: "TOP", position: "top" }, { name: "BOTTOM", position: "bottom" }],
    speakerPanels: [{ x: 100, y: 100, w: 400, h: 400 }, { x: 580, y: 1100, w: 400, h: 400 }],
    logoBadgeAnchor: { x: 20, y: 600, size: 100 },
    broll: [{ type: "logo", src: "card.svg", mode: "badge", start: 0, end: 3 }] };
  const original = await frame(props, 20, "Short");
  assert.deepEqual(original, await frame({ ...props, captionBand: { top: 0.5, bottom: 0.7 } }, 20, "Short"));
  assert.notDeepEqual(original, await frame({ ...props,
    speakerPanels: props.speakerPanels.map((panel) => ({ ...panel, x: panel.x + 100 })) }, 20, "Short"));
  assert.notDeepEqual(original, await frame({ ...props,
    logoBadgeAnchor: { ...props.logoBadgeAnchor, x: 200, y: 700 } }, 20, "Short"));
  assert.notDeepEqual(await frame({ ...props, speakerPanels: [] }, 20, "Short"),
    await frame({ ...props, speakerPanels: [], captionBand: { top: 0.5, bottom: 0.7 } }, 20, "Short"));
});

test("font failure keeps rendering and preserves the cause in its warning", async () => {
  const { defaultShortProps } = require("../src/types.ts");
  const logs = [];
  await frame({ ...defaultShortProps, testFontFailure: true }, 0, "Short", logs);
  assert.match(JSON.stringify(logs), /Anton font could not load/);
  assert.match(JSON.stringify(logs), /Test font unavailable/);
});

test("invalid caption size uses the existing default with a diagnostic", async () => {
  const { defaultShortProps } = require("../src/types.ts");
  const props = { ...defaultShortProps, words: [{ text: "VISIBLE", start: 0, end: 1 }] };
  const logs = [];
  assert.deepEqual(await frame({ ...props, captionMaxWords: 0 }, 10, "Short", logs),
    await frame(props, 10, "Short"));
  assert.match(JSON.stringify(logs), /Invalid captionMaxWords 0; using default 4/);
});

test("rejected VFX entries report their indexes and malformed containers are diagnosed", async () => {
  const { defaultShortProps } = require("../src/types.ts");
  const event = { t: 0, effect: "flash", color: "#fff", intensity: 1, durationMs: 300 };
  const logs = [];
  const props = { ...defaultShortProps, vfxEvents: [event, { ...event, durationMs: 0 }] };
  assert.deepEqual(await frame(props, 1, "Short", logs),
    await frame({ ...props, vfxEvents: [event] }, 1, "Short"));
  assert.match(JSON.stringify(logs), /Skipped 1 invalid VFX event\(s\) at indexes \[1\]/);
  const invalidLogs = [];
  await frame({ ...defaultShortProps, broll: null, vfxEvents: "invalid" }, 0, "Short", invalidLogs);
  assert.match(JSON.stringify(invalidLogs), /Invalid b-roll payload: expected an array/);
  assert.match(JSON.stringify(invalidLogs), /Invalid VFX payload: expected an array/);
});
