// crawler/explorer.js
//
// Breadth-first, same-origin, depth-limited crawler. For each route:
//   1. navigate and wait for network idle
//   2. capture status code, page title, final URL (after redirects)
//   3. full-page screenshot at 1440x900 → screenshots/
//   4. inventory of interactive elements with stable selectors
//   5. console errors + 4xx/5xx requests collected during the visit
//   6. emit one Finding per console error / network failure
//
// Outputs:
//   - inventory.json   list of route entries
//   - findings.json    findings array (append mode — battery layer joins here)

import fs from 'node:fs/promises';
import path from 'node:path';
import { URL } from 'node:url';

import { newPage, clearCapture, snapshotCapture } from '../lib/browser.js';
import { makeFinding } from '../lib/schema.js';

/**
 * @typedef {object} ElementEntry
 * @property {string} kind          'link' | 'button' | 'input' | 'select' | 'textarea' | 'form' | 'role'
 * @property {string} selector      Stable selector preferring id > data-testid > aria-label > CSS path.
 * @property {string} [text]        Visible text or accessible name.
 * @property {string} [href]        For links.
 * @property {string} [type]        For inputs.
 * @property {string} [name]        For form fields.
 * @property {string} [role]        ARIA role.
 */

/**
 * @typedef {object} RouteEntry
 * @property {string} url           Requested URL.
 * @property {string} final_url     URL after redirects.
 * @property {number|null} status   HTTP status of the navigation response.
 * @property {string} title         document.title.
 * @property {string} screenshot_path  Relative path under the run dir.
 * @property {ElementEntry[]} elements
 * @property {Array<{type: string, message: string}>} errors
 * @property {number} depth
 * @property {number} elapsed_ms
 */

const SAME_ORIGIN_ONLY = true;

/**
 * Normalize a URL by stripping the hash and (optionally) deduping query params.
 *
 * @param {string} url
 * @param {boolean} dedupeQuery
 */
function normalizeUrl(url, dedupeQuery) {
  try {
    const u = new URL(url);
    u.hash = '';
    if (dedupeQuery) u.search = '';
    return u.toString();
  } catch {
    return url;
  }
}

/**
 * @param {string} a
 * @param {string} b
 */
function sameOrigin(a, b) {
  try {
    return new URL(a).origin === new URL(b).origin;
  } catch {
    return false;
  }
}

/**
 * Build a CSS-path selector for an element by walking up the DOM. This is the
 * fallback when no id/data-testid/aria-label is available.
 *
 * This function runs INSIDE the browser via `page.evaluate`.
 */
const SELECTOR_FN = `(el) => {
  if (!(el instanceof Element)) return null;
  const id = el.getAttribute('id');
  if (id && /^[A-Za-z][\\w\\-]*$/.test(id)) return '#' + CSS.escape(id);
  const testId = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test');
  if (testId) return '[data-testid=' + JSON.stringify(testId) + ']';
  const label = el.getAttribute('aria-label');
  if (label) {
    const tag = el.tagName.toLowerCase();
    return tag + '[aria-label=' + JSON.stringify(label) + ']';
  }
  const parts = [];
  let cur = el;
  while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
    let part = cur.tagName.toLowerCase();
    const cid = cur.getAttribute('id');
    if (cid && /^[A-Za-z][\\w\\-]*$/.test(cid)) {
      part = '#' + cid;
      parts.unshift(part);
      break;
    }
    if (cur.classList && cur.classList.length) {
      part += '.' + Array.from(cur.classList).slice(0, 2).map(c => CSS.escape(c)).join('.');
    }
    const parent = cur.parentElement;
    if (parent) {
      const sibs = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
      if (sibs.length > 1) {
        const idx = sibs.indexOf(cur) + 1;
        part += ':nth-of-type(' + idx + ')';
      }
    }
    parts.unshift(part);
    cur = cur.parentElement;
    if (parts.length >= 6) break;
  }
  return parts.join(' > ');
}`;

/**
 * Collect interactive elements and same-origin link hrefs from the current page.
 *
 * @param {import('playwright').Page} page
 * @returns {Promise<{elements: ElementEntry[], links: string[]}>}
 */
