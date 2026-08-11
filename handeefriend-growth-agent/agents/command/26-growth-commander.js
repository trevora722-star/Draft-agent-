import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { getMRR } from '../../lib/stripe.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, todayISO, daysAgoISO, writeOutput, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 26;
const AGENT_NAME = 'growth-commander';
const TARGET_SUBS = 1000;

const SYSTEM_PROMPT = `You are the Growth Commander for HandeeFriend (handeefriend.com), an AI-powered home platform targeting 1,000 subscribers in 6 months.

You receive weekly data from 25 specialized agents across content, outreach, SEO, CRM, analytics, and retention departments.

Your job: synthesize everything into a clear, actionable weekly brief for the founders (Trevor and Faydra). They are busy operators — give them signal, not noise.

Format your output as JSON:
{
  "week_summary": "2–3 sentences on where things stand vs. the 1,000-subscriber goal",
  "subscriber_count": 0,
  "mrr_cad": 0,
  "wins": [{"title": "...", "detail": "..."}],
  "blockers": [{"title": "...", "detail": "...", "recommended_fix": "..."}],
  "priority_actions": [{"rank": 1, "action": "...", "owner": "Trevor|Faydra|Agent XX", "why": "..."}],
  "agent_health": [{"agent_id": 0, "status": "ok|warning|failed", "note": "..."}]
}

3 wins, 3 blockers, 5 priority actions. Be direct. If something isn't working, say so specifically. If a channel is outperforming, say why and recommend doubling down.`;

async function gatherContext() {
  const since = daysAgoISO(7);
  const [users, calendar, outreach, keywords, refs, partners, agentLogs, weekly, ads] =
    await Promise.all([
      nocodb.listAll('users'),
      nocodb.listAll('content_calendar', { where: `(CreatedAt,ge,${since})` }),
      nocodb.listAll('outreach_pipeline'),
      nocodb.listAll('keyword_tracker'),
      nocodb.listAll('referral_events', { where: `(event_date,ge,${since})` }),
      nocodb.listAll('partner_registry', { where: '(status,eq,active)' }),
      nocodb.listAll('agent_logs', { where: `(run_date,ge,${since})` }),
      nocodb.listAll('weekly_metrics'),
      nocodb.listAll('ad_creative'),
    ]);

  let mrr = 0;
  if (process.env.STRIPE_SECRET_KEY) {
    try {
      mrr = await getMRR();
    } catch {}
  }

  const agentSummary = agentLogs.reduce((acc, log) => {
    const key = log.agent_id;
    if (!acc[key]) acc[key] = { runs: 0, success: 0, partial: 0, failed: 0, name: log.agent_name };
    acc[key].runs++;
    acc[key][log.status]++;
    return acc;
  }, {});

  return {
    target_subscribers: TARGET_SUBS,
    subscriber_count: users.length,
    paid_subscribers: users.filter((u) => ['starter', 'pro', 'annual'].includes(u.plan_tier)).length,
    new_signups_this_week: users.filter((u) => u.signup_date && u.signup_date >= since).length,
    mrr_cad: Math.round(mrr * 100) / 100,
    segment_distribution: users.reduce((a, u) => {
      a[u.segment || 'unknown'] = (a[u.segment || 'unknown'] || 0) + 1;
      return a;
    }, {}),
    content_pipeline: {
      total_recent: calendar.length,
      by_status: calendar.reduce((a, c) => {
        a[c.script_status || 'unknown'] = (a[c.script_status || 'unknown'] || 0) + 1;
        return a;
      }, {}),
    },
    outreach_pipeline: {
      total: outreach.length,
      by_stage: outreach.reduce((a, o) => {
        a[o.stage || 'unknown'] = (a[o.stage || 'unknown'] || 0) + 1;
        return a;
      }, {}),
      partner_signups_total: outreach.filter((o) => o.stage === 'partner_signed').length,
    },
    keywords: { total: keywords.length, p1: keywords.filter((k) => k.priority === 'p1').length },
    referral_events_this_week: refs.length,
    active_partners: partners.length,
    agent_summary: agentSummary,
    last_week_metrics: weekly.slice(-1)[0] || null,
    ad_creative_total: ads.length,
  };
}

