#!/usr/bin/env node
// QAgent CLI entry point.
//
// Usage:
//   node run.js <url> [options]
//
// Pipelines the three sessions:
//   1. crawler/explorer.js  (always runs)
//   2. battery/*            (optional — try/catch dynamic import)
//   3. intelligence/*       (optional — try/catch dynamic import)

import fs from 'node:fs/promises';
import path from 'node:path';
import { Command } from 'commander';
import dotenv from 'dotenv';

import { launch } from './lib/browser.js';
import { configFromFlags, applyAuth, browserAuthOptions } from './lib/auth.js';
import {
  crawl,
  appendFindings,
  writeInventory,
} from './crawler/explorer.js';

dotenv.config();

const program = new Command();
program
  .name('qagent')
  .description('Automated QA: crawl, test, and analyze a web app.')
  .argument('<url>', 'Base URL to crawl')
  .option('--max-depth <n>', 'BFS depth limit', (v) => parseInt(v, 10))
  .option('--max-routes <n>', 'Hard route cap', (v) => parseInt(v, 10))
  .option(
    '--auth-mode <mode>',
    'Auth mode: none | basic | form',
    'none',
  )
  .option('--auth <user:pass>', 'Credentials for basic/form auth')
  .option('--login-url <url>', 'Login page URL (form auth)')
  .option('--out <dir>', 'Output directory (default runs/<ts>)')
  .option('--no-dedupe-query', 'Treat /x?a=1 and /x?a=2 as distinct routes')
  .option('--skip-battery', 'Skip the test battery layer even if present')
  .option('--skip-intel', 'Skip the intelligence / reporting layer')
  .parse(process.argv);

const opts = program.opts();
const [baseUrl] = program.args;

const ts = new Date().toISOString().replace(/[:.]/g, '-');
const runDir = path.resolve(opts.out || path.join('runs', ts));
await fs.mkdir(runDir, { recursive: true });

const inventoryPath = path.join(runDir, 'inventory.json');
const findingsPath = path.join(runDir, 'findings.json');

console.log(`[qagent] target=${baseUrl}`);
console.log(`[qagent] run dir=${runDir}`);

// --- auth -------------------------------------------------------------------
const authCfg = configFromFlags({
  authMode: opts.authMode,
  auth: opts.auth,
  loginUrl: opts.loginUrl || baseUrl,
});

const launchOpts = {
  ...browserAuthOptions(authCfg),
};

const { browser, context } = await launch(launchOpts);

try {
  // Form login uses a throwaway page; basic/none does nothing.
  if (authCfg.mode === 'form') {
    const loginPage = await context.newPage();
    await applyAuth(loginPage, authCfg);
    await loginPage.close();
    console.log(`[qagent] auth=form (logged in)`);
  } else if (authCfg.mode === 'basic') {
    console.log(`[qagent] auth=basic (credentials attached)`);
  } else {
    console.log(`[qagent] auth=none`);
  }

  // --- crawler ---------------------------------------------------------------
  console.log('[qagent] starting crawl…');
  const { inventory, findings } = await crawl({
    context,
    baseUrl,
    runDir,
    maxDepth: opts.maxDepth,
    maxRoutes: opts.maxRoutes,
    dedupeQuery: opts.dedupeQuery !== false,
  });
  await writeInventory(inventoryPath, inventory);
  const totalAfterCrawl = await appendFindings(findingsPath, findings);
  console.log(
    `[qagent] crawl complete: ${inventory.length} routes, ${findings.length} findings (total ${totalAfterCrawl}).`,
  );

  // --- battery (Session B) ---------------------------------------------------
  if (!opts.skipBattery) {
    try {
      const mod = await import('./battery/run.js');
      if (typeof mod.runBattery === 'function') {
        console.log('[qagent] running battery…');
        const newFindings = await mod.runBattery({
          context,
          inventory,
          runDir,
        });
        if (Array.isArray(newFindings) && newFindings.length) {
          const total = await appendFindings(findingsPath, newFindings);
          console.log(
            `[qagent] battery complete: ${newFindings.length} findings (total ${total}).`,
          );
        } else {
          console.log('[qagent] battery complete: 0 findings.');
        }
      }
    } catch (err) {
      if (/** @type {NodeJS.ErrnoException} */ (err).code === 'ERR_MODULE_NOT_FOUND') {
        console.log('[qagent] battery layer not present — skipping.');
      } else {
        console.warn('[qagent] battery layer failed:', err.message);
      }
    }
  }

  // --- intelligence (Session C) ---------------------------------------------
  if (!opts.skipIntel) {
    try {
      const mod = await import('./intelligence/run-intel.js');
      if (typeof mod.runIntel === 'function') {
        console.log('[qagent] running intelligence pass…');
        await mod.runIntel({
          findingsPath,
          inventoryPath,
          runDir,
          target: baseUrl,
        });
        console.log('[qagent] intelligence complete.');
      }
    } catch (err) {
      if (/** @type {NodeJS.ErrnoException} */ (err).code === 'ERR_MODULE_NOT_FOUND') {
        console.log('[qagent] intelligence layer not present — skipping.');
      } else {
        console.warn('[qagent] intelligence layer failed:', err.message);
      }
    }
  }

  console.log(`[qagent] done. Artifacts in ${runDir}`);
} finally {
  await context.close().catch(() => {});
  await browser.close().catch(() => {});
}
