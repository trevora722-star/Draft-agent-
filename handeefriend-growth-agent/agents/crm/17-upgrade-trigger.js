import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { createPromoCode } from '../../lib/stripe.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, randomCode, daysFromNowISO } from '../../lib/utils.js';

const AGENT_ID = 17;
const AGENT_NAME = 'upgrade-trigger';

const SYSTEM_PROMPT = `You are Faydra Aldridge writing a hyper-personalized upgrade email to a HandeeFriend power user. Reference their actual usage stats — number of reports, renders, plan tier. Specific. Warm. The promo code is real (20% off, expires in 72 hours, single use).

Rules:
- Subject under 45 chars
- Open with a specific observation about their usage
- One CTA button to upgrade with the promo code visible
- Sender footer + unsubscribe block

Return JSON: { "subject": "...", "preview_text": "...", "html_body": "..." }

Also return a short banner string (max 120 chars) suitable for in-app display in field "banner_html".`;

async function fetchUser(userId) {
  return nocodb.get('users', userId);
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const userId =
    options.user_id ||
    process.argv.find((a) => a.startsWith('--user='))?.split('=')[1];
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    if (!userId) throw new Error('user_id required (--user=ID or options.user_id)');
    const user = await fetchUser(userId);
    if (!user) throw new Error(`No user with id=${userId}`);
    logger.log(`Generating upgrade trigger for ${user.email}`);
    logger.increment('records_processed');

    const code = `HF-${user.id}-${randomCode('', 4)}`;

    if (!dryRun && process.env.STRIPE_SECRET_KEY) {
      try {
        await createPromoCode({
          code,
          customerEmail: user.email,
          percentOff: 20,
          expiresInHours: 72,
          maxRedemptions: 1,
        });
        logger.log(`Stripe promo created: ${code}`);
      } catch (e) {
        logger.error(`Stripe promo creation failed: ${e.message}`);
      }
    }

    const userContent = `Recipient: ${user.name || user.email} (${user.email})
Plan tier: ${user.plan_tier}
Reports generated: ${user.reports_generated || 0}
Renders saved: ${user.renders_saved || 0}
Lead score: ${user.lead_score || 0}
Days active: since ${user.signup_date}

Promo code: ${code} (20% off first paid month, expires 72 hours)
Upgrade URL: ${process.env.HANDEEFRIEND_BASE_URL || 'https://handeefriend.com'}/upgrade?code=${encodeURIComponent(code)}

Write the email + banner.`;

    const result = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent,
      maxTokens: 2000,
    });

    if (!dryRun) {
      await nocodb.update('users', user.id, {
        active_promo_code: code,
        promo_expires: daysFromNowISO(3),
        upgrade_banner_html: result.banner_html || '',
      });
      logger.increment('records_created');
      await sendEmail({
        to: user.email,
        subject: result.subject,
        html: result.html_body,
      });
      logger.increment('emails_sent');
    } else {
      console.log('[DRY-RUN] subject:', result.subject);
      console.log('[DRY-RUN] code:', code);
    }

    await logger.finish('success');
    return { code };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '17-upgrade-trigger.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
