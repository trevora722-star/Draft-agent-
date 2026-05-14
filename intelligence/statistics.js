// intelligence/statistics.js
//
// Pure (deterministic) quantitative analysis over findings + bugs + inventory.
// No Claude calls here.
//
// Output schema (statistics.json):
//   {
//     totals: { findings, bugs, dedup_ratio },
//     by_severity:    { critical: { count, pct }, ... },
//     by_category:    { ... },
//     by_source_agent:{ ... },
//     by_route:       { "url": count, ... }      sorted desc
//     top_selectors:  [ { selector, count } ]    top 10
//     form_correlation: { n, r, note } | null
//     co_occurrence:  { matrix: { catA: { catB: count } }, pairs: [ ... ] }
//   }

import fs from 'node:fs/promises';

const SEV_ORDER = ['critical', 'high', 'medium', 'low', 'info'];

/**
 * Increment a key in a Map.
 * @template K
 * @param {Map<K, number>} m
 * @param {K} k
 */
function bump(m, k) {
  m.set(k, (m.get(k) || 0) + 1);
}

/**
 * Map -> object sorted desc by count, with percentages.
 *
 * @param {Map<string, number>} m
 * @param {number} total
 */
function pctMap(m, total) {
  /** @type {Array<[string, number]>} */
  const entries = Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  /** @type {Record<string, {count: number, pct: number}>} */
  const out = {};
  for (const [k, v] of entries) {
    out[k] = {
      count: v,
      pct: total === 0 ? 0 : Number((v / total).toFixed(3)),
    };
  }
  return out;
}

/**
 * Pearson correlation coefficient. Returns null when input is degenerate.
 *
 * @param {number[]} xs
 * @param {number[]} ys
 */
function pearson(xs, ys) {
  if (xs.length !== ys.length || xs.length < 2) return null;
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let dx = 0;
  let dy = 0;
  for (let i = 0; i < n; i++) {
    const ex = xs[i] - mx;
    const ey = ys[i] - my;
    num += ex * ey;
    dx += ex * ex;
    dy += ey * ey;
  }
  const denom = Math.sqrt(dx * dy);
  if (!denom) return null;
  return Number((num / denom).toFixed(3));
}

/**
 * Top-N entries from a Map.
 *
 * @param {Map<string, number>} m
 * @param {number} n
 */
function topN(m, n) {
  return Array.from(m.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([selector, count]) => ({ selector, count }));
}

/**
 * Form complexity per route = count of input/select/textarea elements in the
 * inventory entry. Returns Map<route_url, inputCount>.
 *
 * @param {any[]} inventory
 */
function formComplexityMap(inventory) {
  /** @type {Map<string, number>} */
  const m = new Map();
  for (const r of inventory) {
    const inputs = (r.elements || []).filter((e) =>
      ['input', 'select', 'textarea'].includes(e.kind),
    ).length;
    m.set(r.final_url || r.url, inputs);
  }
  return m;
}

/**
 * Co-occurrence: for each route, take the set of distinct categories observed,
 * then increment matrix[catA][catB] for every unordered pair.
 *
 * @param {any[]} findings
 */
function coOccurrence(findings) {
  /** @type {Map<string, Set<string>>} */
  const byRoute = new Map();
  for (const f of findings) {
    if (!byRoute.has(f.url)) byRoute.set(f.url, new Set());
    byRoute.get(f.url).add(f.category);
  }
  /** @type {Record<string, Record<string, number>>} */
  const matrix = {};
  /** @type {Map<string, number>} */
  const pairCounts = new Map();
  for (const cats of byRoute.values()) {
    const arr = Array.from(cats).sort();
    for (let i = 0; i < arr.length; i++) {
      for (let j = i + 1; j < arr.length; j++) {
        const a = arr[i];
        const b = arr[j];
        matrix[a] = matrix[a] || {};
        matrix[a][b] = (matrix[a][b] || 0) + 1;
        bump(pairCounts, `${a}::${b}`);
      }
    }
  }
  const pairs = Array.from(pairCounts.entries())
    .sort((x, y) => y[1] - x[1])
    .slice(0, 12)
    .map(([k, count]) => {
      const [a, b] = k.split('::');
      return { a, b, count };
    });
  return { matrix, pairs };
}

