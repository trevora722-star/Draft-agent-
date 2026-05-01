import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { getMRR, listSubscriptions } from '../../lib/stripe.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, todayISO, daysAgoISO, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 20;
const AGENT_NAME = 'weekly-metrics';

const SYSTEM_PROMPT = `You are an analyst summarizing weekly metrics for HandeeFriend. Output JSON:
{
  "wins": [{"title": "...", "detail": "..."}, ...3],
  "concerns": [{"title": "...", "detail": "..."}, ...3],
  "recommended_action": "..."
}
Be specific and tied to the data. Concrete numbers > vague language.`;

async function gatherMetrics(weekAgo) {
  const [users, calendar, outreach, referrals, agentLogs] = await Promise.all([
    nocodb.listAll('users'),
    nocodb.listAll('content_calendar', { where: `(CreatedAt,ge,${weekAgo})` }),
    nocodb.listAll('outreach_pipeline', { where: `(CreatedAt,ge,${weekAgo})` }),
    nocodb.listAll('referral_events', { where: `(event_date,ge,${weekAgo})` }),
    nocodb.listAll('agent_logs', { where: `(run_date,ge,${weekAgo})` }),
  ]);

  const newSignups = users.filter(
    (u) => u.signup_date && u.signup_date >= weekAgo,
  ).length;
  const paidUsers = users.filter((u) => ['starter', 'pro', 'annual'].includes(u.plan_tier));
  const segmentDist = users.reduce((acc, u) => {
    const s = u.segment || 'unknown';
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});

  const partnerSignups = outreach.filter((o) => o.stage === 'partner_signed').length;
  const emailsSent =
    outreach.filter((o) => o.email_1_sent_date && o.email_1_sent_date >= weekAgo).length +
    outreach.filter((o) => o.email_2_sent_date && o.email_2_sent_date >= weekAgo).length +
    outreach.filter((o) => o.email_3_sent_date && o.email_3_sent_date >= weekAgo).length;

  const referralBySource = referrals.reduce((acc, r) => {
    const s = r.utm_campaign || 'direct';
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});
  const topReferralSource =
    Object.entries(referralBySource).sort((a, b) => b[1] - a[1])[0]?.[0] || 'none';

  const agentFailures = agentLogs.filter((l) => l.status === 'failed');
  const agentPartials = agentLogs.filter((l) => l.status === 'partial');

  let mrr = 0;
  let stripeNew = 0;
  let stripeChurn = 0;
  if (process.env.STRIPE_SECRET_KEY) {
    try {
      mrr = await getMRR();
      const subs = await listSubscriptions({ status: 'all' });
      const weekAgoTs = new Date(weekAgo).getTime() / 1000;
      stripeNew = subs.filter((s) => s.created >= weekAgoTs && s.status === 'active').length;
      stripeChurn = subs.filter(
        (s) => s.canceled_at && s.canceled_at >= weekAgoTs,
      ).length;
    } catch (e) {
      console.warn(`Stripe metrics failed: ${e.message}`);
    }
  }

  return {
    total_subscribers: users.length,
    paid_subscribers: paidUsers.length,
    new_subscribers: newSignups,
    new_paid: stripeNew,
    churn_count: stripeChurn,
    mrr_cad: Math.round(mrr * 100) / 100,
    segment_distribution: segmentDist,
    partner_signups: partnerSignups,
    outreach_emails_sent: emailsSent,
    posts_published: calendar.filter((c) => c.script_status === 'published').length,
    scripts_approved: calendar.filter((c) => c.script_status === 'approved').length,
    referrals_this_week: referrals.length,
    top_referral_source: topReferralSource,
    agent_failures: agentFailures.map((a) => ({ id: a.agent_id, name: a.agent_name })),
    agent_partials: agentPartials.map((a) => ({ id: a.agent_id, name: a.agent_name })),
    conversion_rate:
      newSignups > 0 ? Math.round((stripeNew / newSignups) * 1000) / 10 : 0,
  };
}

