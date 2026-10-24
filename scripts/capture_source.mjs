/** Original public DOM screenshots, using the browser bundled with Remotion. */
import {lookup} from 'node:dns/promises';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import http from 'node:http';
import https from 'node:https';
import {createRequire} from 'node:module';
import {BlockList, isIP} from 'node:net';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

// A 2x-density 1080x1920 source, also enough area for a 3840x2160 image.
export const MAX_IMAGE_PIXELS = (1080 * 2) * (1920 * 2);
// One uncompressed 16-bit RGBA frame is the per-response HTTP body budget.
export const MAX_BODY_BYTES = MAX_IMAGE_PIXELS * 4 * 2;

const deny = new BlockList();
for (const [address, prefix] of [
  ['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8],
  ['169.254.0.0', 16], ['172.16.0.0', 12], ['192.0.0.0', 24], ['192.0.2.0', 24],
  ['192.88.99.0', 24], ['192.168.0.0', 16], ['198.18.0.0', 15],
  ['198.51.100.0', 24], ['203.0.113.0', 24], ['224.0.0.0', 4], ['240.0.0.0', 4],
]) deny.addSubnet(address, prefix, 'ipv4');
// Only global IPv6 unicast is eligible; exclude IANA special-use allocations.
const globalV6 = new BlockList();
globalV6.addSubnet('2000::', 3, 'ipv6');
for (const [address, prefix] of [
  ['2001::', 23], ['2001:db8::', 32], ['2002::', 16], ['3fff::', 20],
]) deny.addSubnet(address, prefix, 'ipv6');

export function validateUrl(value) {
  if (typeof value !== 'string' || /[\x00-\x20\x7f\\]/.test(value)) {
    throw new Error('Source URL contains whitespace, controls, or a backslash');
  }
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
      || /^https?:\/\/[^/]*@/i.test(value)) {
    throw new Error('Source URL must be public HTTP(S), without credentials');
  }
  const host = url.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  if (host === 'localhost' || /\.(localhost|local|internal)$/.test(host)) {
    throw new Error('Source host is not public');
  }
  if (isIP(host)) assertPublic(host);
  return {url, host};
}

export function assertPublic(address) {
  const family = isIP(address);
  const kind = family === 6 ? 'ipv6' : 'ipv4';
  if (!family || deny.check(address, kind) || (family === 6 && !globalV6.check(address, kind))) {
    throw new Error('Source host resolves to a non-public address');
  }
}

async function publicAddress(host) {
  const addresses = await lookup(host, {all: true, verbatim: true});
  if (!addresses.length) throw new Error('Source host has no public address');
  addresses.forEach(({address}) => assertPublic(address));
  return addresses[0];
}

export function rasterDimensions(data) {
  try {
    if (data.subarray(0, 8).equals(Buffer.from('\x89PNG\r\n\x1a\n', 'binary'))
        && data.toString('ascii', 12, 16) === 'IHDR') {
      return [data.readUInt32BE(16), data.readUInt32BE(20)];
    }
    if (['GIF87a', 'GIF89a'].includes(data.toString('ascii', 0, 6))) {
      return [data.readUInt16LE(6), data.readUInt16LE(8)];
    }
    if (data.toString('ascii', 0, 2) === 'BM') {
      const dibSize = data.readUInt32LE(14);
      if (dibSize === 12) return [data.readUInt16LE(18), data.readUInt16LE(20)];
      if (dibSize >= 40) return [data.readInt32LE(18), Math.abs(data.readInt32LE(22))];
    }
    if (data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) {
      let offset = 2;
      while (offset < data.length) {
        if (data[offset] !== 0xff) break;
        while (offset < data.length && data[offset] === 0xff) offset++;
        const marker = data[offset++];
        if ([0xd8, 0x01].includes(marker) || (marker >= 0xd0 && marker <= 0xd7)) continue;
        if ([0xd9, 0xda].includes(marker)) break;
        const length = data.readUInt16BE(offset);
        if (length < 2 || offset + length > data.length) break;
        if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
          return [data.readUInt16BE(offset + 5), data.readUInt16BE(offset + 3)];
        }
        offset += length;
      }
    }
    const tiff = data.toString('binary', 0, 4);
    if (['II*\x00', 'MM\x00*'].includes(tiff)) {
      const short = (offset) => tiff.startsWith('II') ? data.readUInt16LE(offset) : data.readUInt16BE(offset);
      const long = (offset) => tiff.startsWith('II') ? data.readUInt32LE(offset) : data.readUInt32BE(offset);
      const offset = long(4);
      const count = short(offset);
      const dimensions = new Map();
      for (let index = 0; index < count; index++) {
        const entry = offset + 2 + index * 12;
        const tag = short(entry); const kind = short(entry + 2); const size = long(entry + 4);
        if ([256, 257].includes(tag) && size === 1 && [3, 4].includes(kind)) {
          if (dimensions.has(tag)) throw new Error('Source image has duplicate raster dimensions');
          dimensions.set(tag, kind === 3 ? short(entry + 8) : long(entry + 8));
        }
      }
      if (dimensions.size === 2) return [dimensions.get(256), dimensions.get(257)];
    }
    if (data.toString('ascii', 0, 4) === 'RIFF' && data.toString('ascii', 8, 12) === 'WEBP') {
      const kind = data.toString('ascii', 12, 16);
      if (kind === 'VP8X') return [1 + data.readUIntLE(24, 3), 1 + data.readUIntLE(27, 3)];
      if (kind === 'VP8 ' && data.subarray(23, 26).equals(Buffer.from([0x9d, 0x01, 0x2a]))) {
        return [data.readUInt16LE(26) & 0x3fff, data.readUInt16LE(28) & 0x3fff];
      }
      if (kind === 'VP8L' && data[20] === 0x2f) {
        const bits = data.readUInt32LE(21);
        return [1 + (bits & 0x3fff), 1 + ((bits >>> 14) & 0x3fff)];
      }
    }
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
  }
  throw new Error('Source image has invalid or unsupported raster dimensions');
}

