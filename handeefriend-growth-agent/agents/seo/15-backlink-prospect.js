import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, escapeHtml, nowISO } from '../../lib/utils.js';

const AGENT_ID = 15;
const AGENT_NAME = 'backlink-prospect';

const SYSTEM_PROMPT = `You are an outreach strategist for HandeeFriend. Generate 20 BC/Canadian home improvement, real estate, or homeowner-focused websites worth pursuing for guest posts or citations.

For each, output: domain, da_estimate (rough 1-100), pitch_angle, contact_email (best guess like info@domain.com if unknown).

Then for the top 5, draft a short, tailored outreach email each.

Return JSON:
{
  "prospects": [
    { "domain": "...", "da_estimate": 35, "pitch_angle": "...", "contact_email": "..." }
  ],
  "drafts": [
    { "domain": "...", "subject": "...", "html": "..." }
  ]
}`;

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const result = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent:
        'Generate 20 prospects targeting Canadian/BC homeowner audiences. Then draft 5 outreach emails for the strongest 5.',
      maxTokens: 4000,
    });

    logger.increment('records_processed', (result.prospects || []).length);

    if (!dryRun) {
      for (const p of result.prospects || []) {
        const draftMatch = (result.drafts || []).find((d) => d.domain === p.domain);
        try {
          await nocodb.create('backlink_prospects', {
            domain: p.domain,
            da_estimate: p.da_estimate,
            pitch_angle: p.pitch_angle,
            contact_email: p.contact_email,
            status: 'draft',
            outreach_date: nowISO(),
            draft_email: draftMatch ? JSON.stringify(draftMatch) : null,
          });
          logger.increment('records_created');
        } catch (e) {
          logger.error(`Save failed for ${p.domain}: ${e.message}`);
        }
      }

      if (process.env.TREVOR_EMAIL) {
        const html = `<div style="font-family:system-ui,sans-serif;max-width:680px">
          <h2>Backlink prospects + 5 ready-to-send drafts</h2>
          <h3>Prospects</h3>
          <ul>${(result.prospects || []).map((p) => `<li><strong>${escapeHtml(p.domain)}</strong> (DA ~${p.da_estimate}) — ${escapeHtml(p.pitch_angle)}</li>`).join('')}</ul>
          <h3>Top 5 drafts</h3>
          ${(result.drafts || []).map((d) => `<div style="border:1px solid #eee;padding:12px;border-radius:8px;margin-bottom:8px"><h4>${escapeHtml(d.domain)} — ${escapeHtml(d.subject)}</h4>${d.html}</div>`).join('')}
        </div>`;
        await sendEmail({
          to: process.env.TREVOR_EMAIL,
          subject: `Backlink prospects — ${(result.prospects || []).length} found`,
          html,
        });
        logger.increment('emails_sent');
      }
    } else {
      console.log('[DRY-RUN] prospects:', (result.prospects || []).length, 'drafts:', (result.drafts || []).length);
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '15-backlink-prospect.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