function htmlReport(m, summary) {
  return `<div style="font-family:system-ui,sans-serif;max-width:680px;margin:0 auto;padding:16px;color:#222">
    <h1 style="color:#f97316">Weekly Metrics — week of ${todayISO()}</h1>
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin:16px 0">
      <div style="background:#f7f7f7;padding:12px;border-radius:8px"><div style="color:#888;font-size:12px">SUBSCRIBERS</div><div style="font-size:24px;font-weight:600">${m.total_subscribers}</div><div style="font-size:13px;color:#555">+${m.new_subscribers} this week</div></div>
      <div style="background:#f7f7f7;padding:12px;border-radius:8px"><div style="color:#888;font-size:12px">MRR (CAD)</div><div style="font-size:24px;font-weight:600">$${m.mrr_cad}</div><div style="font-size:13px;color:#555">${m.paid_subscribers} paying</div></div>
      <div style="background:#f7f7f7;padding:12px;border-radius:8px"><div style="color:#888;font-size:12px">CHURN</div><div style="font-size:24px;font-weight:600">${m.churn_count}</div></div>
      <div style="background:#f7f7f7;padding:12px;border-radius:8px"><div style="color:#888;font-size:12px">CONVERSION</div><div style="font-size:24px;font-weight:600">${m.conversion_rate}%</div></div>
    </div>
    <h3>Wins</h3>
    <ul>${(summary.wins || []).map((w) => `<li><strong>${escapeHtml(w.title)}</strong> — ${escapeHtml(w.detail)}</li>`).join('')}</ul>
    <h3>Concerns</h3>
    <ul>${(summary.concerns || []).map((w) => `<li><strong>${escapeHtml(w.title)}</strong> — ${escapeHtml(w.detail)}</li>`).join('')}</ul>
    <h3>Recommended action</h3>
    <p style="background:#fff7ed;padding:12px;border-left:4px solid #f97316">${escapeHtml(summary.recommended_action || '')}</p>
    <h3>Agent health</h3>
    <p>Failures: ${m.agent_failures.length} · Partials: ${m.agent_partials.length}</p>
    <pre style="background:#111;color:#0f0;padding:12px;font-size:12px;overflow-x:auto">${escapeHtml(JSON.stringify(m, null, 2))}</pre>
  </div>`;
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const weekAgo = daysAgoISO(7);
    const metrics = await gatherMetrics(weekAgo);
    logger.log(`Aggregated metrics: subs=${metrics.total_subscribers} mrr=${metrics.mrr_cad}`);
    logger.increment('records_processed');

    const summary = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent: `Metrics this week:\n${JSON.stringify(metrics, null, 2)}`,
      maxTokens: 2000,
    });

    const html = htmlReport(metrics, summary);
    const recipients = [process.env.TREVOR_EMAIL, process.env.FAYDRA_EMAIL].filter(Boolean);

    if (!dryRun) {
      const snapshot = {
        week_date: todayISO(),
        total_subscribers: metrics.total_subscribers,
        new_subscribers: metrics.new_subscribers,
        mrr_cad: metrics.mrr_cad,
        churn_count: metrics.churn_count,
        conversion_rate: metrics.conversion_rate,
        top_referral_source: metrics.top_referral_source,
        emails_sent: metrics.outreach_emails_sent,
        community_posts: 0,
        snapshot_json: JSON.stringify({ metrics, summary }),
      };
      await nocodb.create('weekly_metrics', snapshot);
      logger.increment('records_created');

      if (recipients.length) {
        await sendEmail({
          to: recipients,
          subject: `Weekly Metrics — ${todayISO()}`,
          html,
        });
        logger.increment('emails_sent');
      }
    } else {
      console.log('[DRY-RUN] summary:', JSON.stringify(summary, null, 2));
    }

    await logger.finish('success');
    return { metrics, summary };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '20-weekly-metrics.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
