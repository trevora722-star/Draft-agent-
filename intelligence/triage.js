// intelligence/triage.js
//
// Two-pass deduplication + severity recalibration over raw findings.
//
//   Pass 1 — exact-match clustering on (category, normalized title, selector_pattern).
//            Very cheap; collapses the high-volume noise (same console error on
//            every route, same a11y rule across the same component).
//   Pass 2 — semantic merge via Claude. For each candidate pair (same category,
//            different exact keys), ask the model to decide if they share a root
//            cause. Recorded reasoning is preserved in the Bug record.
//
//   Pass 3 — severity recalibration. Single Claude Opus 4.7 call per cluster
//            (batched into one big request) returns final_severity +
//            severity_reasoning + confidence.
//
// Input:  runs/<ts>/findings.json
// Output: runs/<ts>/bugs.json

import fs from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

import { askClaude } from '../lib/claude.js';
import { BugSchema, validateBug } from '../lib/schema.js';

const OPUS = process.env.QAGENT_MODEL_OPUS || 'claude-opus-4-7';

// --- helpers ----------------------------------------------------------------

/**
 * Normalize a title: lowercase, collapse whitespace, strip numbers and IDs
 * that vary between routes ("Route returned 404", "Error 12345 at /foo").
 *
 * @param {string} s
 */
