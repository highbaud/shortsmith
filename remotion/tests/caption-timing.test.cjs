const assert = require("node:assert/strict");
const { test } = require("node:test");
const { chunkWords, activeCaptionChunk, yieldOpacity } = require("../src/caption-timing.ts");

const words = [
  { text: "first", start: 1, end: 1.2 },
  { text: "phrase", start: 1.2, end: 1.5 },
  { text: "last", start: 3, end: 3.4 },
];

test("invalid chunk sizes fail before entering the loop", () => {
  for (const size of [0, -1, 0.5, NaN, Infinity]) {
    assert.throws(() => chunkWords(words, size), /positive integer/);
  }
});

test("chunks preserve word order and the final partial phrase", () => {
  const chunks = chunkWords(words, 2);
  assert.deepEqual(chunks.map(({ start, end }) => [start, end]), [[1, 1.5], [3, 3.4]]);
  assert.deepEqual(chunks.flatMap((chunk) => chunk.words), words);
  assert.deepEqual(chunkWords([], 2), []);
});

test("captions hold between phrases and expire after the final hold", () => {
  const chunks = chunkWords(words, 2);
  assert.equal(activeCaptionChunk(0.9, chunks), null);
  assert.equal(activeCaptionChunk(1.1, chunks), chunks[0]);
  assert.equal(activeCaptionChunk(2, chunks), chunks[0]);
  assert.equal(activeCaptionChunk(3.2, chunks), chunks[1]);
  assert.equal(activeCaptionChunk(3.65, chunks), chunks[1]);
  assert.equal(activeCaptionChunk(3.651, chunks), null);
  assert.equal(activeCaptionChunk(100, chunks), null);
  assert.equal(activeCaptionChunk(0, []), null);
});

test("yield windows fade at their edges and overlapping windows cannot reveal captions", () => {
  const windows = [{ start: 1, end: 2 }];
  assert.equal(yieldOpacity(0.5, windows, 0.2), 1);
  assert.ok(Math.abs(yieldOpacity(0.9, windows, 0.2) - 0.5) < 1e-9);
  assert.equal(yieldOpacity(1.5, windows, 0.2), 0);
  assert.ok(Math.abs(yieldOpacity(2.1, windows, 0.2) - 0.5) < 1e-9);
  assert.equal(yieldOpacity(2.3, windows, 0.2), 1);
  assert.equal(yieldOpacity(2.1, [...windows, { start: 2, end: 3 }], 0.2), 0);
  assert.equal(yieldOpacity(1.5, windows, 0), 0);
});
