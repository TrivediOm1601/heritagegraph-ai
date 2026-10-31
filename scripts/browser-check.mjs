#!/usr/bin/env node
/**
 * Cross-browser end-to-end check for HeritageGraph AI — no dependencies.
 *
 * Drives a real installed browser (Brave by default, then Chrome/Edge) over
 * the Chrome DevTools Protocol and exercises the browser-facing workflows:
 *
 *   1. load the app;
 *   2. text search → subject overview: heading, subject photograph (checks
 *      the image really decoded, which is how a blocked external image host
 *      shows up), related topics;
 *   3. click a related topic → its own overview replaces the previous one;
 *   4. upload each image through the real <input type=file> (CDP
 *      DOM.setFileInputFiles, so the actual bytes travel the browser's
 *      multipart path) and wait for the analysis to reach a terminal state;
 *   5. report the rendered status, warnings, extracted text/entities and
 *      whether the fixture-only markers of a broken pipeline appear.
 *
 * Everything it reports comes from the live page and its network log; no
 * value is assumed. Exits non-zero when a step fails, so it can gate a
 * release. Console errors, failed requests and non-2xx responses are listed
 * with the first failing step that was seen.
 *
 * Usage (see README "Cross-browser check"):
 *
 *   node scripts/browser-check.mjs --url http://127.0.0.1:5173 \
 *        --search "Qutub Minar" --image backend/testdata/eval/t1.jpg
 *
 *   --browser <exe>    browser binary (default: autodetect Brave/Chrome/Edge)
 *   --headed           show the browser window (default: headless)
 *   --timeout <sec>    per-analysis timeout (default 180)
 *   --json <path>      also write the report as JSON
 *   --keep             leave the browser running when the script exits
 */

import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';

/* ------------------------------------------------------------------ args */

const argv = process.argv.slice(2);
const has = (name) => argv.includes(name);
function value(name, fallback = null) {
  const index = argv.indexOf(name);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
}
function values(name) {
  const found = [];
  argv.forEach((token, index) => {
    if (token === name && argv[index + 1]) found.push(argv[index + 1]);
  });
  return found;
}

const APP_URL = value('--url', 'http://127.0.0.1:5173');
const SEARCH_QUERY = value('--search', 'Qutub Minar');
const IMAGES = values('--image');
const TIMEOUT_MS = Number(value('--timeout', '180')) * 1000;
const JSON_OUT = value('--json');
const HEADED = has('--headed');
const KEEP = has('--keep');
//: Extra browser switches, e.g. --arg "--host-resolver-rules=MAP thumb.wikimedia.org ~NOTFOUND"
//: to reproduce a browser that cannot reach the image hosts.
const EXTRA_ARGS = values('--arg');

/* ------------------------------------------------------- browser lookup */

const WINDOWS_CANDIDATES = [
  'C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe',
  'C:/Program Files (x86)/BraveSoftware/Brave-Browser/Application/brave.exe',
  `${process.env.LOCALAPPDATA ?? ''}/BraveSoftware/Brave-Browser/Application/brave.exe`,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const POSIX_CANDIDATES = [
  '/usr/bin/brave-browser',
  '/usr/bin/brave',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/usr/bin/microsoft-edge',
];

function findBrowser() {
  const explicit = value('--browser') ?? process.env.BROWSER_PATH ?? null;
  if (explicit) {
    if (!existsSync(explicit)) {
      console.error(`browser not found: ${explicit}`);
      process.exit(2);
    }
    return explicit;
  }
  for (const candidate of [...WINDOWS_CANDIDATES, ...POSIX_CANDIDATES]) {
    if (candidate && existsSync(candidate)) return candidate;
  }
  console.error(
    'No Brave/Chrome/Edge binary found. Pass --browser <path> or set BROWSER_PATH.',
  );
  process.exit(2);
}

function browserName(exe) {
  const lower = exe.toLowerCase();
  if (lower.includes('brave')) return 'Brave';
  if (lower.includes('chrome')) return 'Chrome';
  if (lower.includes('edge')) return 'Edge';
  if (lower.includes('chromium')) return 'Chromium';
  return path.basename(exe);
}

/* ---------------------------------------------------------------- utils */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function waitForHttp(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
    } catch {
      /* not up yet */
    }
    await sleep(250);
  }
  throw new Error(`timed out waiting for ${url}`);
}

