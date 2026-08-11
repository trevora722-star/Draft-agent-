import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, todayISO, weekNumber, slugify, nowISO } from '../../lib/utils.js';

const AGENT_ID = 6;
const AGENT_NAME = 'case-study';

const SYSTEM_PROMPT = `You are writing a case study draft + permission outreach for a real HandeeFriend power user.

The case study must NOT be published or sent to the user without human approval. You produce drafts only.

Output JSON:
{
  "narrative_html": "<article>... 600 words, real BC voice, specific numbers, anonymizable...</article>",
  "slide_summary": ["3 short slide-style bullets"],
  "permission_email": { "subject": "...", "html": "..." }
}

The permission email is from Faydra to the user, asking permission to feature them. It must be warm, low-pressure, and offer to keep them anonymous.`;

async function findPowerUsers() {
  const res = await nocodb.list('users', {
    where: '(lead_score,ge,70)~and(reports_generated,ge,3)',
    sort: '-lead_score',
    limit: 3,
  });
  return res.list || res.records || [];
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const users = await findPowerUsers();
    logger.log(`Found ${users.length} candidate power users for case studies`);

    for (const u of users) {
      logger.increment('records_processed');

      const userContent = `User profile:
- Name: ${u.name || 'Anonymous'}
- Plan: ${u.plan_tier}
- Reports generated: ${u.reports_generated}
- Renders saved: ${u.renders_saved}
- Lead score: ${u.lead_score}
- Active since: ${u.signup_date}

Generate a case study and a permission email.`;

      try {
        const result = await callClaudeJSON({
          systemPrompt: SYSTEM_PROMPT,
          userContent,
          maxTokens: 4000,
        });

        if (!dryRun) {
          await nocodb.create('content_calendar', {
            content_type: 'blog',
            publish_date: todayISO(),
            week_number: weekNumber(),
            script_status: 'pending_permission',
            keyword: `case-study-${slugify(u.name || u.email || 'user')}`,
            script_content: result.narrative_html,
            brief_json: JSON.stringify({
              slide_summary: result.slide_summary,
              user_id: u.id,
              user_email: u.email,
            }),
            agent_id: AGENT_ID,
            platform: 'blog',
          });
          logger.increment('records_created');

          await nocodb.create('email_templates', {
            sequence_name: 'case_study_permission',
            email_number: 1,
            subject: result.permission_email.subject,
            html_body: result.permission_email.html,
            last_updated: nowISO(),
            agent_id: AGENT_ID,
          });
          logger.increment('records_created');
        } else {
          console.log('[DRY-RUN] case study for', u.email, '— title preview:', (result.narrative_html || '').slice(0, 200));
        }
      } catch (e) {
        logger.error(`Case study generation failed for ${u.email}: ${e.message}`);
      }
    }

    logger.log('NOTE: Drafts queued for review. No emails sent automatically.');
    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '06-case-study.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