function normalizeTitle(s) {
  return s
    .toLowerCase()
    .replace(/0x[0-9a-f]+/g, '<hex>')
    .replace(/\b\d{2,}\b/g, '<n>')
    .replace(/["']/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Reduce a selector to a structural pattern for clustering.
 *   '#submit-btn-3'           -> '#submit-btn-<n>'
 *   '[data-testid="row-12"]'  -> '[data-testid=row-<n>]'
 *   'div > a:nth-of-type(4)'  -> 'div > a:nth-of-type(<n>)'
 *
 * @param {string|null|undefined} sel
 */
function selectorPattern(sel) {
  if (!sel) return null;
  return sel
    .replace(/"/g, '')
    .replace(/\b\d{1,}\b/g, '<n>')
    .replace(/:nth-of-type\([^)]+\)/g, ':nth-of-type(<n>)');
}

/**
 * @param {import('../lib/schema.js').Finding} f
 */
function clusterKey(f) {
  return [
    f.category,
    normalizeTitle(f.title),
    selectorPattern(f.selector_pattern || f.evidence?.selector || ''),
  ].join('||');
}

const SEVERITY_RANK = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };

/**
 * Pick the worst raw_severity in a cluster (used as initial estimate before
 * Claude recalibrates).
 *
 * @param {import('../lib/schema.js').Finding[]} fs
 */
function worstSeverity(fs) {
  return fs
    .map((f) => f.raw_severity)
    .reduce(
      (acc, s) => (SEVERITY_RANK[s] > SEVERITY_RANK[acc] ? s : acc),
      'info',
    );
}

// --- pass 1: exact clustering ----------------------------------------------

/**
 * @param {import('../lib/schema.js').Finding[]} findings
 * @returns {Map<string, import('../lib/schema.js').Finding[]>}
 */
function exactCluster(findings) {
  const map = new Map();
  for (const f of findings) {
    const k = clusterKey(f);
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(f);
  }
  return map;
}

// --- pass 2: semantic merge --------------------------------------------------
//
// Within each (category) bucket we ask Claude to merge clusters that share a
// root cause. We only consider categories with >1 cluster, and we pass a small
// digest per cluster, not full findings.

/**
 * @param {Array<{key: string, sample: import('../lib/schema.js').Finding, size: number}>} digests
 * @returns {Promise<Array<{merged: string[], reason: string}>>}
 */
async function semanticMerge(digests) {
  if (digests.length < 2) return [];
  const prompt = [
    'You are deduplicating QA findings. Each item below is one cluster of',
    'observations that already passed an exact-match dedupe. Two clusters',
    'belong to the SAME ROOT CAUSE if fixing one would resolve the other.',
    'Otherwise they are independent and must stay separate. Be conservative:',
    'merge only when you are confident a single fix resolves both.',
    '',
    'Clusters:',
    ...digests.map(
      (d, i) =>
        `(${i}) key=${d.key} | size=${d.size} | title=${d.sample.title} | url=${d.sample.url} | desc=${d.sample.description.slice(0, 200)}`,
    ),
    '',
    'Return a JSON array of merge groups. Each group: { "merged": [<cluster indices>], "reason": "one sentence" }.',
    'Singleton groups (no merge) MUST NOT be returned. Indices must reference the list above.',
    'If nothing merges, return [].',
  ].join('\n');
  try {
    const result = await askClaude(prompt, {
      model: OPUS,
      json: true,
      maxTokens: 1500,
      temperature: 0.1,
    });
    if (!Array.isArray(result)) return [];
    /** @type {Array<{merged: string[], reason: string}>} */
    const groups = [];
    for (const g of result) {
      if (!g || !Array.isArray(g.merged) || g.merged.length < 2) continue;
      const keys = g.merged
        .map((i) => digests[i]?.key)
        .filter((k) => typeof k === 'string');
      if (keys.length >= 2) groups.push({ merged: keys, reason: String(g.reason || '') });
    }
    return groups;
  } catch (err) {
    console.warn('[triage] semantic merge skipped:', err.message);
    return [];
  }
}

// --- pass 3: severity recalibration -----------------------------------------

/**
 * @param {Array<{id: string, title: string, description: string, category: string, raw_severity_votes: string[], affected_routes: string[], evidence_summary: string}>} digests
 */
async function recalibrateSeverity(digests) {
  if (digests.length === 0) return new Map();
  const prompt = [
    'You are a senior QA engineer assigning final severity to deduped bug clusters.',
    'Use this rubric strictly:',
    '  critical : data loss, security breach, blocks core functionality for all users',
    '  high     : significant functional issue affecting many users',
    '  medium   : noticeable issue with workaround',
    '  low      : cosmetic or rare edge case',
    '  info     : improvement suggestion',
    '',
    'For each bug below, output a JSON object with:',
    '  id, final_severity, severity_reasoning (1-2 sentences citing the rubric),',
    '  confidence (0..1, how sure are you this is a real bug rather than noise).',
    'Do not be afraid to disagree with the raw_severity_votes — they are agent opinions.',
    '',
    'Return a JSON array with one object per bug, in the same order.',
    '',
    'Bugs:',
    JSON.stringify(digests, null, 2),
  ].join('\n');
  const result = await askClaude(prompt, {
    model: OPUS,
    json: true,
    maxTokens: 4000,
    temperature: 0.1,
  });
  /** @type {Map<string, {final_severity: string, severity_reasoning: string, confidence: number}>} */
  const out = new Map();
  if (!Array.isArray(result)) return out;
  for (const r of result) {
    if (!r || typeof r.id !== 'string') continue;
    const sev = String(r.final_severity || '').toLowerCase();
    if (!(sev in SEVERITY_RANK)) continue;
    out.set(r.id, {
      final_severity: sev,
      severity_reasoning: String(r.severity_reasoning || '').trim(),
      confidence: Math.max(0, Math.min(1, Number(r.confidence ?? 0.5))),
    });
  }
  return out;
}

// --- bug record construction -------------------------------------------------

/**
 * Build a Bug from a cluster of findings. Severity fields are placeholders that
 * `recalibrateSeverity` fills in.
 *
 * @param {import('../lib/schema.js').Finding[]} cluster
 * @param {{ mergedFrom?: string[], mergeReason?: string }} [meta]
 * @returns {import('../lib/schema.js').Bug}
 */
function bugFromCluster(cluster, meta = {}) {
  const head = cluster[0];
  const routes = Array.from(new Set(cluster.map((c) => c.url)));
  const categories = Array.from(new Set(cluster.map((c) => c.category)));
  const screenshots = Array.from(
    new Set(
      cluster.map((c) => c.evidence?.screenshot_path).filter(Boolean),
    ),
  );
  const consoleLogs = Array.from(
    new Set(
      cluster.map((c) => c.evidence?.console_message).filter(Boolean),
    ),
  ).slice(0, 6);
  const networkFailures = Array.from(
    new Set(
      cluster
        .map((c) => c.evidence?.request_url && `${c.evidence.status_code ?? 'ERR'} ${c.evidence.request_url}`)
        .filter(Boolean),
    ),
  ).slice(0, 6);
  const selectors = Array.from(
    new Set(
      cluster.map((c) => c.selector_pattern || c.evidence?.selector).filter(Boolean),
    ),
  ).slice(0, 6);

  const techDescription =
    `${cluster.length} finding(s) across ${routes.length} route(s). ` +
    (head.description || '') +
    (meta.mergeReason ? `\nMerge rationale: ${meta.mergeReason}` : '');

  const repro =
    routes.length === 1
      ? [`Navigate to ${routes[0]}`, `Observe: ${head.title}`]
      : [
          `Navigate to any of: ${routes.slice(0, 5).join(', ')}${routes.length > 5 ? '…' : ''}`,
          `Observe: ${head.title}`,
        ];

  const suggested =
    head.category === 'accessibility'
      ? 'Address the underlying axe rule by adjusting markup or ARIA attributes.'
      : head.category === 'console_error'
      ? 'Trace the console message to its source and handle or remove the offending call.'
      : head.category === 'http_status' || head.category === 'network_error'
      ? 'Investigate the failing endpoint and ensure the client either degrades gracefully or no longer requests it.'
      : head.category === 'performance'
      ? 'Profile the route; defer non-critical work and trim render-blocking resources.'
      : head.category === 'security'
      ? 'Configure the missing header or CSP directive at the server / edge layer.'
      : 'Reproduce with the steps above, then investigate the responsible component or service.';

  const draft = {
    id: randomUUID(),
    title: head.title,
    executive_description: `${head.title} (${cluster.length} occurrences, ${routes.length} route${routes.length === 1 ? '' : 's'}).`,
    technical_description: techDescription,
    repro_steps: repro,
    evidence: {
      screenshots,
      console_logs: consoleLogs,
      network_failures: networkFailures,
      selectors,
    },
    suggested_fix: suggested,
    raw_severity_votes: cluster.map((c) => c.raw_severity),
    final_severity: worstSeverity(cluster), // placeholder, replaced after Claude pass
    severity_reasoning: 'Pre-recalibration placeholder (worst raw vote).',
    confidence: 0.6,
    affected_routes: routes,
    source_finding_ids: cluster.map((c) => c.id),
    categories,
  };
  return BugSchema.parse(draft);
}

// --- public API --------------------------------------------------------------

/**
 * Run the full triage pipeline.
 *
 * @param {import('../lib/schema.js').Finding[]} findings
 * @param {object} [opts]
 * @param {boolean} [opts.useClaude]   Set false to skip Claude calls (offline mode).
 * @returns {Promise<{bugs: import('../lib/schema.js').Bug[], stats: object}>}
 */
export async function triage(findings, opts = {}) {
  const useClaude = opts.useClaude ?? Boolean(process.env.ANTHROPIC_API_KEY);
  const total = findings.length;

  // Pass 1: exact clustering.
  const exact = exactCluster(findings);

  // Pass 2: semantic merge — within each category only, to keep prompt small.
  /** @type {Map<string, Array<{key: string, sample: import('../lib/schema.js').Finding, size: number}>>} */
  const byCategory = new Map();
  for (const [k, arr] of exact) {
    const cat = arr[0].category;
    if (!byCategory.has(cat)) byCategory.set(cat, []);
    byCategory.get(cat).push({ key: k, sample: arr[0], size: arr.length });
  }
  /** @type {Map<string, string>} representative key -> rep key after merging */
  const mergedInto = new Map();
  /** @type {Map<string, string>} rep key -> merge reason */
  const mergeReasons = new Map();
  if (useClaude) {
    for (const [, digests] of byCategory) {
      if (digests.length < 2) continue;
      const groups = await semanticMerge(digests);
      for (const g of groups) {
        const [rep, ...rest] = g.merged;
        for (const k of rest) mergedInto.set(k, rep);
        if (rest.length > 0) mergeReasons.set(rep, g.reason);
      }
    }
  }

  // Build final clusters after applying mergedInto.
  const finalClusters = new Map();
  for (const [k, arr] of exact) {
    const rep = mergedInto.get(k) || k;
    if (!finalClusters.has(rep)) finalClusters.set(rep, []);
    finalClusters.get(rep).push(...arr);
  }

  // Build draft bugs.
  /** @type {import('../lib/schema.js').Bug[]} */
  const bugs = [];
  for (const [k, cluster] of finalClusters) {
    const bug = bugFromCluster(cluster, { mergeReason: mergeReasons.get(k) });
    bugs.push(bug);
  }

  // Pass 3: severity recalibration in a single batched call.
  if (useClaude && bugs.length) {
    const digests = bugs.map((b) => ({
      id: b.id,
      title: b.title,
      description: b.technical_description.slice(0, 600),
      category: b.categories[0] || 'content',
      raw_severity_votes: b.raw_severity_votes,
      affected_routes: b.affected_routes.slice(0, 8),
      evidence_summary: [
        b.evidence?.console_logs?.length
          ? `${b.evidence.console_logs.length} console message(s)`
          : '',
        b.evidence?.network_failures?.length
          ? `${b.evidence.network_failures.length} failed request(s)`
          : '',
        b.evidence?.screenshots?.length
          ? `${b.evidence.screenshots.length} screenshot(s)`
          : '',
      ]
        .filter(Boolean)
        .join(', '),
    }));
    try {
      const calibrations = await recalibrateSeverity(digests);
      for (const b of bugs) {
        const c = calibrations.get(b.id);
        if (!c) continue;
        b.final_severity = /** @type {any} */ (c.final_severity);
        b.severity_reasoning = c.severity_reasoning;
        b.confidence = c.confidence;
      }
    } catch (err) {
      console.warn('[triage] severity recalibration failed:', err.message);
    }
  }

  // Sort bugs by severity desc, then by route count.
  bugs.sort(
    (a, b) =>
      SEVERITY_RANK[b.final_severity] - SEVERITY_RANK[a.final_severity] ||
      b.affected_routes.length - a.affected_routes.length,
  );

  const stats = {
    total_findings: total,
    exact_clusters: exact.size,
    final_bugs: bugs.length,
    dedup_ratio: total === 0 ? 0 : Number((1 - bugs.length / total).toFixed(3)),
    semantic_merges: mergeReasons.size,
    claude_used: useClaude,
  };
  return { bugs, stats };
}

/**
 * CLI / module-level convenience: read findings.json, write bugs.json.
 *
 * @param {string} findingsPath
 * @param {string} outPath
 * @param {object} [opts]
 */
export async function triageFile(findingsPath, outPath, opts = {}) {
  const raw = JSON.parse(await fs.readFile(findingsPath, 'utf8'));
  if (!Array.isArray(raw)) throw new Error(`${findingsPath} is not an array`);
  const { bugs, stats } = await triage(raw, opts);
  // Validate before writing.
  for (const b of bugs) {
    const v = validateBug(b);
    if (!v.ok) throw new Error('Invalid bug record: ' + v.error.message);
  }
  await fs.mkdir(path.dirname(outPath), { recursive: true });
  await fs.writeFile(outPath, JSON.stringify(bugs, null, 2));
  return { bugs, stats };
}

export const _internal = {
  normalizeTitle,
  selectorPattern,
  clusterKey,
  worstSeverity,
  bugFromCluster,
};