async function inventoryPage(page) {
  return page.evaluate(
    ({ selectorFnSrc }) => {
      // eslint-disable-next-line no-new-func
      const selectorFor = new Function('return (' + selectorFnSrc + ')')();
      const textOf = (el) => {
        const aria = el.getAttribute('aria-label');
        if (aria) return aria.trim().slice(0, 120);
        const t = (el.innerText || el.textContent || '').trim();
        return t.slice(0, 120);
      };
      /** @type {any[]} */
      const elements = [];
      const links = new Set();
      const SEL =
        'a[href], button, input:not([type="hidden"]), select, textarea, [role="button"], [role="link"], [role="tab"], [role="menuitem"], form';
      const seen = new Set();
      const all = document.querySelectorAll(SEL);
      for (const el of all) {
        if (seen.has(el)) continue;
        seen.add(el);
        const tag = el.tagName.toLowerCase();
        const entry = {
          kind:
            tag === 'a'
              ? 'link'
              : tag === 'button'
              ? 'button'
              : tag === 'form'
              ? 'form'
              : tag === 'input'
              ? 'input'
              : tag === 'select'
              ? 'select'
              : tag === 'textarea'
              ? 'textarea'
              : 'role',
          selector: selectorFor(el),
          text: textOf(el),
        };
        const role = el.getAttribute('role');
        if (role) entry.role = role;
        if (tag === 'a') {
          const href = el.getAttribute('href');
          if (href) {
            try {
              const abs = new URL(href, document.baseURI).toString();
              entry.href = abs;
              links.add(abs);
            } catch {
              entry.href = href;
            }
          }
        }
        if (tag === 'input') entry.type = el.getAttribute('type') || 'text';
        if (el.getAttribute('name')) entry.name = el.getAttribute('name');
        elements.push(entry);
      }
      return { elements, links: Array.from(links) };
    },
    { selectorFnSrc: SELECTOR_FN },
  );
}

/**
 * Convert captured console + failed requests into Finding records.
 *
 * @param {string} url
 * @param {string} screenshotPath
 * @param {import('../lib/browser.js').PageCapture} cap
 */
function findingsFromCapture(url, screenshotPath, cap) {
  const findings = [];
  for (const c of cap.console) {
    if (c.level !== 'error') continue;
    findings.push(
      makeFinding({
        source_agent: 'crawler',
        category: 'console_error',
        raw_severity: 'medium',
        url,
        title: 'Console error: ' + c.text.slice(0, 80),
        description: c.text,
        evidence: {
          console_message: c.text,
          console_level: c.level,
          screenshot_path: screenshotPath,
        },
        tags: ['runtime'],
      }),
    );
  }
  for (const e of cap.pageErrors) {
    findings.push(
      makeFinding({
        source_agent: 'crawler',
        category: 'console_error',
        raw_severity: 'high',
        url,
        title: 'Uncaught ' + e.type + ': ' + e.text.slice(0, 80),
        description: e.text,
        evidence: { console_message: e.text, screenshot_path: screenshotPath },
        tags: ['runtime', 'uncaught'],
      }),
    );
  }
  for (const r of cap.failedRequests) {
    // Some failed requests are noisy (favicon, analytics blocked by clients).
    // Keep them but tag and rate as low if obviously cosmetic.
    const cosmetic =
      /\/(favicon\.ico|sw\.js)$/.test(r.url) ||
      /\b(google-analytics|googletagmanager|hotjar|segment|amplitude)\b/.test(
        r.url,
      );
    const isServerError = (r.status ?? 0) >= 500;
    const severity = isServerError
      ? 'high'
      : cosmetic
      ? 'low'
      : (r.status ?? 0) >= 400
      ? 'medium'
      : 'medium';
    findings.push(
      makeFinding({
        source_agent: 'crawler',
        category: r.status ? 'http_status' : 'network_error',
        raw_severity: severity,
        url,
        title:
          (r.status ? `${r.status} ` : 'Request failed: ') +
          new URL(r.url, url).pathname,
        description: r.failure
          ? `Request to ${r.url} failed: ${r.failure}`
          : `Request to ${r.url} returned ${r.status}`,
        evidence: {
          request_url: r.url,
          status_code: r.status ?? null,
          method: r.method,
          screenshot_path: screenshotPath,
        },
        tags: cosmetic ? ['network', 'cosmetic'] : ['network'],
      }),
    );
  }
  return findings;
}

/**
 * Wait for the page to settle. Tries networkidle, then a brief wait for SPA
 * frameworks to hydrate if their root markers are present.
 *
 * @param {import('playwright').Page} page
 */
async function waitForReady(page) {
  await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
  // SPA hydration probe: look for common framework anchors.
  await page
    .waitForFunction(
      () => {
        const root =
          document.getElementById('root') ||
          document.getElementById('app') ||
          document.querySelector('[data-reactroot], [data-server-rendered], [ng-version]');
        if (!root) return true; // not an SPA we can detect — give up waiting.
        return root.childElementCount > 0;
      },
      undefined,
      { timeout: 5000 },
    )
    .catch(() => {});
}

/**
 * Crawl a site BFS from `baseUrl`, depth-limited, same-origin.
 *
 * @param {object} opts
 * @param {import('playwright').BrowserContext} opts.context
 * @param {string} opts.baseUrl
 * @param {string} opts.runDir              Output directory (e.g. runs/<ts>).
 * @param {number} [opts.maxDepth]          Default 2.
 * @param {number} [opts.maxRoutes]         Default 40.
 * @param {boolean} [opts.dedupeQuery]      Treat /x?a=1 and /x?a=2 as one route.
 * @returns {Promise<{inventory: RouteEntry[], findings: import('../lib/schema.js').Finding[]}>}
 */