/**
 * Compute statistics from findings + bugs + (optional) inventory.
 *
 * @param {object} input
 * @param {any[]} input.findings
 * @param {any[]} input.bugs
 * @param {any[]} [input.inventory]
 * @returns {object}
 */
export function computeStatistics({ findings, bugs, inventory = [] }) {
  // Distributions
  const bySev = new Map();
  const byCat = new Map();
  const byAgent = new Map();
  const byRoute = new Map();
  const bySelector = new Map();

  for (const f of findings) {
    bump(byAgent, f.source_agent);
    bump(byCat, f.category);
    bump(byRoute, f.url);
    const sel = f.selector_pattern || f.evidence?.selector;
    if (sel) bump(bySelector, sel);
  }
  for (const b of bugs) {
    bump(bySev, b.final_severity);
  }

  const totalFindings = findings.length;
  const totalBugs = bugs.length;
  const dedupRatio =
    totalFindings === 0
      ? 0
      : Number((1 - totalBugs / totalFindings).toFixed(3));

  // Severity object always includes all keys (UI charts expect this).
  /** @type {Record<string, {count: number, pct: number}>} */
  const bySeverity = {};
  for (const s of SEV_ORDER) {
    const c = bySev.get(s) || 0;
    bySeverity[s] = {
      count: c,
      pct: totalBugs === 0 ? 0 : Number((c / totalBugs).toFixed(3)),
    };
  }

  // Form complexity correlation: x = input count, y = bug count per route.
  let formCorr = null;
  if (inventory.length >= 5) {
    const inputs = formComplexityMap(inventory);
    const bugsByRoute = new Map();
    for (const b of bugs) {
      for (const r of b.affected_routes) bump(bugsByRoute, r);
    }
    const routes = Array.from(inputs.keys()).filter((r) => inputs.get(r) > 0 || bugsByRoute.get(r));
    if (routes.length >= 5) {
      const xs = routes.map((r) => inputs.get(r) || 0);
      const ys = routes.map((r) => bugsByRoute.get(r) || 0);
      const r = pearson(xs, ys);
      formCorr = {
        n: routes.length,
        r,
        note:
          r == null
            ? 'Insufficient variance to compute.'
            : Math.abs(r) < 0.2
            ? 'No meaningful correlation between form complexity and bug count.'
            : r > 0
            ? 'Positive correlation: more inputs tend to coincide with more bugs.'
            : 'Negative correlation: forms with more inputs have fewer reported bugs.',
      };
    }
  }

  const co = coOccurrence(findings);

  return {
    totals: {
      findings: totalFindings,
      bugs: totalBugs,
      dedup_ratio: dedupRatio,
    },
    by_severity: bySeverity,
    by_category: pctMap(byCat, totalFindings),
    by_source_agent: pctMap(byAgent, totalFindings),
    by_route: Object.fromEntries(
      Array.from(byRoute.entries()).sort((a, b) => b[1] - a[1]),
    ),
    top_selectors: topN(bySelector, 10),
    form_correlation: formCorr,
    co_occurrence: co,
  };
}

/**
 * Read files, compute, write out.
 *
 * @param {object} paths
 * @param {string} paths.findingsPath
 * @param {string} paths.bugsPath
 * @param {string} [paths.inventoryPath]
 * @param {string} paths.outPath
 */
export async function statisticsFile({
  findingsPath,
  bugsPath,
  inventoryPath,
  outPath,
}) {
  const findings = JSON.parse(await fs.readFile(findingsPath, 'utf8'));
  const bugs = JSON.parse(await fs.readFile(bugsPath, 'utf8'));
  let inventory = [];
  if (inventoryPath) {
    try {
      inventory = JSON.parse(await fs.readFile(inventoryPath, 'utf8'));
    } catch {
      inventory = [];
    }
  }
  const stats = computeStatistics({ findings, bugs, inventory });
  await fs.writeFile(outPath, JSON.stringify(stats, null, 2));
  return stats;
}

export const _internal = { pearson, coOccurrence };
