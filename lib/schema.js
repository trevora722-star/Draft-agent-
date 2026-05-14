// Shared Finding schema for the QAgent pipeline.
//
// A Finding is one raw observation: one console error, one a11y violation,
// one slow LCP, one missing security header. Findings are emitted by Session A
// (crawler) and Session B (battery). Session C consumes them as input.
//
// After triage they collapse into Bug records (see intelligence/triage.js).

import { z } from 'zod';
import { randomUUID } from 'node:crypto';

/** @typedef {z.infer<typeof FindingSchema>} Finding */

export const SOURCE_AGENTS = /** @type {const} */ ([
  'crawler',
  'battery',
  'intelligence',
]);

export const CATEGORIES = /** @type {const} */ ([
  'console_error',
  'network_error',
  'http_status',
  'accessibility',
  'performance',
  'security',
  'visual',
  'content',
  'form_validation',
  'navigation',
  'auth',
  'seo',
]);

export const RAW_SEVERITIES = /** @type {const} */ ([
  'critical',
  'high',
  'medium',
  'low',
  'info',
]);

const EvidenceSchema = z
  .object({
    screenshot_path: z.string().nullish(),
    selector: z.string().nullish(),
    console_message: z.string().nullish(),
    console_level: z.string().nullish(),
    request_url: z.string().nullish(),
    status_code: z.number().int().nullish(),
    method: z.string().nullish(),
    snippet: z.string().nullish(),
    stack: z.string().nullish(),
    axe_rule: z.string().nullish(),
    metric: z.string().nullish(),
    metric_value: z.number().nullish(),
  })
  .partial()
  .passthrough();

export const FindingSchema = z.object({
  id: z.string().min(1),
  source_agent: z.enum(SOURCE_AGENTS),
  category: z.enum(CATEGORIES),
  raw_severity: z.enum(RAW_SEVERITIES),
  url: z.string().min(1),
  title: z.string().min(1),
  description: z.string().min(1),
  evidence: EvidenceSchema.default({}),
  detected_at: z.string().datetime({ offset: true }),
  selector_pattern: z.string().nullish(),
  tags: z.array(z.string()).default([]),
  metadata: z.record(z.string(), z.unknown()).default({}),
});

/**
 * Validate an arbitrary object against the Finding schema.
 *
 * @param {unknown} obj
 * @returns {{ ok: true, value: Finding } | { ok: false, error: import('zod').ZodError }}
 */
export function validateFinding(obj) {
  const parsed = FindingSchema.safeParse(obj);
  if (parsed.success) return { ok: true, value: parsed.data };
  return { ok: false, error: parsed.error };
}

/**
 * Validate and throw on failure. Useful in pipelines where a bad Finding is
 * a programmer error, not user data.
 *
 * @param {unknown} obj
 * @returns {Finding}
 */
export function assertFinding(obj) {
  return FindingSchema.parse(obj);
}

/**
 * Build a Finding with sensible defaults. `id` and `detected_at` are filled in
 * if omitted. Throws if the resulting object fails validation.
 *
 * @param {Partial<Finding> & Pick<Finding, 'source_agent' | 'category' | 'raw_severity' | 'url' | 'title' | 'description'>} input
 * @returns {Finding}
 */
export function makeFinding(input) {
  const draft = {
    id: input.id ?? randomUUID(),
    detected_at: input.detected_at ?? new Date().toISOString(),
    evidence: input.evidence ?? {},
    tags: input.tags ?? [],
    metadata: input.metadata ?? {},
    selector_pattern: input.selector_pattern ?? null,
    ...input,
  };
  return FindingSchema.parse(draft);
}

// --- Bug schema -------------------------------------------------------------
// Output of intelligence/triage.js. A Bug is the deduped, severity-calibrated
// representation that ends up in the report.

export const FINAL_SEVERITIES = RAW_SEVERITIES;

export const BugSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  executive_description: z.string().min(1),
  technical_description: z.string().min(1),
  repro_steps: z.array(z.string()).default([]),
  evidence: z
    .object({
      screenshots: z.array(z.string()).default([]),
      console_logs: z.array(z.string()).default([]),
      network_failures: z.array(z.string()).default([]),
      selectors: z.array(z.string()).default([]),
    })
    .partial()
    .default({}),
  suggested_fix: z.string().min(1),
  raw_severity_votes: z.array(z.enum(RAW_SEVERITIES)).default([]),
  final_severity: z.enum(FINAL_SEVERITIES),
  severity_reasoning: z.string().min(1),
  confidence: z.number().min(0).max(1),
  affected_routes: z.array(z.string()).default([]),
  source_finding_ids: z.array(z.string()).default([]),
  categories: z.array(z.enum(CATEGORIES)).default([]),
});

/** @typedef {z.infer<typeof BugSchema>} Bug */

/**
 * Validate a Bug record.
 *
 * @param {unknown} obj
 * @returns {{ ok: true, value: Bug } | { ok: false, error: import('zod').ZodError }}
 */
export function validateBug(obj) {
  const parsed = BugSchema.safeParse(obj);
  if (parsed.success) return { ok: true, value: parsed.data };
  return { ok: false, error: parsed.error };
}
