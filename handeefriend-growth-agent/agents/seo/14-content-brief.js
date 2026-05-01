import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, todayISO, weekNumber } from '../../lib/utils.js';

const AGENT_ID = 14;
const AGENT_NAME = 'content-brief';

const SYSTEM_PROMPT = `You are an SEO content strategist building a brief for a 900-word blog post on handeefriend.com.

Output JSON:
{
  "keyword": "...",
  "search_intent": "informational|transactional|navigational",
  "recommended_title": "...",
  "word_count_target": 900,
  "h2_outline": ["H2 1", "H2 2", "..."],
  "competitor_urls": ["url1", "url2", "url3"],
  "required_entities": ["entity1", "..."],
  "internal_links": [{"anchor_text": "...", "url": "..."}],
  "schema_type": "Article|HowTo|FAQ",
  "meta_description_suggestion": "..."
}

The site has these key URLs available for internal linking:
- /design-studio
- /forensic-report
- /repair-estimate
- /pricing
- /blog

Lean Canadian/BC angles. Internal-link 2–3 of those URLs naturally.`;

async function fetchTopRanking(keyword) {
  if (!process.env.SEARCH_API_KEY || !process.env.SEARCH_ENGINE_ID) {
    return { items: [], note: 'No SEARCH_API_KEY configured — skipping SERP fetch.' };
  }
  const url = `https://www.googleapis.com/customsearch/v1?key=${encodeURIComponent(process.env.SEARCH_API_KEY)}&cx=${encodeURIComponent(process.env.SEARCH_ENGINE_ID)}&q=${encodeURIComponent(keyword)}&num=3`;
  try {
    const res = await fetch(url);
    if (!res.ok) return { items: [], note: `SERP fetch ${res.status}` };
    const data = await res.json();
    return {
      items: (data.items || []).map((i) => ({
        title: i.title,
        link: i.link,
        snippet: i.snippet,
      })),
    };
  } catch (e) {
    return { items: [], note: `SERP error: ${e.message}` };
  }
}

async function pickTopP1Keyword() {
  const res = await nocodb.list('keyword_tracker', {
    where: '(priority,eq,p1)',
    limit: 5,
  });
  const rows = res.list || res.records || [];
  if (!rows.length) {
    const fallback = await nocodb.list('keyword_tracker', { limit: 1, sort: '-monthly_volume' });
    return (fallback.list || fallback.records || [])[0] || null;
  }
  return rows[0];
}

async function triggerAgent02({ briefId }) {
  const port = process.env.PORT || 3001;
  const secret = process.env.RUNNER_SECRET;
  if (!secret) return { triggered: false, reason: 'RUNNER_SECRET not set' };
  try {
    const res = await fetch(`http://localhost:${port}/run/02`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${secret}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ content_calendar_id: briefId }),
    });
    return { triggered: res.ok, status: res.status };
  } catch (e) {
    return { triggered: false, reason: e.message };
  }
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const target = options.keyword
      ? { keyword: options.keyword }
      : await pickTopP1Keyword();
    if (!target) throw new Error('No keyword candidates found in keyword_tracker');
    logger.log(`Building brief for keyword: "${target.keyword}"`);

    const serp = await fetchTopRanking(target.keyword);
    logger.log(`SERP results: ${serp.items?.length || 0}`);

    const userContent = `Target keyword: ${target.keyword}
Monthly volume: ${target.monthly_volume || 'unknown'}
KD estimate: ${target.kd_estimate || 'unknown'}
Current rank: ${target.current_rank || 'not ranking'}

Top 3 ranking competitors:
${(serp.items || []).map((i, idx) => `${idx + 1}. ${i.title}\n   ${i.link}\n   ${i.snippet}`).join('\n\n') || 'No SERP data available.'}

Build the brief.`;

    const brief = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent,
      maxTokens: 2500,
    });

    logger.increment('records_processed');

    if (dryRun) {
      console.log('[DRY-RUN] Brief:', JSON.stringify(brief, null, 2));
      await logger.finish('success');
      return brief;
    }

    const calendarRow = {
      week_number: weekNumber(),
      publish_date: todayISO(),
      content_type: 'blog',
      keyword: brief.keyword,
      script_status: 'briefed',
      platform: 'blog',
      brief_json: JSON.stringify(brief),
      agent_id: AGENT_ID,
    };
    const created = await nocodb.create('content_calendar', calendarRow);
    logger.increment('records_created');
    logger.log(`Brief saved to content_calendar (id=${created.id || 'unknown'})`);

    if (target.id) {
      await nocodb.update('keyword_tracker', target.id, {
        content_calendar_id: created.id,
      });
    }

    const trig = await triggerAgent02({ briefId: created.id });
    logger.log(`Agent 02 trigger: ${JSON.stringify(trig)}`);

    await logger.finish('success');
    return { brief, calendar_id: created.id };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '14-content-brief.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
