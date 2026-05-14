// Authentication helpers for the QAgent browser layer.
//
// Three modes:
//   - none   : do nothing.
//   - basic  : pass HTTP basic credentials at context-create time. Parses
//              `user:pass` from CLI --auth or env vars.
//   - form   : navigate to the target URL, detect a login form, fill it, and
//              wait for navigation. A `verifySelector` (post-login element)
//              or `verifyUrlRegex` (URL pattern after login) confirms success.

/**
 * @typedef {'none' | 'basic' | 'form'} AuthMode
 */

/**
 * @typedef {object} AuthConfig
 * @property {AuthMode} mode
 * @property {string}   [username]
 * @property {string}   [password]
 * @property {string}   [loginUrl]        For form mode; defaults to base URL.
 * @property {string}   [userSelector]    Override username field selector.
 * @property {string}   [passSelector]    Override password field selector.
 * @property {string}   [submitSelector]  Override submit selector.
 * @property {string}   [verifySelector]  Element that should appear post-login.
 * @property {RegExp|string} [verifyUrlRegex] URL pattern that should match after login.
 */

/**
 * Parse a CLI-friendly `user:pass` string. Returns undefined for empty input.
 *
 * @param {string|undefined} raw
 * @returns {{username: string, password: string} | undefined}
 */
export function parseCredentials(raw) {
  if (!raw) return undefined;
  const idx = raw.indexOf(':');
  if (idx === -1) return { username: raw, password: '' };
  return { username: raw.slice(0, idx), password: raw.slice(idx + 1) };
}

/**
 * Build an AuthConfig from CLI flags + env. The `--auth` flag is a single
 * `user:pass` string used for basic and form modes both.
 *
 * @param {object} flags
 * @param {AuthMode} [flags.authMode]
 * @param {string}   [flags.auth]            user:pass
 * @param {string}   [flags.loginUrl]
 * @returns {AuthConfig}
 */
export function configFromFlags(flags) {
  const mode = flags.authMode || 'none';
  const creds =
    parseCredentials(flags.auth) ||
    (process.env.QAGENT_AUTH_USER
      ? {
          username: process.env.QAGENT_AUTH_USER,
          password: process.env.QAGENT_AUTH_PASS ?? '',
        }
      : undefined);
  return {
    mode,
    username: creds?.username,
    password: creds?.password,
    loginUrl: flags.loginUrl,
  };
}

/**
 * Some plausible default selectors for common login forms. The form login
 * helper tries these in order if no override was provided.
 */
const DEFAULT_USER_SELECTORS = [
  'input[type="email"]',
  'input[name="email"]',
  'input[name="username"]',
  'input[name="user"]',
  'input[id*="email" i]',
  'input[id*="user" i]',
];
const DEFAULT_PASS_SELECTORS = [
  'input[type="password"]',
  'input[name="password"]',
  'input[id*="password" i]',
];
const DEFAULT_SUBMIT_SELECTORS = [
  'button[type="submit"]',
  'input[type="submit"]',
  'button:has-text("Sign in")',
  'button:has-text("Log in")',
  'button:has-text("Login")',
];

/**
 * Probe a list of selectors and return the first that matches.
 *
 * @param {import('playwright').Page} page
 * @param {string[]} selectors
 * @returns {Promise<string|null>}
 */
async function firstMatching(page, selectors) {
  for (const sel of selectors) {
    const count = await page.locator(sel).count().catch(() => 0);
    if (count > 0) return sel;
  }
  return null;
}

/**
 * Execute form login on the given page. Throws if the post-login verification
 * does not pass within the navigation timeout.
 *
 * @param {import('playwright').Page} page
 * @param {AuthConfig} cfg
 * @returns {Promise<{userSelector: string, passSelector: string, submitSelector: string, verifiedBy: 'selector'|'url'|'navigation'}>}
 */
export async function performFormLogin(page, cfg) {
  if (!cfg.username || cfg.password == null) {
    throw new Error('form auth requires --auth user:pass');
  }
  if (!cfg.loginUrl) {
    throw new Error('form auth requires loginUrl');
  }

  await page.goto(cfg.loginUrl, { waitUntil: 'domcontentloaded' });

  const userSel =
    cfg.userSelector ?? (await firstMatching(page, DEFAULT_USER_SELECTORS));
  const passSel =
    cfg.passSelector ?? (await firstMatching(page, DEFAULT_PASS_SELECTORS));
  const submitSel =
    cfg.submitSelector ?? (await firstMatching(page, DEFAULT_SUBMIT_SELECTORS));

  if (!userSel || !passSel) {
    throw new Error(
      `Could not locate username/password fields on ${cfg.loginUrl}. Pass --user-selector/--pass-selector.`,
    );
  }

  await page.fill(userSel, cfg.username);
  await page.fill(passSel, cfg.password);

  const navPromise = page
    .waitForLoadState('networkidle', { timeout: 15000 })
    .catch(() => {});

  if (submitSel) {
    await page.click(submitSel);
  } else {
    // Submit by pressing Enter on the password field.
    await page.locator(passSel).press('Enter');
  }
  await navPromise;

  if (cfg.verifySelector) {
    await page.waitForSelector(cfg.verifySelector, { timeout: 15000 });
    return {
      userSelector: userSel,
      passSelector: passSel,
      submitSelector: submitSel ?? '<enter>',
      verifiedBy: 'selector',
    };
  }
  if (cfg.verifyUrlRegex) {
    const re =
      typeof cfg.verifyUrlRegex === 'string'
        ? new RegExp(cfg.verifyUrlRegex)
        : cfg.verifyUrlRegex;
    if (!re.test(page.url())) {
      throw new Error(
        `Post-login URL ${page.url()} did not match ${re.toString()}`,
      );
    }
    return {
      userSelector: userSel,
      passSelector: passSel,
      submitSelector: submitSel ?? '<enter>',
      verifiedBy: 'url',
    };
  }
  // No explicit verification — accept successful navigation.
  return {
    userSelector: userSel,
    passSelector: passSel,
    submitSelector: submitSel ?? '<enter>',
    verifiedBy: 'navigation',
  };
}

/**
 * Resolve a config into browser launch options. Useful for HTTP basic auth,
 * which needs to be passed at context creation, not after.
 *
 * @param {AuthConfig} cfg
 * @returns {{httpCredentials?: {username: string, password: string}}}
 */
export function browserAuthOptions(cfg) {
  if (cfg.mode !== 'basic') return {};
  if (!cfg.username) {
    throw new Error('basic auth requires --auth user:pass');
  }
  return {
    httpCredentials: {
      username: cfg.username,
      password: cfg.password ?? '',
    },
  };
}

/**
 * Apply auth to an already-built page. For `basic` this is a no-op (handled at
 * context level); for `form` it performs the login flow.
 *
 * @param {import('playwright').Page} page
 * @param {AuthConfig} cfg
 * @returns {Promise<{mode: AuthMode, info?: object}>}
 */
export async function applyAuth(page, cfg) {
  if (cfg.mode === 'none' || cfg.mode === 'basic') return { mode: cfg.mode };
  if (cfg.mode === 'form') {
    const info = await performFormLogin(page, cfg);
    return { mode: 'form', info };
  }
  throw new Error(`Unknown auth mode: ${cfg.mode}`);
}
