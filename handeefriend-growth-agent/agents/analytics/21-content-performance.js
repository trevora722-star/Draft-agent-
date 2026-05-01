import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, daysAgoISO, todayISO, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 21;
const AGENT_NAME = 'content-performance';

const SYSTEM_PROMPT = `You are an analyst studying which HandeeFriend content is converting trial starts. Given the top performers, output a short JSON insight:
{
  "tiktok_pattern": "What's working in our top hooks",
  "blog_pattern": "What's working in our top posts",
  "next_week_recommendation": "What to lean into",
  "what_to_avoid": "What pattern is consistently underperforming"
}
Be concrete and reference numbers in the data.`;

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const since = daysAgoISO(14);
    const recent = await nocodb.listAll('content_calendar', {
      where: `(publish_date,ge,${since})`,
    });
    const users = await nocodb.listAll('users');

    // Attribution: count users whose referral_source matches a UTM tag
    const sourceToCount = users.reduce((acc, u) => {
      const s = u.referral_source;
      if (!s) return acc;
      acc[s] = (acc[s] || 0) + 1;
      return acc;
    }, {});

    const enriched = recent.map((c) => ({
      ...c,
      trial_starts_attributed: c.utm_tag ? sourceToCount[c.utm_tag] || 0 : c.trial_starts_attributed || 0,
    }));

    const blogs = enriched
      .filter((c) => c.content_type === 'blog')
      .sort((a, b) => (b.trial_starts_attributed || 0) - (a.trial_starts_attributed || 0))
      .slice(0, 5);
    const tiktoks = enriched
      .filter((c) => c.content_type === 'tiktok')
      .sort((a, b) => (b.performance_score || 0) - (a.performance_score || 0))
      .slice(0, 3);

    logger.increment('records_processed', enriched.length);

    const userContent = `Top blog posts (last 14d):
${blogs.map((b) => `- ${b.keyword || ''} | trials: ${b.trial_starts_attributed}`).join('\n') || 'none yet'}

Top TikTok hooks (last 14d):
${tiktoks
  .map((t) => {
    try {
      const s = JSON.parse(t.script_content || '{}');
      return `- pillar=${t.pillar} score=${t.performance_score} hook="${s.hook || ''}"`;
    } catch {
      return `- pillar=${t.pillar} score=${t.performance_score}`;
    }
  })
  .join('\n') || 'none yet'}

Total trial starts this period: ${blogs.reduce((s, b) => s + (b.trial_starts_attributed || 0), 0)}`;

    const insight = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent,
      maxTokens: 1500,
    });

    if (!dryRun) {
      await nocodb.create('content_calendar', {
        content_type: 'insights',
        publish_date: todayISO(),
        script_status: 'draft',
        script_content: JSON.stringify(insight),
        agent_id: AGENT_ID,
      });
      logger.increment('records_created');

      const recipients = [process.env.TREVOR_EMAIL, process.env.FAYDRA_EMAIL].filter(Boolean);
      if (recipients.length) {
        await sendEmail({
          to: recipients,
          subject: `Content perf insight — ${todayISO()}`,
          html: `<div style="font-family:system-ui,sans-serif"><h2>Content Performance — last 14 days</h2><pre>${escapeHtml(JSON.stringify(insight, null, 2))}</pre></div>`,
        });
        logger.increment('emails_sent');
      }
    } else {
      console.log('[DRY-RUN]', JSON.stringify(insight, null, 2));
    }

    await logger.finish('success');
    return insight;
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '21-content-performance.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
