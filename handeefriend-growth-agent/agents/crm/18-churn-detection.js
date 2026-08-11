import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { listSubscriptions } from '../../lib/stripe.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, daysAgoISO } from '../../lib/utils.js';

const AGENT_ID = 18;
const AGENT_NAME = 'churn-detection';

const SAVE_PROMPT = `Write a save email from Faydra Aldridge at HandeeFriend. The user hasn't used their account in a while.

Tone: Genuinely curious, no guilt-tripping. "Life gets busy" energy. Offer something — either a reminder of what they're missing or a plan pause option.

The pause option (for D7 email): "We can pause your subscription for 30 days — no charge, no cancellation. You keep all your data. Just reply with 'pause' and I'll take care of it."

Keep it under 120 words. Subject line should not mention "cancel" or "leaving."

Return JSON: { "subject": "...", "html": "..." }`;

async function getCancelAtPeriodEndIds() {
  if (!process.env.STRIPE_SECRET_KEY) return new Set();
  try {
    const subs = await listSubscriptions({ status: 'active' });
    return new Set(
      subs.filter((s) => s.cancel_at_period_end).map((s) => s.customer),
    );
  } catch (e) {
    console.warn(`Stripe sub fetch failed: ${e.message}`);
    return new Set();
  }
}

async function generateSave(user, step) {
  const userContent = `Recipient: ${user.name || user.email}
Plan: ${user.plan_tier}
Last active: ${user.last_active}
Reports generated: ${user.reports_generated}
Save step: ${step} (D0=immediate awareness; D3=value reminder; D7=pause offer)`;
  return callClaudeJSON({ systemPrompt: SAVE_PROMPT, userContent, maxTokens: 1200 });
}

function annualPriceCAD(plan) {
  // Approximate annualized price thresholds for high-value alerts
  return { starter: 120, pro: 240, annual: 240 }[plan] || 0;
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const cancelSet = await getCancelAtPeriodEndIds();
    const cutoff = daysAgoISO(14);
    const where = `(plan_tier,neq,free)~and((last_active,le,${cutoff})~or(stripe_customer_id,in,${[...cancelSet].join(',') || '__none__'}))`;
    const users = await nocodb.listAll('users', { where });
    logger.log(`At-risk paid users: ${users.length}`);

    for (const u of users) {
      logger.increment('records_processed');
      const lastActiveDays = u.last_active
        ? Math.floor((Date.now() - new Date(u.last_active).getTime()) / 86400000)
        : 999;

      let step;
      if (lastActiveDays >= 7 && lastActiveDays < 14) step = 'D3';
      else if (lastActiveDays >= 14) step = 'D7';
      else step = 'D0';

      try {
        const email = await generateSave(u, step);
        if (!dryRun) {
          await sendEmail({ to: u.email, subject: email.subject, html: email.html });
          logger.increment('emails_sent');
        } else {
          logger.log(`  [DRY-RUN] ${u.email} step=${step} subject="${email.subject}"`);
        }

        if (annualPriceCAD(u.plan_tier) >= 200 && process.env.TREVOR_EMAIL) {
          if (!dryRun) {
            await sendEmail({
              to: process.env.TREVOR_EMAIL,
              subject: `High-value at-risk: ${u.email}`,
              html: `<p>${u.email} (${u.plan_tier}) is at risk. Last active ${lastActiveDays} days ago. Save step: ${step}.</p>`,
            });
            logger.increment('emails_sent');
          }
        }
      } catch (e) {
        logger.error(`Save email failed for ${u.email}: ${e.message}`);
      }
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '18-churn-detection.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