function htmlBrief(brief, ctx) {
  const pct = ((ctx.subscriber_count / ctx.target_subscribers) * 100).toFixed(1);
  return `<div style="font-family:system-ui,sans-serif;max-width:720px;margin:0 auto;padding:16px;color:#111">
    <h1 style="color:#f97316">Growth Commander — Weekly Brief</h1>
    <p style="font-size:18px">${escapeHtml(brief.week_summary || '')}</p>
    <div style="background:#fff7ed;border-radius:8px;padding:16px;margin:16px 0">
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
        <div><div style="font-size:12px;color:#888">SUBSCRIBERS</div><div style="font-size:22px;font-weight:600">${brief.subscriber_count}</div><div style="font-size:12px">${pct}% of ${ctx.target_subscribers}</div></div>
        <div><div style="font-size:12px;color:#888">MRR</div><div style="font-size:22px;font-weight:600">$${brief.mrr_cad}</div></div>
        <div><div style="font-size:12px;color:#888">PAID</div><div style="font-size:22px;font-weight:600">${ctx.paid_subscribers}</div></div>
      </div>
    </div>
    <h2 style="color:#16a34a">Wins</h2>
    <ul>${(brief.wins || []).map((w) => `<li><strong>${escapeHtml(w.title)}</strong> — ${escapeHtml(w.detail)}</li>`).join('')}</ul>
    <h2 style="color:#dc2626">Blockers</h2>
    <ul>${(brief.blockers || []).map((b) => `<li><strong>${escapeHtml(b.title)}</strong> — ${escapeHtml(b.detail)}<br><em style="color:#666">Fix: ${escapeHtml(b.recommended_fix)}</em></li>`).join('')}</ul>
    <h2>Priority Actions for Next Week</h2>
    <ol>${(brief.priority_actions || [])
      .sort((a, b) => a.rank - b.rank)
      .map((a) => `<li><strong>${escapeHtml(a.action)}</strong> — ${escapeHtml(a.owner)} <br><span style="color:#666">${escapeHtml(a.why)}</span></li>`)
      .join('')}</ol>
    <h3>Agent Health</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr style="background:#eee"><th style="text-align:left;padding:6px">Agent</th><th style="padding:6px">Status</th><th style="text-align:left;padding:6px">Note</th></tr>
      ${(brief.agent_health || []).map((a) => `<tr style="border-bottom:1px solid #eee"><td style="padding:6px">${a.agent_id}</td><td style="padding:6px;color:${a.status === 'failed' ? '#dc2626' : a.status === 'warning' ? '#f97316' : '#16a34a'}">${escapeHtml(a.status)}</td><td style="padding:6px">${escapeHtml(a.note || '')}</td></tr>`).join('')}
    </table>
  </div>`;
}

async function kickoffMonday() {
  const port = process.env.PORT || 3001;
  const secret = process.env.RUNNER_SECRET;
  if (!secret) return;
  const targets = ['16', '10'];
  for (const a of targets) {
    try {
      await fetch(`http://localhost:${port}/run/${a}`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${secret}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
      });
    } catch (e) {
      console.warn(`Kickoff ${a} failed: ${e.message}`);
    }
  }
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const ctx = await gatherContext();
    logger.log(`Context built: subs=${ctx.subscriber_count} mrr=${ctx.mrr_cad} agents-run=${Object.keys(ctx.agent_summary).length}`);
    logger.increment('records_processed');

    const brief = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent: `Weekly context:\n${JSON.stringify(ctx, null, 2)}`,
      maxTokens: 8000,
      thinkingEnabled: true,
    });

    const html = htmlBrief(brief, ctx);

    if (!dryRun) {
      const path = await writeOutput(`command/commander-${todayISO()}.json`, { brief, ctx });
      logger.log(`Snapshot → ${path}`);

      const recipients = [process.env.TREVOR_EMAIL, process.env.FAYDRA_EMAIL].filter(Boolean);
      if (recipients.length) {
        await sendEmail({
          to: recipients,
          subject: `Growth Commander Brief — ${todayISO()}`,
          html,
        });
        logger.increment('emails_sent');
      }

      await kickoffMonday();
    } else {
      console.log('[DRY-RUN]', JSON.stringify(brief, null, 2));
    }

    await logger.finish('success');
    return brief;
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '26-growth-commander.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