/* ------------------------------------------------------- CDP connection */

class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(`${message.error.message} (${message.error.code})`));
        else resolve(message.result);
        return;
      }
      if (message.method && this.handlers.has(message.method)) {
        for (const handler of this.handlers.get(message.method)) handler(message.params);
      }
    });
  }

  on(method, handler) {
    if (!this.handlers.has(method)) this.handlers.set(method, []);
    this.handlers.get(method).push(handler);
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`${method} timed out`));
      }, 120_000);
    });
  }

  /** Evaluate an async expression in the page and return its value. */
  async eval(expression) {
    const result = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      const text =
        result.exceptionDetails.exception?.description ??
        result.exceptionDetails.text ??
        'evaluation failed';
      throw new Error(text.split('\n')[0]);
    }
    return result.result.value;
  }

  /** Poll an expression until it returns a truthy value. */
  async waitFor(expression, { timeoutMs = 30_000, everyMs = 250, what = 'condition' } = {}) {
    const deadline = Date.now() + timeoutMs;
    let last;
    while (Date.now() < deadline) {
      last = await this.eval(expression);
      if (last) return last;
      await sleep(everyMs);
    }
    throw new Error(`timed out waiting for ${what} (last value: ${JSON.stringify(last)})`);
  }
}

/* ----------------------------------------------------------- page helpers */

const PAGE_HELPERS = `
  window.__hgSetValue = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return el.value;
  };
  window.__hgText = (selector) => {
    const node = document.querySelector(selector);
    return node ? (node.textContent || '').replace(/\\s+/g, ' ').trim() : null;
  };
  window.__hgImage = (selector) => {
    const img = document.querySelector(selector);
    if (!img) return { present: false };
    return {
      present: true,
      src: img.currentSrc || img.src,
      complete: img.complete,
      naturalWidth: img.naturalWidth,
      decoded: img.complete && img.naturalWidth > 0,
    };
  };
  'ready';
`;

/** Load the app in a clean page, so no previous run's panel can be mistaken
 *  for this run's result. */
async function freshPage(cdp, url) {
  await cdp.send('Page.navigate', { url });
  await cdp.waitFor('document.readyState === "complete"', { what: 'document load' });
  await cdp.waitFor('Boolean(document.querySelector("#historical-search-input"))', {
    what: 'the search field',
  });
  await cdp.eval(PAGE_HELPERS);
}

/**
 * Wait for an image to actually decode, then report what was observed.
 * A URL that is fetched but never renders (blocked host, 4xx, aborted) ends
 * up here as `decoded: false` — the failure a browser-specific block shows.
 */
async function loadImage(cdp, selector, timeoutMs = 20_000) {
  const before = await cdp.eval(`window.__hgImage(${JSON.stringify(selector)})`);
  if (!before.present) return before;
  try {
    await cdp.waitFor(
      `(() => { const image = window.__hgImage(${JSON.stringify(selector)}); return image.decoded ? image : null; })()`,
      { timeoutMs, what: 'the image to decode' },
    );
  } catch {
    /* fall through: the caller decides whether that is a failure */
  }
  return cdp.eval(`window.__hgImage(${JSON.stringify(selector)})`);
}

/**
 * Compare how this browser loads the subject photograph through the app
 * (same-origin proxy) versus the upstream image host directly. A browser
 * whose shields/DNS/proxy blocks the image host still renders the former and
 * fails the latter — which is exactly the failure this check exists to show.
 */
