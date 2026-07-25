#!/usr/bin/env node
// CLI ledger integrity check. Green/red output, first break identified.
// Verifies every shop's chain by default, or a single shop with --shop=<id>.
//
// Usage:
//   node scripts/verify-ledger.js
//   node scripts/verify-ledger.js --shop=3

import { listRecords } from "../src/lib/nocodb.js";
import { verifyChain } from "../src/lib/ledger.js";

const GREEN = "\x1b[32m";
const RED = "\x1b[31m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";

function parseArgs(argv) {
  return Object.fromEntries(
    argv.map((a) => {
      const [k, v] = a.replace(/^--/, "").split("=");
      return [k, v ?? true];
    })
  );
}

async function shopIds(explicitShopId) {
  if (explicitShopId) return [explicitShopId];
  const { records } = await listRecords("shops", { all: true });
  return records.map((s) => s.Id);
}

export async function main() {
  const args = parseArgs(process.argv.slice(2));
  const ids = await shopIds(args.shop);

  if (ids.length === 0) {
    console.log("No shops found — nothing to verify.");
    return { ok: true, results: [] };
  }

  console.log(`Verifying audit ledger for ${ids.length} shop(s)...`);
  console.log("=".repeat(60));

  const results = [];
  for (const shopId of ids) {
    const result = await verifyChain(shopId);
    results.push({ shopId, ...result });
    if (result.ok) {
      console.log(`${GREEN}✓${RESET} shop ${shopId}: ${result.length} rows, chain intact`);
    } else {
      console.log(`${RED}✗ shop ${shopId}: BROKEN at seq ${result.brokenAtSeq}${RESET}`);
      console.log(`  ${DIM}${result.reason}${RESET}`);
    }
  }

  console.log("=".repeat(60));
  const allOk = results.every((r) => r.ok);
  console.log(allOk ? `${GREEN}All ledgers intact.${RESET}` : `${RED}One or more ledgers are broken — see above.${RESET}`);

  return { ok: allOk, results };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main()
    .then(({ ok }) => {
      process.exitCode = ok ? 0 : 1;
    })
    .catch((err) => {
      console.error("verify-ledger failed:", err.message);
      process.exitCode = 1;
    });
}