export function assertImagePixels(data) {
  const [width, height] = rasterDimensions(data);
  if (width <= 0 || height <= 0 || width * height > MAX_IMAGE_PIXELS) {
    throw new Error('Source image exceeds the pixel limit');
  }
}

export function publicRequest(url, address, headers, signal, image = false) {
  return new Promise((resolve, reject) => {
    const transport = url.protocol === 'https:' ? https : http;
    const request = transport.get(url, {
      signal, headers, agent: false,
      // The TLS hostname remains the URL hostname; DNS cannot change after check.
      lookup: (_hostname, options, done) => options.all
        ? done(null, [address]) : done(null, address.address, address.family),
    }, (response) => {
      const fail = (error) => { response.destroy(); request.destroy(); reject(error); };
      if ((response.headers['content-encoding'] || 'identity').trim().toLowerCase() !== 'identity') {
        fail(new Error('Public source uses an unexpected content encoding'));
        return;
      }
      if (Number(response.headers['content-length']) > MAX_BODY_BYTES) {
        fail(new Error('Public source body exceeds the byte limit'));
        return;
      }
      const chunks = [];
      let size = 0;
      response.on('data', (chunk) => {
        size += chunk.length;
        if (size > MAX_BODY_BYTES) fail(new Error('Public source body exceeds the byte limit'));
        else chunks.push(chunk);
      });
      response.on('error', reject);
      response.on('end', () => {
        try {
          const body = Buffer.concat(chunks);
          const mime = (response.headers['content-type'] || '').split(';')[0].trim().toLowerCase();
          // Inspect mislabeled rasters too: Chromium sniffs formats independently.
          const raster = /^(\x89PNG|\xff\xd8\xff|GIF8[79]a|BM|II\*\x00|MM\x00\*|RIFF)/.test(body.toString('binary', 0, 12));
          const redirected = [301, 302, 303, 307, 308].includes(response.statusCode);
          if (!redirected && (image || mime.startsWith('image/') || raster)) assertImagePixels(body);
          resolve({status: response.statusCode, headers: response.headers, body});
        } catch (error) { reject(error); }
      });
    });
    request.on('error', reject);
  });
}

export async function fetchPublic(value, headers = {}, timeout = 20000, image = false) {
  const {url, host} = validateUrl(value);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const safeHeaders = {'user-agent': 'ShortSmith public-source capture', 'accept-encoding': 'identity'};
  for (const name of ['accept', 'accept-language', 'range', 'content-type']) {
    if (headers[name]) safeHeaders[name] = headers[name];
  }
  try {
    const address = await Promise.race([
      publicAddress(host),
      new Promise((_, reject) => controller.signal.addEventListener('abort',
        () => reject(new Error('Public source HTTP deadline exceeded')), {once: true})),
    ]);
    return await publicRequest(url, address, safeHeaders, controller.signal, image);
  } finally {
    clearTimeout(timer);
  }
}

