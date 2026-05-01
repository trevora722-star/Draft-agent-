import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { listSubscriptions } from '../../lib/stripe.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, daysAgoISO, todayISO, writeOutput, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 22;
const AGENT_NAME = 'funnel-analysis';

const SYSTEM_PROMPT = `You are a growth analyst studying HandeeFriend's funnel for the last 30 days.

Identify the SINGLE biggest drop-off point and propose a specific, testable hypothesis to fix it.

Return JSON:
{
  "biggest_drop": { "from_stage": "...", "to_stage": "...", "drop_pct": 0, "context": "..." },
  "hypothesis": "...",
  "test_design": { "what_to_change": "...", "metric": "...", "target_lift": "...", "duration_days": 14 },
  "secondary_observations": ["...", "..."]
}`;

async function buildFunnel() {
  const since = daysAgoISO(30);
  const [users, referralEvents] = await Promise.all([
    nocodb.listAll('users'),
    nocodb.listAll('referral_events', { where: `(event_date,ge,${since})` }),
  ]);

  const new30 = users.filter((u) => u.signup_date && u.signup_date >= since);
  const trialStarts = new30.length;
  const secondReport = new30.filter((u) => (u.reports_generated || 0) >= 2).length;
  const upgradeClick = new30.filter((u) => u.active_promo_code).length;
  const paid = new30.filter((u) => ['starter', 'pro', 'annual'].includes(u.plan_tier)).length;

  let retained30 = 0;
  if (process.env.STRIPE_SECRET_KEY) {
    try {
      const subs = await listSubscriptions({ status: 'all' });
      const cutoff = Date.now() / 1000 - 30 * 86400;
      retained30 = subs.filter(
        (s) => s.created <= cutoff && (s.status === 'active' || s.status === 'trialing'),
      ).length;
    } catch {}
  }

  const visits = trialStarts ? trialStarts * 12 : 0; // proxy until GA4 wired up

  return {
    site_visits: visits,
    trial_starts: trialStarts,
    second_report: secondReport,
    upgrade_prompt_click: upgradeClick,
    paid,
    retained_30d: retained30,
    referral_events_count: referralEvents.length,
  };
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const funnel = await buildFunnel();
    logger.log(`Funnel: ${JSON.stringify(funnel)}`);
    logger.increment('records_processed');

    const analysis = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent: `Funnel data:\n${JSON.stringify(funnel, null, 2)}\n\nNote: site_visits is currently a proxy estimate.`,
      maxTokens: 3000,
      thinkingEnabled: true,
    });

    const html = `<div style="font-family:system-ui,sans-serif;max-width:680px;margin:0 auto;padding:16px">
      <h1>Funnel Analysis — ${todayISO()}</h1>
      <pre style="background:#f7f7f7;padding:12px">${escapeHtml(JSON.stringify(funnel, null, 2))}</pre>
      <h2 style="color:#f97316">Biggest drop: ${escapeHtml(analysis.biggest_drop?.from_stage || '')} → ${escapeHtml(analysis.biggest_drop?.to_stage || '')} (${analysis.biggest_drop?.drop_pct || 0}%)</h2>
      <p>${escapeHtml(analysis.biggest_drop?.context || '')}</p>
      <h3>Hypothesis</h3>
      <p>${escapeHtml(analysis.hypothesis || '')}</p>
      <h3>Test design</h3>
      <p><strong>Change:</strong> ${escapeHtml(analysis.test_design?.what_to_change || '')}</p>
      <p><strong>Metric:</strong> ${escapeHtml(analysis.test_design?.metric || '')}</p>
      <p><strong>Target lift:</strong> ${escapeHtml(analysis.test_design?.target_lift || '')}</p>
      <p><strong>Duration:</strong> ${analysis.test_design?.duration_days || 14} days</p>
      <h3>Other observations</h3>
      <ul>${(analysis.secondary_observations || []).map((o) => `<li>${escapeHtml(o)}</li>`).join('')}</ul>
    </div>`;

    if (!dryRun) {
      const path = await writeOutput(`analytics/funnel-${todayISO()}.html`, html);
      logger.log(`Funnel report → ${path}`);
      if (process.env.TREVOR_EMAIL) {
        await sendEmail({
          to: process.env.TREVOR_EMAIL,
          subject: `Funnel analysis — biggest drop: ${analysis.biggest_drop?.from_stage} → ${analysis.biggest_drop?.to_stage}`,
          html,
        });
        logger.increment('emails_sent');
      }
    } else {
      console.log('[DRY-RUN]', JSON.stringify(analysis, null, 2));
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '22-funnel-analysis.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
