import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { applyAccountCredit } from '../../lib/stripe.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, nowISO, daysAgoISO, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 23;
const AGENT_NAME = 'referral-program';

const REWARD_THRESHOLD = 3;
const REWARD_AMOUNT_CAD = 19.99; // approx 1 month free credit

async function logReferralEvent(event) {
  return nocodb.create('referral_events', event);
}

async function findReferrer(refId) {
  // refId is encoded into referral_link, e.g. ?ref=USER123
  return nocodb.findOne('users', `(referral_link,like,%ref=${refId}%)`);
}

async function notifyReferrer(referrer, referredEmail) {
  if (!referrer?.email) return;
  const userContent = `Referrer: ${referrer.name || referrer.email}
Their referral count: ${(referrer.referral_count || 0) + 1}
Their goal threshold: ${REWARD_THRESHOLD} for free month credit
Referred email (mask the local-part): ${referredEmail}

Write a short celebratory note. JSON: { "subject": "...", "html": "..." }`;
  return callClaudeJSON({
    systemPrompt:
      'You are Faydra writing a quick happy note about a successful referral. Under 80 words. Inline-styled HTML.',
    userContent,
    maxTokens: 800,
  });
}

export async function handleReferralEvent({ refId, referredEmail, eventType = 'signup', utm = '', dryRun = false }) {
  const referrer = await findReferrer(refId);
  const event = {
    referrer_user_id: referrer?.id || null,
    referred_email: referredEmail,
    event_type: eventType,
    event_date: nowISO(),
    utm_campaign: utm,
    reward_applied: false,
  };

  if (!dryRun) await logReferralEvent(event);

  if (!referrer) return { event, reward: false };

  const newCount = (referrer.referral_count || 0) + (eventType === 'signup' ? 1 : 0);

  if (!dryRun) {
    await nocodb.update('users', referrer.id, { referral_count: newCount });
    const note = await notifyReferrer(referrer, referredEmail);
    if (note?.subject && referrer.email) {
      await sendEmail({ to: referrer.email, subject: note.subject, html: note.html });
    }
  }

  let rewardApplied = false;
  if (newCount >= REWARD_THRESHOLD && !referrer.reward_applied && referrer.stripe_customer_id) {
    if (!dryRun) {
      try {
        await applyAccountCredit({
          customerId: referrer.stripe_customer_id,
          amountCad: REWARD_AMOUNT_CAD,
          description: `Referral milestone reward for ${referrer.email}`,
        });
        rewardApplied = true;
      } catch (e) {
        console.warn(`Stripe credit failed: ${e.message}`);
      }
    } else {
      rewardApplied = true;
    }
  }

  return { event, reward: rewardApplied, referrer_id: referrer.id, new_count: newCount };
}

async function monthlyLeaderboard({ logger, dryRun }) {
  const since = daysAgoISO(30);
  const events = await nocodb.listAll('referral_events', {
    where: `(event_date,ge,${since})~and(event_type,eq,signup)`,
  });
  const byUser = events.reduce((acc, e) => {
    if (!e.referrer_user_id) return acc;
    acc[e.referrer_user_id] = (acc[e.referrer_user_id] || 0) + 1;
    return acc;
  }, {});
  const top = Object.entries(byUser)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  const enriched = [];
  for (const [uid, count] of top) {
    const u = await nocodb.get('users', uid).catch(() => null);
    enriched.push({ id: uid, name: u?.name || u?.email || `user-${uid}`, count });
  }

  const html = `<div style="font-family:system-ui,sans-serif">
    <h2>Referral Leaderboard — last 30 days</h2>
    <ol>${enriched.map((e) => `<li><strong>${escapeHtml(e.name)}</strong> — ${e.count} referrals</li>`).join('')}</ol>
  </div>`;

  if (!dryRun) {
    const recipients = [process.env.TREVOR_EMAIL, process.env.FAYDRA_EMAIL].filter(Boolean);
    if (recipients.length) {
      await sendEmail({
        to: recipients,
        subject: 'Top referrers — last 30 days',
        html,
      });
      logger.increment('emails_sent');
    }
  }
  return enriched;
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    if (options.refId && options.referredEmail) {
      logger.increment('records_processed');
      const result = await handleReferralEvent({ ...options, dryRun });
      await logger.finish('success');
      return result;
    }

    // Default: run monthly leaderboard
    const leaderboard = await monthlyLeaderboard({ logger, dryRun });
    logger.increment('records_processed', leaderboard.length);
    await logger.finish('success');
    return { leaderboard };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '23-referral-program.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
