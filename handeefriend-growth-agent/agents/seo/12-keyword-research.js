import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry } from '../../lib/utils.js';

const AGENT_ID = 12;
const AGENT_NAME = 'keyword-research';

const SEED_CATEGORIES = [
  'AI home repair Canada',
  'renovation cost estimator BC',
  'house condition report',
  'AI interior design render',
  'home inspection AI tool',
  'repair estimate app',
  'forensic condition report BC',
];

const SYSTEM_PROMPT = `You are an SEO strategist for HandeeFriend (handeefriend.com), a Canadian home AI platform serving homeowners in BC and across Canada.

Given Google Search Console data and seed categories, output:
1. 10 new keyword ideas to target (mix of informational and transactional)
2. Up to 3 quick-win keywords (current rank 8–20, decent volume) drawn from GSC if any
3. Ranking-gap notes (themes where we're underperforming)

Return JSON: {
  "new_keywords": [
    { "keyword": "...", "monthly_volume": 1200, "kd_estimate": 35, "priority": "p1|p2|p3", "rationale": "..." }
  ],
  "quick_wins": [
    { "keyword": "...", "current_rank": 12, "monthly_volume": 800, "rationale": "..." }
  ],
  "gap_notes": ["..."]
}

monthly_volume and kd_estimate may be your best estimate based on category — be realistic. Lean Canadian/BC angle wherever possible.`;

async function fetchGSCData() {
  // GSC integration requires OAuth + service account JWT; in this scaffold we stub
  // it but expose the shape of data Claude expects. To wire up, decode
  // GSC_CREDENTIALS_JSON, sign a JWT, exchange for an access token, then call
  // searchanalytics.query for the configured site URL with a 28-day date range.
  if (!process.env.GSC_CREDENTIALS_JSON || !process.env.GSC_SITE_URL) {
    return { rows: [], note: 'GSC not configured — proceeding without recent rank data.' };
  }
  // Best-effort placeholder. Production: implement Google JWT flow and call
  // https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query
  return { rows: [], note: 'GSC fetch not implemented in scaffold — pass-through.' };
}

async function listExistingKeywords() {
  try {
    const res = await nocodb.list('keyword_tracker', { limit: 200 });
    return res.list || res.records || [];
  } catch {
    return [];
  }
}

async function upsertKeyword(row) {
  const existing = await nocodb.findOne('keyword_tracker', `(keyword,eq,${row.keyword})`);
  if (existing) {
    return nocodb.update('keyword_tracker', existing.id, row);
  }
  return nocodb.create('keyword_tracker', row);
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const [gsc, existing] = await Promise.all([fetchGSCData(), listExistingKeywords()]);
    logger.log(
      `GSC rows: ${gsc.rows.length} (${gsc.note || ''}); existing tracker rows: ${existing.length}`,
    );

    const userContent = `Seed categories: ${SEED_CATEGORIES.join(', ')}

GSC last-28-days snippet (top rows by impressions): ${JSON.stringify(gsc.rows.slice(0, 30))}

Existing tracked keywords (avoid duplicates): ${existing.map((k) => k.keyword).filter(Boolean).slice(0, 50).join(', ')}

Generate 10 new keyword ideas + up to 3 quick wins.`;

    const result = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent,
      maxTokens: 3000,
    });

    const allKeywords = [
      ...(result.new_keywords || []).map((k) => ({ ...k, source: 'new' })),
      ...(result.quick_wins || []).map((k) => ({
        keyword: k.keyword,
        monthly_volume: k.monthly_volume,
        current_rank: k.current_rank,
        priority: 'p1',
        rationale: k.rationale,
        source: 'quick_win',
      })),
    ];

    logger.increment('records_processed', allKeywords.length);

    if (!dryRun) {
      for (const k of allKeywords) {
        const row = {
          keyword: k.keyword,
          monthly_volume: k.monthly_volume || 0,
          kd_estimate: k.kd_estimate || null,
          current_rank: k.current_rank || null,
          priority: k.priority || 'p2',
        };
        try {
          await upsertKeyword(row);
          logger.increment('records_created');
        } catch (e) {
          logger.error(`Upsert failed for "${k.keyword}": ${e.message}`);
        }
      }
    } else {
      logger.log(`[DRY-RUN] would upsert ${allKeywords.length} keywords`);
      console.log(JSON.stringify(result, null, 2));
    }

    await logger.finish('success');
    return result;
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '12-keyword-research.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
