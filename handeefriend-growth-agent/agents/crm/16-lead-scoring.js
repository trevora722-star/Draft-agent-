import 'dotenv/config';
import { nocodb } from '../../lib/nocodb.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry } from '../../lib/utils.js';

const AGENT_ID = 16;
const AGENT_NAME = 'lead-scoring';

function daysSince(dateStr) {
  if (!dateStr) return Infinity;
  const ms = Date.now() - new Date(dateStr).getTime();
  return ms / 86400000;
}

function activityScore(days) {
  if (days < 7) return 10;
  if (days <= 30) return 7;
  if (days <= 60) return 4;
  return 1;
}

function planScore(tier) {
  return { free: 0, starter: 5, pro: 10, annual: 10 }[tier] || 0;
}

export function scoreUser(u) {
  const reports = Math.min(30, (u.reports_generated || 0) * 10);
  const renders = Math.min(20, (u.renders_saved || 0) * 10);
  const openRate = 15; // placeholder until Resend webhooks wired up
  const activity = activityScore(daysSince(u.signup_date));
  const referrals = Math.min(10, (u.referral_count || 0) * 5);
  const plan = planScore(u.plan_tier);
  const total = reports + renders + openRate + activity + referrals + plan;
  return Math.max(0, Math.min(100, total));
}

export function segmentFor(score) {
  if (score >= 76) return 'upgrade_ready';
  if (score >= 51) return 'hot';
  if (score >= 26) return 'warm';
  return 'cold';
}

async function triggerAgent17(userId) {
  const port = process.env.PORT || 3001;
  const secret = process.env.RUNNER_SECRET;
  if (!secret) return;
  try {
    await fetch(`http://localhost:${port}/run/17`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${secret}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ user_id: userId }),
    });
  } catch (e) {
    console.warn(`Agent 17 trigger failed: ${e.message}`);
  }
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const users = await nocodb.listAll('users', { where: '(plan_tier,eq,free)' });
    logger.log(`Scoring ${users.length} free users`);

    for (const u of users) {
      logger.increment('records_processed');
      const newScore = scoreUser(u);
      const newSegment = segmentFor(newScore);
      const wasUpgradeReady = u.segment === 'upgrade_ready';

      if (newScore === u.lead_score && newSegment === u.segment) continue;

      if (!dryRun) {
        await nocodb.update('users', u.id, {
          lead_score: newScore,
          segment: newSegment,
        });
        logger.increment('records_created');
      } else {
        logger.log(`  [DRY-RUN] ${u.email} → score=${newScore} segment=${newSegment}`);
      }

      if (newSegment === 'upgrade_ready' && !wasUpgradeReady && !dryRun) {
        await triggerAgent17(u.id);
        logger.log(`  triggered Agent 17 for ${u.email}`);
      }
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '16-lead-scoring.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
