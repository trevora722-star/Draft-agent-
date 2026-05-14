#!/usr/bin/env node
// Thin CLI wrapper around intelligence/report.js#runIntel.
//
// Usage:
//   node intelligence/run-intel.js --findings runs/<ts>/findings.json --out runs/<ts>
//   node intelligence/run-intel.js --findings test-fixtures/sample-findings.json --out runs/fixture

import path from 'node:path';
import fs from 'node:fs/promises';
import { Command } from 'commander';
import dotenv from 'dotenv';

import { runIntel } from './report.js';

dotenv.config();

const program = new Command();
program
  .name('qagent-intel')
  .description('Run the QAgent intelligence pass against a findings.json file.')
  .requiredOption('--findings <path>', 'Path to findings.json')
  .option('--inventory <path>', 'Path to inventory.json (optional)')
  .option('--out <dir>', 'Output directory')
  .option('--target <url>', 'Target URL (cosmetic in report)', 'unknown://target')
  .parse(process.argv);

const opts = program.opts();

const runDir = path.resolve(
  opts.out || path.join('runs', new Date().toISOString().replace(/[:.]/g, '-')),
);
await fs.mkdir(runDir, { recursive: true });

const findingsAbs = path.resolve(opts.findings);
const findingsInRun = path.join(runDir, 'findings.json');
if (path.resolve(findingsInRun) !== findingsAbs) {
  await fs.copyFile(findingsAbs, findingsInRun);
}

let inventoryInRun;
if (opts.inventory) {
  const inventoryAbs = path.resolve(opts.inventory);
  inventoryInRun = path.join(runDir, 'inventory.json');
  if (path.resolve(inventoryInRun) !== inventoryAbs) {
    await fs.copyFile(inventoryAbs, inventoryInRun);
  }
}

await runIntel({
  findingsPath: findingsInRun,
  inventoryPath: inventoryInRun,
  runDir,
  target: opts.target,
});
console.log(`[intel] done. open ${path.join(runDir, 'report.html')}`);
