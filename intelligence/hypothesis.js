// intelligence/hypothesis.js
//
// The PhD pass. One high-effort Claude Opus 4.7 call. Input: bugs.json +
// statistics.json + (digested) findings.json. Output: a markdown document
// with named sections that the report renders verbatim.
//
// The prompt is intentionally pointed: we ask for falsifiable hypotheses and
// explicit confidence statements. Generic advice is not useful here.

import fs from 'node:fs/promises';
import path from 'node:path';

import { askClaude } from '../lib/claude.js';

const OPUS = process.env.QAGENT_MODEL_OPUS || 'claude-opus-4-7';

const SYSTEM = `You are a senior software quality researcher with a PhD in computer science.
You are reviewing the results of an automated test run against a web application.
The data below is comprehensive but raw. Your job is to find the signal:
the underlying causes, the architectural smells, the risk patterns, and the
order in which a competent engineering team should address what you found.
Be intellectually honest. Note where evidence is thin. Generate hypotheses
that are *falsifiable* — for each, state what observation would confirm or
refute it. Avoid generic advice. If the data does not support a strong claim,
say so explicitly.`;

const REQUIRED_SECTIONS = [
  '## Root Cause Hypotheses',
  '## Architectural Inferences',
  '## Risk Surface Analysis',
  '## Prioritization Recommendation',
  '## Confidence Statement',
];

/**
 * Trim a findings array to a digest small enough to fit in context while
 * still being representative. We keep the first N per category, plus a
 * summary count for the rest.
 *
 * @param {any[]} findings
 * @param {number} perCategory
 */
function digestFindings(findings, perCategory = 5) {
  /** @type {Map<string, any[]>} */
  const byCat = new Map();
  for (const f of findings) {
    if (!byCat.has(f.category)) byCat.set(f.category, []);
    byCat.get(f.category).push(f);
  }
  const sample = [];
  /** @type {Record<string, number>} */
  const omitted = {};
  for (const [cat, arr] of byCat) {
    sample.push(
      ...arr.slice(0, perCategory).map((f) => ({
        category: f.category,
        url: f.url,
        title: f.title,
        raw_severity: f.raw_severity,
        description: (f.description || '').slice(0, 240),
      })),
    );
    if (arr.length > perCategory) omitted[cat] = arr.length - perCategory;
  }
  return { sample, omitted_counts: omitted, total: findings.length };
}

/**
 * Build the prompt body. Kept as a function (not a template literal in-line)
 * so it can be unit-tested independently.
 *
 * @param {object} input
 * @param {string} input.target
 * @param {any[]} input.bugs
 * @param {object} input.statistics
 * @param {object} input.findingsDigest
 */
function buildPrompt({ target, bugs, statistics, findingsDigest }) {
  return [
    `Target system: ${target}`,
    '',
    'Below is a deduplicated bug list (after triage), aggregate statistics,',
    'and a representative digest of raw findings. The bug list has already',
    'had severity recalibrated; treat final_severity as authoritative.',
    '',
    '### bugs.json (deduplicated)',
    '```json',
    JSON.stringify(bugs, null, 2),
    '```',
    '',
    '### statistics.json',
    '```json',
    JSON.stringify(statistics, null, 2),
    '```',
    '',
    '### findings.json (digest)',
    '```json',
    JSON.stringify(findingsDigest, null, 2),
    '```',
    '',
    'Produce a markdown report with EXACTLY these named H2 sections, in this order:',
    '',
    '## Root Cause Hypotheses',
    '  For each candidate root cause, name it, list the bug ids that support it,',
    '  and give the falsification test ("what observation would refute this?").',
    '',
    '## Architectural Inferences',
    '  What does the bug distribution suggest about the codebase, team',
    '  ownership, or deployment pipeline? Cite specific evidence (numbers, routes).',
    '',
    '## Risk Surface Analysis',
    '  Identify combinations of bugs that are more severe together than apart',
    '  (e.g., missing CSP + reflected input). Make the chain explicit.',
    '',
    '## Prioritization Recommendation',
    '  Ordered list of fixes. For each, justify the position — dependencies,',
    '  unblocking value, blast radius. Do not simply restate severity.',
    '',
    '## Confidence Statement',
    '  Where is the evidence strong? Where is it weak? What three follow-up',
    '  tests would most efficiently confirm or refute your top hypotheses?',
    '',
    'Style: precise, terse, intellectually honest. No hedging filler. No boilerplate.',
    'If a section has no real signal in the data, say so in one sentence and move on.',
  ].join('\n');
}

