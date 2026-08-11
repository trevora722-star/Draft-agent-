import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 5;
const AGENT_NAME = 'ad-copy';

const CAMPAIGN_PROMPTS = {
  before_after: `Focus on the Design Studio Render. The transformation is the product. Show the gap between ugly and beautiful. Hook: visual shock.`,
  lead_magnet: `The free Forensic Condition Report. Lead with the value — homeowners are scared of hidden problems. We surface them for free.`,
  testimonial: `Write as if a real BC homeowner is talking. Specific. Numbered. "It found $4,200 in issues I didn't know about." Believable, not hype.`,
};

const SYSTEM_PROMPT_BASE = `You are writing Facebook/Instagram ad copy for HandeeFriend (handeefriend.com), a Canadian home AI platform.

Rules for every piece:
- Headlines under 40 characters
- Primary text under 125 characters
- No exclamation marks in headlines
- Canada/BC context where possible
- CTA options: "Try Free", "Get Your Report", "See the Render"

For each campaign, return JSON:
{
  "headline_1": "...", "headline_2": "...", "headline_3": "...",
  "primary_text_1": "...", "primary_text_2": "...", "primary_text_3": "...",
  "description": "...",
  "audience_tag": "bc_homeowners|realtors|diy|lookalike"
}`;

async function fetchTopContent() {
  try {
    const res = await nocodb.list('content_calendar', {
      sort: '-trial_starts_attributed',
      limit: 5,
    });
    return res.list || res.records || [];
  } catch {
    return [];
  }
}

async function buildCampaign(type, topContent) {
  const userContent = `Campaign type: ${type}
Brief: ${CAMPAIGN_PROMPTS[type]}

Best-performing recent content for inspiration:
${topContent
  .map((c) => `- ${c.content_type} | ${c.keyword || c.pillar || ''} | trials: ${c.trial_starts_attributed || 0}`)
  .join('\n') || '(no historical data yet)'}

Write the ad creative now.`;
  return callClaudeJSON({
    systemPrompt: SYSTEM_PROMPT_BASE,
    userContent,
    maxTokens: 1500,
  });
}

function htmlDeck(campaigns) {
  return `<div style="font-family:system-ui,sans-serif;max-width:680px;margin:0 auto;padding:16px">
    <h1>Ad creative deck</h1>
    ${campaigns
      .map(
        (c) => `
      <div style="border:1px solid #eee;border-radius:8px;padding:16px;margin-bottom:16px">
        <h3 style="color:#f97316;text-transform:uppercase">${escapeHtml(c.campaign_type)}</h3>
        <p><strong>Audience:</strong> ${escapeHtml(c.creative.audience_tag || '—')}</p>
        <h4>Headlines</h4>
        <ol>
          <li>${escapeHtml(c.creative.headline_1 || '')}</li>
          <li>${escapeHtml(c.creative.headline_2 || '')}</li>
          <li>${escapeHtml(c.creative.headline_3 || '')}</li>
        </ol>
        <h4>Primary text</h4>
        <ol>
          <li>${escapeHtml(c.creative.primary_text_1 || '')}</li>
          <li>${escapeHtml(c.creative.primary_text_2 || '')}</li>
          <li>${escapeHtml(c.creative.primary_text_3 || '')}</li>
        </ol>
        <p><strong>Description:</strong> ${escapeHtml(c.creative.description || '')}</p>
      </div>`,
      )
      .join('')}
  </div>`;
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const topContent = await fetchTopContent();
    const created_month = new Date().getUTCMonth() + 1;

    const types = ['before_after', 'lead_magnet', 'testimonial'];
    const campaigns = [];

    for (const t of types) {
      logger.increment('records_processed');
      const creative = await buildCampaign(t, topContent);
      campaigns.push({ campaign_type: t, creative });

      if (!dryRun) {
        await nocodb.create('ad_creative', {
          campaign_type: t,
          headline_1: creative.headline_1,
          headline_2: creative.headline_2,
          headline_3: creative.headline_3,
          primary_text_1: creative.primary_text_1,
          primary_text_2: creative.primary_text_2,
          primary_text_3: creative.primary_text_3,
          description: creative.description,
          audience_tag: creative.audience_tag || 'bc_homeowners',
          status: 'draft',
          created_month,
        });
        logger.increment('records_created');
      }
    }

    if (!dryRun) {
      const recipients = [process.env.TREVOR_EMAIL, process.env.FAYDRA_EMAIL].filter(Boolean);
      if (recipients.length) {
        await sendEmail({
          to: recipients,
          subject: `Ad creative deck — month ${created_month}`,
          html: htmlDeck(campaigns),
        });
        logger.increment('emails_sent');
      }
    } else {
      console.log('[DRY-RUN]', JSON.stringify(campaigns, null, 2));
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '05-ad-copy.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