async function compareImagePaths(cdp) {
  return cdp.eval(`(async () => {
    const img = document.querySelector('[data-testid="subject-overview"] img');
    if (!img) return { skipped: 'this result has no subject photograph' };
    const renderedSrc = img.currentSrc || img.src;
    const proxied = renderedSrc.includes('/api/media');
    const upstream = proxied
      ? decodeURIComponent(new URL(renderedSrc, location.href).searchParams.get('url') || '')
      : renderedSrc;
    const attempt = (url) => new Promise((resolve) => {
      const probe = new Image();
      probe.onload = () => resolve({ url, loaded: true, naturalWidth: probe.naturalWidth });
      probe.onerror = () => resolve({ url, loaded: false });
      probe.src = url;
    });
    return {
      renderedSrc: renderedSrc.slice(0, 160),
      renderPath: proxied ? 'same-origin proxy' : 'direct upstream URL',
      renderedDecoded: img.complete && img.naturalWidth > 0,
      upstream,
      directAttempt: upstream ? await attempt(upstream) : { skipped: 'no upstream URL' },
    };
  })()`);
}

/** The upload form's own submit button (the search form has one too). */
const UPLOAD_BUTTON = `(() => {
  const section = document.querySelector('#upload-heading')?.closest('section');
  return section ? section.querySelector('button[type=submit]') : null;
})()`;

async function step(name, fn, report) {
  const started = Date.now();
  process.stdout.write(`\n▸ ${name}\n`);
  try {
    const result = await fn();
    const ms = Date.now() - started;
    report.steps.push({ name, ok: true, ms, result });
    console.log(`  ✓ ok (${(ms / 1000).toFixed(1)}s)`);
    if (result !== undefined) console.log(`    ${JSON.stringify(result)}`);
    return result;
  } catch (error) {
    const ms = Date.now() - started;
    report.steps.push({ name, ok: false, ms, error: String(error.message ?? error) });
    console.log(`  ✗ ${error.message ?? error}`);
    return undefined;
  }
}

/* ------------------------------------------------------------------- run */

const browserPath = findBrowser();
const port = await freePort();
const profileDir = mkdtempSync(path.join(tmpdir(), 'hg-browser-check-'));

const report = {
  browser: browserName(browserPath),
  browserPath,
  appUrl: APP_URL,
  searchQuery: SEARCH_QUERY,
  startedAt: new Date().toISOString(),
  steps: [],
  images: [],
  consoleErrors: [],
  failedRequests: [],
  badResponses: [],
};

console.log(`HeritageGraph AI — browser check`);
console.log(`  browser: ${report.browser} (${browserPath})`);
console.log(`  app:     ${APP_URL}`);
console.log(`  mode:    ${HEADED ? 'headed' : 'headless'}`);

