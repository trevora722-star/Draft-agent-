import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { applyAccountCredit } from '../../lib/stripe.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, daysAgoISO, todayISO } from '../../lib/utils.js';

const AGENT_ID = 24;
const AGENT_NAME = 'partner-dashboard';
const COMMISSION_PCT = 0.10;

const SYSTEM_PROMPT = `You are Faydra writing a monthly partner report email. Tone: professional, grateful, specific. Include the actual numbers given. JSON: { "subject": "...", "html": "..." }
HTML inline-styled, max 600px wide.`;

async function fetchActivePartners() {
  return nocodb.listAll('partner_registry', { where: '(status,eq,active)' });
}

async function partnerMetricsThisMonth(partner) {
  const since = daysAgoISO(30);
  const events = await nocodb.listAll('referral_events', {
    where: `(referrer_user_id,eq,${partner.id})~and(event_date,ge,${since})`,
  });
  const referrals = events.length;
  const conversions = events.filter((e) => e.event_type === 'paid_convert').length;
  // Commission: assume avg paid customer = $19.99/mo, 10% commission
  const commissionThisMonth = +(conversions * 19.99 * COMMISSION_PCT).toFixed(2);
  return { referrals, conversions, commissionThisMonth };
}

async function generateReport(partner, metrics) {
  const userContent = `Partner: ${partner.name} (${partner.type}, ${partner.city})
Their referral link: ${partner.referral_link}
This month: ${metrics.referrals} referrals, ${metrics.conversions} conversions
Commission earned this month: CAD $${metrics.commissionThisMonth}
Total earnings to date: CAD $${partner.total_commission_cad || 0}`;
  return callClaudeJSON({ systemPrompt: SYSTEM_PROMPT, userContent, maxTokens: 1500 });
}

async function maybeQuarterlySpotlight({ logger, dryRun, partners, metricsByPartner }) {
  const month = new Date().getUTCMonth() + 1;
  if (![3, 6, 9, 12].includes(month)) return;

  const top = partners
    .map((p) => ({ partner: p, metrics: metricsByPartner.get(p.id) || {} }))
    .sort((a, b) => (b.metrics.commissionThisMonth || 0) - (a.metrics.commissionThisMonth || 0))[0];
  if (!top) return;

  const userContent = `Quarterly spotlight on top partner: ${top.partner.name} (${top.partner.type}). Conversions this month: ${top.metrics.conversions || 0}. Write a short congratulatory email + a public-friendly spotlight blurb. JSON: { "subject": "...", "html": "...", "spotlight_blurb": "..." }`;
  const sp = await callClaudeJSON({
    systemPrompt: 'You are Faydra spotlighting a top partner. JSON output only.',
    userContent,
    maxTokens: 1500,
  });
  if (!dryRun && top.partner.email) {
    await sendEmail({ to: top.partner.email, subject: sp.subject, html: sp.html });
    logger.increment('emails_sent');
  }
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const partners = await fetchActivePartners();
    logger.log(`Active partners: ${partners.length}`);
    const metricsByPartner = new Map();

    for (const p of partners) {
      logger.increment('records_processed');
      const m = await partnerMetricsThisMonth(p);
      metricsByPartner.set(p.id, m);

      if (m.commissionThisMonth > 0 && p.email) {
        try {
          // Stripe credit (if customer record exists; partners may not have one)
          if (!dryRun && p.stripe_customer_id) {
            await applyAccountCredit({
              customerId: p.stripe_customer_id,
              amountCad: m.commissionThisMonth,
              description: `Partner commission ${todayISO()}`,
            });
          }

          const report = await generateReport(p, m);
          if (!dryRun) {
            await sendEmail({ to: p.email, subject: report.subject, html: report.html });
            logger.increment('emails_sent');

            await nocodb.update('partner_registry', p.id, {
              total_referrals: (p.total_referrals || 0) + m.referrals,
              total_conversions: (p.total_conversions || 0) + m.conversions,
              total_commission_cad: (p.total_commission_cad || 0) + m.commissionThisMonth,
              stripe_credit_applied: (p.stripe_credit_applied || 0) + m.commissionThisMonth,
            });
          }
        } catch (e) {
          logger.error(`partner report failed for ${p.email}: ${e.message}`);
        }
      }
    }

    await maybeQuarterlySpotlight({ logger, dryRun, partners, metricsByPartner });
    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '24-partner-dashboard.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
