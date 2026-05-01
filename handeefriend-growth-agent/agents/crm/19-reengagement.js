import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, daysAgoISO } from '../../lib/utils.js';

const AGENT_ID = 19;
const AGENT_NAME = 'reengagement';

const PROMPT = `You are Faydra writing a re-engagement email to a specific user segment of HandeeFriend.

Segments:
- signup_ghost: signed up but never used. Write prescriptive, ONE clear next action.
- one_and_done: ran exactly one report. Reference what they did, suggest the logical next tool.
- cancelled_60: cancelled 60+ days ago. Win-back. Mention something genuinely new.

Return JSON: { "subject": "...", "html": "..." }
Tone: warm, no pressure, no guilt.`;

async function fetchSegment(name) {
  const where = {
    signup_ghost: `(plan_tier,eq,free)~and(reports_generated,eq,0)~and(signup_date,le,${daysAgoISO(7)})`,
    one_and_done: `(reports_generated,eq,1)~and(last_active,le,${daysAgoISO(14)})`,
    cancelled_60: `(plan_tier,eq,free)~and(cancelled_date,le,${daysAgoISO(60)})`,
  }[name];
  return nocodb.listAll('users', { where });
}

async function emailFor(user, segment) {
  const userContent = `Recipient: ${user.name || user.email}
Segment: ${segment}
Reports: ${user.reports_generated || 0}
Renders: ${user.renders_saved || 0}
Plan: ${user.plan_tier}
Signup: ${user.signup_date}
Last active: ${user.last_active}`;
  return callClaudeJSON({ systemPrompt: PROMPT, userContent, maxTokens: 1500 });
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    for (const segment of ['signup_ghost', 'one_and_done', 'cancelled_60']) {
      const users = await fetchSegment(segment);
      logger.log(`segment=${segment} → ${users.length} users`);

      for (const u of users) {
        logger.increment('records_processed');
        try {
          const email = await emailFor(u, segment);
          if (!dryRun) {
            await sendEmail({ to: u.email, subject: email.subject, html: email.html });
            logger.increment('emails_sent');
          } else {
            logger.log(`  [DRY-RUN] ${segment} → ${u.email} subject="${email.subject}"`);
          }
        } catch (e) {
          logger.error(`re-engage failed for ${u.email}: ${e.message}`);
        }
      }
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '19-reengagement.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