const browserArgs = [
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profileDir}`,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-extensions',
  '--disable-background-networking',
  '--window-size=1400,1000',
  ...EXTRA_ARGS,
  'about:blank',
];
if (!HEADED) browserArgs.unshift('--headless=new');

const child = spawn(browserPath, browserArgs, { stdio: 'ignore', detached: false });
let closed = false;
function shutdown() {
  if (closed) return;
  closed = true;
  if (!KEEP) {
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
      } else {
        child.kill('SIGTERM');
      }
    } catch {
      /* already gone */
    }
  }
}
process.on('exit', () => {
  shutdown();
  try {
    rmSync(profileDir, { recursive: true, force: true });
  } catch {
    /* profile may be locked briefly on Windows */
  }
});
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    shutdown();
    process.exit(130);
  });
}

let cdp;
try {
  const version = await waitForHttp(`http://127.0.0.1:${port}/json/version`, 40_000);
  report.browserVersion = version.Browser;
  console.log(`  version: ${version.Browser}`);

  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page =
    targets.find((target) => target.type === 'page' && target.url === 'about:blank') ??
    targets.find((target) => target.type === 'page');
  if (!page) throw new Error('the browser exposed no page target');

  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', () => reject(new Error('CDP socket failed')), { once: true });
  });
  cdp = new Cdp(socket);

  cdp.on('Runtime.consoleAPICalled', (params) => {
    if (params.type !== 'error' && params.type !== 'warning') return;
    const text = (params.args ?? [])
      .map((arg) => arg.value ?? arg.description ?? arg.type)
      .join(' ');
    report.consoleErrors.push({ level: params.type, text: text.slice(0, 400) });
  });
  cdp.on('Runtime.exceptionThrown', (params) => {
    report.consoleErrors.push({
      level: 'exception',
      text: (params.exceptionDetails?.exception?.description ?? 'exception').slice(0, 400),
    });
  });
  cdp.on('Log.entryAdded', (params) => {
    if (params.entry.level !== 'error') return;
    report.consoleErrors.push({
      level: 'log',
      text: `${params.entry.source}: ${params.entry.text}`.slice(0, 400),
    });
  });
  cdp.on('Network.loadingFailed', (params) => {
    report.failedRequests.push({
      error: params.errorText,
      type: params.type,
      blocked: Boolean(params.blockedReason),
      blockedReason: params.blockedReason ?? null,
    });
  });
  cdp.on('Network.responseReceived', (params) => {
    if (params.response.status < 400) return;
    const url = params.response.url;
    // Signing in probes the fallback path on purpose (signup → 409 when the
    // account exists, then login); those answers are expected, not failures.
    const expectedProbe =
      /\/api\/auth\/(signup|login)$/.test(url) && [401, 409].includes(params.response.status);
    if (expectedProbe) return;
    report.badResponses.push({
      status: params.response.status,
      url: url.slice(0, 300),
    });
  });

  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Network.enable');
  await cdp.send('DOM.enable');

  /* 1 — load --------------------------------------------------------- */
  await step('load the application', async () => {
    await cdp.send('Page.navigate', { url: APP_URL });
    await cdp.waitFor('document.readyState === "complete"', { what: 'document load' });
    await cdp.waitFor(
      'Boolean(document.querySelector("#historical-search-input"))',
      { what: 'the search field' },
    );
    await cdp.eval(PAGE_HELPERS);
    return { title: await cdp.eval('document.title') };
  }, report);

  /* 1.5 — sign in ------------------------------------------------------
     Image analysis and the history are owner-scoped, so the upload form (and
     its file input) only exists for an authenticated session. Signing in from
     inside the page (rather than injecting a cookie over CDP) is what a real
     user does and works in every engine; the account is created on the first
     run and reused afterwards. */
  if (IMAGES.length > 0) {
    await step('sign in (creating the check account on first use)', async () => {
      const result = await cdp.eval(`(async () => {
        const account = {
          email: 'browser-check@heritagegraph.invalid',
          password: 'browser-check-passphrase',
        };
        const post = (route) => fetch(route, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(account),
          credentials: 'same-origin',
        });
        let response = await post('/api/auth/signup');
        const created = response.status === 201;
        if (!created) response = await post('/api/auth/login');
        const me = await fetch('/api/auth/me', { credentials: 'same-origin' });
        return { created, signInStatus: response.status, meStatus: me.status };
      })()`);
      if (result.meStatus !== 200) {
        throw new Error(`sign-in failed (HTTP ${result.signInStatus})`);
      }
      return result;
    }, report);
  }

  /* 2 — text search + subject photograph ----------------------------- */
  const search = await step(`search for "${SEARCH_QUERY}"`, async () => {
    await cdp.eval(`(() => {
      const input = document.querySelector('#historical-search-input');
      window.__hgSetValue(input, ${JSON.stringify(SEARCH_QUERY)});
      document.querySelector('form')?.requestSubmit();
      return true;
    })()`);

    await cdp.waitFor(
      'Boolean(document.querySelector(\'[data-testid="subject-overview"]\'))',
      { timeoutMs: 60_000, what: 'the subject overview' },
    );

    const heading =    await cdp.eval(
      'window.__hgText(\'[data-testid="subject-overview"] h2\')',
    );
    const image = await loadImage(cdp, '[data-testid="subject-overview"] img');
    const topics = await cdp.eval(
      'Array.from(document.querySelectorAll(\'[data-testid="related-topic"]\')).map((a) => a.textContent.replace(/\\s+/g, " ").trim().slice(0, 60))',
    );
    return { heading, image, topicCount: topics.length, topics: topics.slice(0, 3) };
  }, report);

  await step('compare proxied vs direct image loading in this browser', async () =>
    compareImagePaths(cdp), report);

  if (search && search.image?.present && !search.image.decoded) {
    report.steps.push({
      name: 'subject photograph decoded',
      ok: false,
      error: `image did not decode: ${search.image.src}`,
    });
    console.log(`  ✗ subject photograph did not decode: ${search.image.src}`);
  }

  /* 3 — related-topic navigation ------------------------------------- */
  await step('open a related topic', async () => {
    const before = await cdp.eval('window.__hgText(\'[data-testid="subject-overview"] h2\')');
    const clicked = await cdp.eval(`(() => {
      const link = document.querySelector('[data-testid="related-topic"]');
      if (!link) return null;
      const name = link.textContent.replace(/\\s+/g, ' ').trim().slice(0, 40);
      link.click();
      return name;
    })()`);
    if (!clicked) return { skipped: 'no related topic in this result' };
    // The overview reloads in place: wait for a *loaded* heading that is not
    // the previous one (a null heading is the intermediate loading state).
    await cdp.waitFor(
      `(() => {
         const heading = window.__hgText('[data-testid="subject-overview"] h2');
         return heading && heading !== ${JSON.stringify(before)} ? heading : null;
       })()`,
      { timeoutMs: 60_000, what: 'the topic overview' },
    );
    const after = await cdp.eval('window.__hgText(\'[data-testid="subject-overview"] h2\')');
    return { from: before, to: after, url: await cdp.eval('location.search') };
  }, report);

  /* 4 — uploads ------------------------------------------------------- */
  for (const imagePath of IMAGES) {
    const absolute = path.resolve(imagePath);
    const entry = { image: imagePath };
    report.images.push(entry);

    await step(`upload ${path.basename(imagePath)} and analyse it`, async () => {
      // A clean page per upload: the analysis panel starts empty, so any
      // terminal status below belongs to this image and not the previous one.
      await freshPage(cdp, APP_URL);

      const { root } = await cdp.send('DOM.getDocument', { depth: -1 });
      const { nodeId } = await cdp.send('DOM.querySelector', {
        nodeId: root.nodeId,
        selector: 'input[type=file]',
      });
      if (!nodeId) throw new Error('no file input on the page');
      await cdp.send('DOM.setFileInputFiles', { nodeId, files: [absolute] });

      // The upload form only enables its submit button once a file is set.
      await cdp.waitFor(
        `(() => { const button = ${UPLOAD_BUTTON}; return Boolean(button) && !button.disabled; })()`,
        { timeoutMs: 15_000, what: 'the upload submit button' },
      );
      await cdp.eval(`(() => { ${UPLOAD_BUTTON}.click(); return true; })()`);

      // Either the upload is accepted, or the browser/backend rejected it.
      const outcome = await cdp.waitFor(
        `(() => {
           const section = document.querySelector('#upload-heading')?.closest('section');
           const text = (section?.innerText || '').replace(/\\s+/g, ' ').trim();
           if (text.includes('Upload accepted')) return { kind: 'accepted', text };
           const alert = section?.querySelector('[role=alert]');
           if (alert) return { kind: 'rejected', text: alert.innerText.trim() };
           return null;
         })()`,
        { timeoutMs: 40_000, what: 'the upload outcome' },
      );

      if (outcome.kind === 'rejected') {
        entry.outcome = outcome.text;
        throw new Error(`upload rejected: ${outcome.text}`);
      }
      entry.outcome = outcome.text;

      // Wait for the analysis itself to reach a terminal state.
      const status = await cdp.waitFor(
        `(() => {
           const text = window.__hgText('[aria-label="Analysis status"]');
           if (!text) return null;
           if (/Failed/.test(text)) return { status: 'Failed', text };
           if (/Completed/.test(text)) return { status: 'Completed', text };
           // A run whose text stage found nothing (no OCR configured and the
           // model read no legible text) is honestly reported Partial; it is
           // terminal too, and the warnings it carries are checked below.
           if (/Partial/.test(text)) return { status: 'Partial', text };
           return null;
         })()`,
        { timeoutMs: TIMEOUT_MS, what: 'a terminal analysis status', everyMs: 1000 },
      );
      entry.status = status.status;
      entry.statusText = status.text.slice(0, 700);
      entry.analysisId = await cdp.eval(
        `(document.body.innerText.match(/analysis ([0-9a-f-]{8,})/) || [])[1] || null`,
      );

      // Give the analysis page's subject photograph a chance to render too.
      await loadImage(cdp, '[aria-label="Analysis results"] img', 15_000);

      const detail = await cdp.eval(`(() => {
        const text = document.body.innerText;
        return {
          extractedText: window.__hgText('#extracted-text-heading + div') || null,
          interpretation: window.__hgText('#interpretation-heading + p') || null,
          subjectHeading: window.__hgText('[aria-label="Analysis results"] ~ * h2') || null,
          entities: Array.from(document.querySelectorAll('[aria-labelledby="entities-heading"] li'))
            .map((li) => li.textContent.replace(/\\s+/g, ' ').trim()),
          matches: Array.from(document.querySelectorAll('[aria-labelledby="matches-heading"] li'))
            .map((li) => li.textContent.replace(/\\s+/g, ' ').trim().slice(0, 80)),
          subjectPhoto: window.__hgImage('[aria-label="Analysis results"] img'),
          subjectPhotoLoading: Boolean(document.querySelector('[aria-label="Analysis results"] img')),
          statusBadge: window.__hgText('[aria-label="Analysis status"]'),
          // Only the demo fixture's own wording counts as a leak: a live
          // retrieval may legitimately mention the same people (a query
          // like "1843" really does return the Ada Lovelace record), and
          // that is real data, not a fixture.
          mentionsFixture:
            /CERTIFICATE OF HISTORICAL RECORD|Ada Lovelace and Charles Babbage|DEMO FIXTURE/.test(text),
          demoMode: /DEMO MODE/.test(text),
        };
      })()`);
      Object.assign(entry, detail);

      if (entry.mentionsFixture) {
        throw new Error('the demo fixture text is on the page');
      }
      if (entry.status === 'Failed') {
        throw new Error(`analysis ended as Failed: ${entry.statusText}`);
      }
      if (entry.status === 'Partial' && !/Warning/i.test(entry.statusText)) {
        throw new Error('a partial analysis must show its warnings');
      }
      return {
        status: entry.status,
        entities: detail.entities.length,
        matches: detail.matches.length,
        extractedText: (detail.extractedText ?? '').slice(0, 80),
        subjectPhotoDecoded: Boolean(detail.subjectPhoto?.decoded),
        fixture: detail.mentionsFixture,
        demoMode: detail.demoMode,
      };
    }, report);
  }

  /* 5 — summary -------------------------------------------------------- */
  console.log('\n— browser diagnostics —');
  console.log(`  console errors/warnings: ${report.consoleErrors.length}`);
  for (const item of report.consoleErrors.slice(0, 8)) {
    console.log(`    [${item.level}] ${item.text}`);
  }
  console.log(`  failed requests: ${report.failedRequests.length}`);
  for (const item of report.failedRequests.slice(0, 8)) {
    console.log(
      `    ${item.error}${item.blocked ? ` (blocked: ${item.blockedReason})` : ''} [${item.type}]`,
    );
  }
  console.log(`  responses >= 400: ${report.badResponses.length}`);
  for (const item of report.badResponses.slice(0, 8)) {
    console.log(`    HTTP ${item.status} ${item.url}`);
  }
} catch (error) {
  console.error(`\nfatal: ${error.message ?? error}`);
  report.fatal = String(error.message ?? error);
} finally {
  report.finishedAt = new Date().toISOString();
  if (JSON_OUT) {
    writeFileSync(JSON_OUT, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
    console.log(`\nreport written to ${JSON_OUT}`);
  }
  const failed = report.steps.filter((item) => !item.ok);
  console.log(
    `\n${failed.length === 0 ? 'PASS' : 'FAIL'} — ${report.steps.length - failed.length}/${report.steps.length} steps ok` +
      `${report.browserVersion ? ` (${report.browserVersion})` : ''}`,
  );
  shutdown();
  process.exit(failed.length === 0 ? 0 : 1);
}
