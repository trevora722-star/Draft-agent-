import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, todayISO, weekNumber, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 4;
const AGENT_NAME = 'youtube-repurpose';

const SYSTEM_PROMPT = `You repurpose an approved TikTok script for YouTube Shorts. Output JSON:
{
  "title": "Under 60 chars, keyword-forward",
  "description": "3 paragraphs: hook recap, HandeeFriend value, CTA. 300–400 chars total.",
  "hashtags": ["#homerepair", "#canadianhomeowner", "..."],
  "pinned_comment": "The comment Faydra posts in the first 10 minutes",
  "thumbnail_text": "2–4 words for thumbnail overlay"
}
Exactly 8 hashtags. Canadian/BC angle.`;

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const id =
      options.content_calendar_id ||
      process.argv.find((a) => a.startsWith('--id='))?.split('=')[1];
    if (!id) throw new Error('content_calendar_id required');

    const tiktok = await nocodb.get('content_calendar', id);
    if (!tiktok) throw new Error(`No content_calendar row id=${id}`);
    if (tiktok.script_status !== 'approved') {
      throw new Error(`Source script not approved (status=${tiktok.script_status})`);
    }

    let script;
    try {
      script = JSON.parse(tiktok.script_content);
    } catch {
      script = { hook: '', body: tiktok.script_content || '', cta: '' };
    }

    logger.increment('records_processed');

    const result = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent: `TikTok script:
Hook: ${script.hook || ''}
Body: ${script.body || ''}
CTA: ${script.cta || ''}
Pillar: ${tiktok.pillar || ''}

Generate the YouTube Shorts metadata.`,
      maxTokens: 1500,
    });

    if (!dryRun) {
      const created = await nocodb.create('content_calendar', {
        week_number: weekNumber(),
        publish_date: todayISO(),
        content_type: 'youtube',
        platform: 'youtube',
        pillar: tiktok.pillar,
        script_status: 'draft',
        script_content: JSON.stringify(result),
        agent_id: AGENT_ID,
      });
      logger.increment('records_created');

      if (process.env.FAYDRA_EMAIL) {
        await sendEmail({
          to: process.env.FAYDRA_EMAIL,
          subject: `YouTube Shorts ready: ${result.title}`,
          html: `<div style="font-family:system-ui,sans-serif">
            <h2>${escapeHtml(result.title)}</h2>
            <p><strong>Thumbnail text:</strong> ${escapeHtml(result.thumbnail_text)}</p>
            <p><strong>Description:</strong></p>
            <pre style="background:#f7f7f7;padding:12px;white-space:pre-wrap">${escapeHtml(result.description)}</pre>
            <p><strong>Hashtags:</strong> ${(result.hashtags || []).map(escapeHtml).join(' ')}</p>
            <p><strong>Pinned comment:</strong></p>
            <blockquote style="border-left:3px solid #f97316;padding-left:8px">${escapeHtml(result.pinned_comment)}</blockquote>
          </div>`,
        });
        logger.increment('emails_sent');
      }
    } else {
      console.log('[DRY-RUN]', JSON.stringify(result, null, 2));
    }

    await logger.finish('success');
    return result;
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '04-youtube-repurpose.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