/**
 * Generate the analysis markdown.
 *
 * @param {object} input
 * @param {string} input.target
 * @param {any[]} input.bugs
 * @param {object} input.statistics
 * @param {any[]} input.findings
 * @returns {Promise<string>} Markdown.
 */
export async function analyze({ target, bugs, statistics, findings }) {
  const digest = digestFindings(findings);
  const prompt = buildPrompt({ target, bugs, statistics, findingsDigest: digest });
  const md = await askClaude(prompt, {
    model: OPUS,
    system: SYSTEM,
    maxTokens: 6000,
    temperature: 0.3,
  });
  return ensureSections(String(md).trim());
}

/**
 * Generate a 3-paragraph executive summary suitable for the report header.
 *
 * @param {object} input
 * @param {string} input.target
 * @param {any[]} input.bugs
 * @param {string} input.analysisMarkdown
 * @returns {Promise<string>} HTML-safe paragraphs joined by `\n\n`.
 */
export async function executiveSummary({ target, bugs, analysisMarkdown }) {
  const counts = bugs.reduce(
    (acc, b) => {
      acc[b.final_severity] = (acc[b.final_severity] || 0) + 1;
      return acc;
    },
    /** @type {Record<string, number>} */ ({}),
  );
  const prompt = [
    `Target: ${target}`,
    `Severity counts: ${JSON.stringify(counts)}`,
    `Total bugs: ${bugs.length}`,
    '',
    'Below is the full PhD-style analysis. Distil it into a 3-paragraph executive',
    'summary for an engineering manager. Paragraph 1: state of the system in one breath',
    '(no padding). Paragraph 2: the most important pattern or root cause. Paragraph 3:',
    'recommended next action and the single most important risk to address first.',
    'No headings. No bullets. Plain prose. ~120 words total.',
    '',
    '--- Analysis ---',
    analysisMarkdown,
  ].join('\n');
  const text = await askClaude(prompt, {
    model: OPUS,
    maxTokens: 800,
    temperature: 0.3,
  });
  return String(text).trim();
}

/**
 * If Claude omits a required section, append a stub so downstream rendering
 * never breaks. This is a safety net, not a feature — log when triggered.
 *
 * @param {string} md
 */
function ensureSections(md) {
  let out = md;
  for (const h of REQUIRED_SECTIONS) {
    if (!out.includes(h)) {
      console.warn(`[hypothesis] missing section "${h}" — inserting stub.`);
      out += `\n\n${h}\n\n_No analysis returned for this section._`;
    }
  }
  return out;
}

/**
 * File-level convenience.
 *
 * @param {object} paths
 * @param {string} paths.target
 * @param {string} paths.bugsPath
 * @param {string} paths.statisticsPath
 * @param {string} paths.findingsPath
 * @param {string} paths.outPath
 */
export async function analyzeFile({
  target,
  bugsPath,
  statisticsPath,
  findingsPath,
  outPath,
}) {
  const bugs = JSON.parse(await fs.readFile(bugsPath, 'utf8'));
  const statistics = JSON.parse(await fs.readFile(statisticsPath, 'utf8'));
  const findings = JSON.parse(await fs.readFile(findingsPath, 'utf8'));
  const md = await analyze({ target, bugs, statistics, findings });
  await fs.mkdir(path.dirname(outPath), { recursive: true });
  await fs.writeFile(outPath, md);
  return md;
}

export const _internal = { buildPrompt, digestFindings, ensureSections };
