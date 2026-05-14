// Playwright launch helper shared by the crawler and the test battery.
//
// Defaults:
//   - headless chromium
//   - 1440x900 viewport
//   - 30s default timeout for actions + navigation
//
// Every page produced via `newPage(context)` has console events and failed
// requests captured into in-memory arrays accessible as `page._capture`.

import { chromium } from 'playwright';

const VIEWPORT = { width: 1440, height: 900 };
const DEFAULT_TIMEOUT_MS = Number(process.env.QAGENT_NAV_TIMEOUT_MS) || 30000;

/**
 * @typedef {object} ConsoleEntry
 * @property {string} level    error | warning | log | info | debug
 * @property {string} text
 * @property {string} [url]    page URL when the event fired
 * @property {string} [location]
 * @property {number} ts       Date.now() at capture time.
 */

/**
 * @typedef {object} FailedRequest
 * @property {string} url
 * @property {string} method
 * @property {string|null} failure
 * @property {number|null} status
 * @property {string} resourceType
 * @property {number} ts
 */

/**
 * @typedef {object} PageCapture
 * @property {ConsoleEntry[]} console
 * @property {FailedRequest[]} failedRequests
 * @property {Array<{type: string, text: string, ts: number}>} pageErrors
 */

/**
 * Attach automatic capture of console + failed requests + page errors to a page.
 * The capture buffers are exposed as `page._capture`. Call `clearCapture(page)`
 * between routes to reset.
 *
 * @param {import('playwright').Page} page
 */
export function attachCapture(page) {
  /** @type {PageCapture} */
  const cap = { console: [], failedRequests: [], pageErrors: [] };
  // eslint-disable-next-line no-underscore-dangle
  page._capture = cap;

  page.on('console', (msg) => {
    cap.console.push({
      level: msg.type(),
      text: msg.text(),
      url: page.url(),
      location: (() => {
        const loc = msg.location();
        if (!loc?.url) return undefined;
        return `${loc.url}:${loc.lineNumber}:${loc.columnNumber}`;
      })(),
      ts: Date.now(),
    });
  });

  page.on('pageerror', (err) => {
    cap.pageErrors.push({
      type: err.name,
      text: err.message,
      ts: Date.now(),
    });
  });

  page.on('requestfailed', (req) => {
    cap.failedRequests.push({
      url: req.url(),
      method: req.method(),
      failure: req.failure()?.errorText ?? null,
      status: null,
      resourceType: req.resourceType(),
      ts: Date.now(),
    });
  });

  page.on('response', (res) => {
    const status = res.status();
    if (status >= 400) {
      cap.failedRequests.push({
        url: res.url(),
        method: res.request().method(),
        failure: null,
        status,
        resourceType: res.request().resourceType(),
        ts: Date.now(),
      });
    }
  });
}

/**
 * Reset the capture buffers without re-creating the page.
 *
 * @param {import('playwright').Page} page
 */
export function clearCapture(page) {
  // eslint-disable-next-line no-underscore-dangle
  if (page._capture) {
    // eslint-disable-next-line no-underscore-dangle
    page._capture.console.length = 0;
    // eslint-disable-next-line no-underscore-dangle
    page._capture.failedRequests.length = 0;
    // eslint-disable-next-line no-underscore-dangle
    page._capture.pageErrors.length = 0;
  }
}

/**
 * @returns {PageCapture} a defensive snapshot of the current capture buffer.
 */
export function snapshotCapture(page) {
  // eslint-disable-next-line no-underscore-dangle
  const cap = page._capture;
  if (!cap) return { console: [], failedRequests: [], pageErrors: [] };
  return {
    console: cap.console.slice(),
    failedRequests: cap.failedRequests.slice(),
    pageErrors: cap.pageErrors.slice(),
  };
}

/**
 * Launch a chromium browser with the shared defaults.
 *
 * @param {object} [opts]
 * @param {boolean} [opts.headless]
 * @param {string}  [opts.userAgent]
 * @param {{username: string, password: string}} [opts.httpCredentials]
 * @returns {Promise<{ browser: import('playwright').Browser, context: import('playwright').BrowserContext }>}
 */
export async function launch(opts = {}) {
  const browser = await chromium.launch({
    headless: opts.headless ?? true,
    args: ['--disable-dev-shm-usage'],
  });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    userAgent: opts.userAgent ?? process.env.QAGENT_USER_AGENT,
    httpCredentials: opts.httpCredentials,
    ignoreHTTPSErrors: true,
  });
  context.setDefaultTimeout(DEFAULT_TIMEOUT_MS);
  context.setDefaultNavigationTimeout(DEFAULT_TIMEOUT_MS);
  return { browser, context };
}

/**
 * Create a page with capture already attached.
 *
 * @param {import('playwright').BrowserContext} context
 * @returns {Promise<import('playwright').Page>}
 */
export async function newPage(context) {
  const page = await context.newPage();
  attachCapture(page);
  return page;
}

/**
 * Run `fn` with a browser + context. Handles teardown even on throw. Useful
 * for one-off jobs that don't want to manage the lifecycle.
 *
 * @template T
 * @param {(ctx: { browser: import('playwright').Browser, context: import('playwright').BrowserContext }) => Promise<T>} fn
 * @param {Parameters<typeof launch>[0]} [opts]
 * @returns {Promise<T>}
 */
export async function withBrowser(fn, opts) {
  const { browser, context } = await launch(opts);
  try {
    return await fn({ browser, context });
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
}

export const DEFAULTS = { viewport: VIEWPORT, timeoutMs: DEFAULT_TIMEOUT_MS };