export async function interceptRequests(client, httpTimeout) {
  let failure = null;
  client.on('Fetch.requestPaused', (event) => {
    void (async () => {
      try {
        if (!['GET', 'HEAD'].includes(event.request.method)) {
          await client.send('Fetch.failRequest', {requestId: event.requestId, errorReason: 'BlockedByClient'});
          return;
        }
        const response = await fetchPublic(event.request.url, event.request.headers, httpTimeout,
          event.resourceType === 'Image');
        const responseHeaders = Object.entries(response.headers)
          .filter(([name]) => !['transfer-encoding', 'connection', 'set-cookie'].includes(name))
          .flatMap(([name, values]) => (Array.isArray(values) ? values : [values])
            .filter((value) => value !== undefined).map((value) => ({name, value: String(value)})));
        await client.send('Fetch.fulfillRequest', {
          requestId: event.requestId, responseCode: response.status,
          responseHeaders, body: response.body.toString('base64'),
        });
      } catch (error) {
        if (event.resourceType === 'Document' || /non-public|Source URL|not public/.test(error.message)) {
          failure = error;
        }
        await client.send('Fetch.failRequest', {
          requestId: event.requestId, errorReason: 'BlockedByClient',
        }).catch(() => {}); // A closing target has no pending request to cancel.
      }
    })();
  });
  await client.send('Fetch.enable', {patterns: [{urlPattern: '*', requestStage: 'Request'}]});
  return () => { if (failure) throw failure; };
}

async function openIsolatedBrowser(browserTimeout, profile) {
  const require = createRequire(new URL('../remotion/package.json', import.meta.url));
  // Remotion's pinned Puppeteer runner does not expose windowsHide. Set it in this
  // isolated CLI before loading the runner so browser launch/cleanup stays hidden.
  const childProcess = require('node:child_process');
  const spawn = childProcess.spawn;
  childProcess.spawn = (command, args, options) => spawn(command, args, {...options, windowsHide: true});
  const exec = childProcess.exec;
  childProcess.exec = (command, options, callback) => typeof options === 'function'
    ? exec(command, {windowsHide: true}, options)
    : exec(command, {...options, windowsHide: true}, callback);
  const renderer = dirname(require.resolve('@remotion/renderer/package.json'));
  const {HeadlessBrowser} = require(join(renderer, 'dist/browser/Browser.js'));
  const {getLocalBrowserExecutable} = require(join(renderer, 'dist/get-local-browser-executable.js'));
  const executablePath = localBrowserExecutable(getLocalBrowserExecutable);
  return HeadlessBrowser.create({
    executablePath, userDataDir: profile, timeout: browserTimeout,
    defaultViewport: {width: 1080, height: 1920, deviceScaleFactor: 1},
    logLevel: 'error', indent: false, args: browserArgs(profile),
  });
}

function localBrowserExecutable(getLocalBrowserExecutable) {
  // Remotion finds its browser cache from cwd. Keep this synchronous lookup scoped
  // to its own package so the helper also works when invoked from the repo root.
  const previousDirectory = process.cwd();
  try {
    process.chdir(fileURLToPath(new URL('../remotion/', import.meta.url)));
    return getLocalBrowserExecutable({
      preferredBrowserExecutable: null, logLevel: 'error', indent: false, chromeMode: 'headless-shell',
    }); // Local lookup only. Never download a browser during source capture.
  } finally {
    process.chdir(previousDirectory);
  }
}

function browserArgs(profile) {
  return [
    'about:blank', '--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    '--disable-background-networking', '--disable-component-update', '--disable-sync',
    '--disable-extensions', '--no-first-run', '--no-default-browser-check', '--disable-quic',
    '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
    // All browser traffic fails closed. Fetch interception supplies checked bytes;
    // this also stops workers, new targets, sockets, DNS prefetch, and IP literals
    // that bypass page-level interception from reaching a local service.
    '--proxy-server=http://unavailable.invalid:80', '--proxy-bypass-list=<-loopback>',
    '--host-resolver-rules=MAP * ~NOTFOUND',
  ];
}

function inspectDom(selector, quote, statusId) {
  const visibleText = document.body?.innerText || '';
  const title = document.title || '';
  if (/just a moment|attention required|checking your browser|verify you are human/i.test(title)
      || /^(access denied|sign in to continue|log in to continue)/i.test(visibleText.trim())
      || document.querySelector('#challenge-running, #challenge-form, .g-recaptcha')) {
    throw new Error('Source page is a login or browser challenge');
  }
  let element;
  if (statusId) {
    element = [...document.querySelectorAll('article')].find((article) =>
      [...article.querySelectorAll('a[href]')].some((link) =>
        new URL(link.href).pathname.endsWith(`/status/${statusId}`)));
    if (!element) throw new Error('Original post is deleted, unavailable, or requires login');
  } else {
    element = selector ? document.querySelector(selector)
      : document.querySelector('article, main, [role="main"]') || document.body;
  }
  if (!element || !element.innerText.trim()) throw new Error('Source content is missing or blank');
  if (quote && !element.innerText.includes(quote)) throw new Error('Exact quote is absent from source DOM');
  element.scrollIntoView({block: 'start'});
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) throw new Error('Source content is not visible');
  return {x: Math.max(0, rect.x + scrollX), y: Math.max(0, rect.y + scrollY),
    width: rect.width, height: rect.height, scale: 1};
}