export async function crawl({
  context,
  baseUrl,
  runDir,
  maxDepth = Number(process.env.QAGENT_MAX_DEPTH) || 2,
  maxRoutes = Number(process.env.QAGENT_MAX_ROUTES) || 40,
  dedupeQuery = true,
}) {
  const screenshotsDir = path.join(runDir, 'screenshots');
  await fs.mkdir(screenshotsDir, { recursive: true });

  /** @type {RouteEntry[]} */
  const inventory = [];
  /** @type {import('../lib/schema.js').Finding[]} */
  const findings = [];

  const seen = new Set();
  const queue = [{ url: normalizeUrl(baseUrl, dedupeQuery), depth: 0 }];
  seen.add(queue[0].url);

  const page = await newPage(context);

  let visited = 0;
  while (queue.length && visited < maxRoutes) {
    const { url, depth } = queue.shift();
    const started = Date.now();
    clearCapture(page);

    let status = null;
    let finalUrl = url;
    let title = '';
    /** @type {ElementEntry[]} */
    let elements = [];
    /** @type {string[]} */
    let links = [];
    /** @type {Array<{type: string, message: string}>} */
    const navErrors = [];

    try {
      const resp = await page.goto(url, { waitUntil: 'domcontentloaded' });
      if (resp) status = resp.status();
      finalUrl = page.url();
      await waitForReady(page);
      title = await page.title().catch(() => '');
      const inv = await inventoryPage(page);
      elements = inv.elements;
      links = inv.links;
    } catch (err) {
      navErrors.push({
        type: 'navigation',
        message: /** @type {Error} */ (err).message,
      });
    }

    const slug =
      `route-${String(visited + 1).padStart(3, '0')}-` +
      (normalizeUrl(finalUrl || url, true)
        .replace(/^https?:\/\//, '')
        .replace(/[^A-Za-z0-9_.-]+/g, '_')
        .slice(0, 80) || 'index');
    const screenshotRel = path.join('screenshots', `${slug}.png`);
    const screenshotAbs = path.join(runDir, screenshotRel);
    try {
      await page.screenshot({ path: screenshotAbs, fullPage: true });
    } catch (err) {
      navErrors.push({
        type: 'screenshot',
        message: /** @type {Error} */ (err).message,
      });
    }

    const cap = snapshotCapture(page);
    const routeFindings = findingsFromCapture(finalUrl || url, screenshotRel, cap);
    // Bad HTTP status itself is a finding.
    if (status && status >= 400) {
      routeFindings.push(
        makeFinding({
          source_agent: 'crawler',
          category: 'http_status',
          raw_severity: status >= 500 ? 'high' : 'medium',
          url: finalUrl || url,
          title: `Route returned ${status}`,
          description: `Navigation to ${url} returned HTTP ${status}.`,
          evidence: {
            status_code: status,
            request_url: url,
            screenshot_path: screenshotRel,
          },
          tags: ['route', 'http'],
        }),
      );
    }
    findings.push(...routeFindings);

    inventory.push({
      url,
      final_url: finalUrl || url,
      status,
      title,
      screenshot_path: screenshotRel,
      elements,
      errors: [
        ...navErrors,
        ...cap.pageErrors.map((e) => ({ type: 'pageerror', message: e.text })),
        ...cap.console
          .filter((c) => c.level === 'error')
          .map((c) => ({ type: 'console', message: c.text })),
      ],
      depth,
      elapsed_ms: Date.now() - started,
    });
    visited += 1;

    if (depth < maxDepth) {
      for (const next of links) {
        if (!SAME_ORIGIN_ONLY || sameOrigin(next, baseUrl)) {
          const norm = normalizeUrl(next, dedupeQuery);
          if (!seen.has(norm) && /^https?:/i.test(norm)) {
            seen.add(norm);
            queue.push({ url: norm, depth: depth + 1 });
          }
        }
      }
    }
  }

  await page.close().catch(() => {});
  return { inventory, findings };
}

/**
 * Append findings to a findings.json file. Creates the file if missing.
 * Concurrency note: this is meant for a single-process pipeline.
 *
 * @param {string} findingsPath
 * @param {import('../lib/schema.js').Finding[]} newFindings
 */
export async function appendFindings(findingsPath, newFindings) {
  let existing = [];
  try {
    const txt = await fs.readFile(findingsPath, 'utf8');
    existing = JSON.parse(txt);
    if (!Array.isArray(existing)) existing = [];
  } catch (err) {
    if (/** @type {NodeJS.ErrnoException} */ (err).code !== 'ENOENT') throw err;
  }
  const merged = existing.concat(newFindings);
  await fs.writeFile(findingsPath, JSON.stringify(merged, null, 2));
  return merged.length;
}

/**
 * Write inventory.json.
 *
 * @param {string} inventoryPath
 * @param {RouteEntry[]} inventory
 */
export async function writeInventory(inventoryPath, inventory) {
  await fs.writeFile(inventoryPath, JSON.stringify(inventory, null, 2));
}