function assertPageIdentity(original, current) {
  const expected = validateUrl(original).url;
  const actual = validateUrl(current).url;
  const postHost = (host) => ['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com',
    'mobile.twitter.com', 'mobile.x.com'].includes(host);
  const expectedStatus = expected.pathname.match(/\/status\/(\d+)/)?.[1];
  if (postHost(expected.hostname) && expectedStatus) {
    if (!postHost(actual.hostname)
        || actual.pathname.match(/\/status\/(\d+)/)?.[1] !== expectedStatus) {
      throw new Error('Source page identity changed during capture');
    }
    return;
  }
  const identity = (url) => `${url.origin}${url.pathname.replace(/\/$/, '')}${url.search}`;
  if (identity(expected) !== identity(actual)) {
    throw new Error('Source page identity changed during capture');
  }
}

async function screenshotPage(browser, options) {
  const page = await browser.newPage({
    context: () => null, logLevel: 'error', indent: false, pageIndex: 0,
    onBrowserLog: null, onLog: () => {},
  });
  const client = page._client();
  await client.send('Network.setBypassServiceWorker', {bypass: true});
  await client.send('Network.setBlockedURLs', {urls: [
    'file:*', 'ftp:*', 'ws:*', 'wss:*', 'chrome:*', 'chrome-extension:*', 'devtools:*',
  ]});
  const checkRequests = await interceptRequests(client, options.httpTimeout);
  const response = await page.goto({url: options.url, timeout: options.browserTimeout});
  if (!response || response.status() >= 400) throw new Error('Source page returned an HTTP error');
  assertPageIdentity(options.url, page.url());
  await page.evaluate(async () => { await document.fonts.ready; });
  const {url} = validateUrl(options.url);
  const statusId = /(^|\.)((x|twitter)\.com)$/.test(url.hostname)
    ? url.pathname.match(/\/status\/(\d+)/)?.[1] : null;
  // React pages are ready when the requested original article exists. The browser
  // supplies animation frames; there is no fixed sleep or guessed loading delay.
  if (statusId) await waitForPost(page, statusId, options.browserTimeout);
  const clip = await page.evaluate(inspectDom, options.selector, options.quote, statusId);
  if (!Number.isFinite(clip.width * clip.height) || clip.width * clip.height > MAX_IMAGE_PIXELS) {
    throw new Error('Source screenshot exceeds the pixel limit');
  }
  assertPageIdentity(options.url, page.url());
  checkRequests();
  const {value} = await client.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true, clip,
  });
  assertPageIdentity(options.url, page.url());
  checkRequests();
  await writeFile(options.dest, Buffer.from(value.data, 'base64'));
}

async function waitForPost(page, statusId, timeout) {
  await page.evaluate((id, duration) => new Promise((resolve, reject) => {
    const deadline = performance.now() + duration;
    const inspect = () => {
      const exists = [...document.querySelectorAll('article a[href]')].some((link) =>
        new URL(link.href).pathname.endsWith(`/status/${id}`));
      const text = document.body?.innerText || '';
      if (exists) return resolve();
      if (/this post was deleted|this account doesn.t exist|nothing to see here|sign in to x/i.test(text)) {
        return reject(new Error('Original post is deleted, unavailable, or requires login'));
      }
      if (performance.now() >= deadline) return reject(new Error('Original post did not load'));
      requestAnimationFrame(inspect);
    };
    inspect();
  }), statusId, timeout);
}

export async function capture(options) {
  if (process.env.SHORTSMITH_BROLL_OFFLINE === '1') throw new Error('Source capture is offline');
  validateUrl(options.url);
  const profile = await mkdtemp(join(tmpdir(), 'shortsmith-source-browser-'));
  let browser;
  let timer;
  try {
    browser = await openIsolatedBrowser(options.browserTimeout, profile);
    await Promise.race([
      screenshotPage(browser, options),
      new Promise((_, reject) => { timer = setTimeout(() =>
        reject(new Error('Public page capture deadline exceeded')), options.browserTimeout); }),
    ]);
  } finally {
    clearTimeout(timer);
    if (browser) await browser.close({silent: true});
    await rm(profile, {recursive: true, force: true});
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  try {
    let input = '';
    for await (const chunk of process.stdin) input += chunk;
    const options = JSON.parse(input);
    await capture(options);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
