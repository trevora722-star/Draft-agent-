import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, todayISO, weekNumber, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 1;
const AGENT_NAME = 'tiktok-script';

const SYSTEM_PROMPT = `You are Faydra Aldridge's content strategist for HandeeFriend (handeefriend.com), an AI-powered home repair estimate, Forensic Condition Report, Design Studio Render, and Strategic Portfolio platform for Canadian homeowners.

Faydra is the public face — a confident, real, BC-based woman who speaks like a homeowner, not a marketer. Short sentences. Real numbers. Zero corporate language. She lives in West Kelowna, BC.

CONTENT PILLARS:
- before_after: Show a Design Studio Render transformation (before/after). Visual hook. Shareability.
- shock_estimate: AI estimates a repair most homeowners underestimate. Shock + relief.
- myth_bust: "Your contractor said X. The AI said Y." Creates controversy. Gets comments.
- live_report: Faydra runs the Forensic Condition Report on her own or a friend's property. Raw, authentic.

SCRIPT FORMAT (return as JSON array of 5 objects):
{
  "variant": 1,
  "hook": "First 3 seconds — the single sentence that stops the scroll",
  "body": "Seconds 4–45 — the story, the reveal, the proof",
  "cta": "Final 5 seconds — exactly what to say, what to show",
  "shot_notes": "Camera direction, props, suggested b-roll",
  "predicted_hook_score": 8,
  "pillar": "shock_estimate"
}

Rules:
- Never use "AI-powered" as an opener. Too generic.
- Every hook must be a question, a shocking number, or an unfinished sentence.
- CTAs always mention the bio link and name the specific free feature being offered.
- Write as Faydra speaks — casual, confident, occasionally sarcastic.

Return a JSON array of exactly 5 variants.`;

async function pullTopPerformers() {
  try {
    const res = await nocodb.list('content_calendar', {
      where: '(content_type,eq,tiktok)~and(performance_score,gt,0)',
      sort: '-performance_score',
      limit: 3,
    });
    return res.list || res.records || [];
  } catch {
    return [];
  }
}

async function pullPendingSlots() {
  try {
    const res = await nocodb.list('content_calendar', {
      where: '(content_type,eq,tiktok)~and(script_status,eq,pending)',
      limit: 2,
    });
    return res.list || res.records || [];
  } catch {
    return [];
  }
}

function summarizeTopPerformers(rows) {
  if (!rows.length) return 'No prior performance data yet.';
  return rows
    .map(
      (r, i) =>
        `${i + 1}. Pillar: ${r.pillar || '—'} | Hook excerpt: ${(r.script_content || '').slice(0, 150)}… | Score: ${r.performance_score || 0}`,
    )
    .join('\n');
}

function emailHtml({ scripts, weekNum }) {
  const cards = scripts
    .map(
      (s) => `
    <div style="border:1px solid #eee;border-radius:8px;padding:16px;margin-bottom:16px">
      <div style="color:#f97316;font-weight:600;font-size:13px">VARIANT ${s.variant} · ${escapeHtml(s.pillar || '')} · score ${s.predicted_hook_score || '?'}/10</div>
      <h3 style="margin:8px 0;color:#111">${escapeHtml(s.hook)}</h3>
      <p style="color:#333;line-height:1.5;margin:0 0 8px"><strong>Body:</strong> ${escapeHtml(s.body)}</p>
      <p style="color:#333;line-height:1.5;margin:0 0 8px"><strong>CTA:</strong> ${escapeHtml(s.cta)}</p>
      <p style="color:#666;font-size:13px;margin:0"><strong>Shots:</strong> ${escapeHtml(s.shot_notes || '')}</p>
    </div>`,
    )
    .join('');
  return `<div style="font-family:system-ui,sans-serif;max-width:640px;margin:0 auto;padding:16px">
    <h2 style="color:#111">Week ${weekNum} TikTok scripts — ready for review</h2>
    <p style="color:#555">5 variants below. Pick your favourites and mark <code>script_status=approved</code> in NocoDB.</p>
    ${cards}
  </div>`;
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const [topPerformers, pendingSlots] = await Promise.all([
      pullTopPerformers(),
      pullPendingSlots(),
    ]);
    logger.log(
      `Found ${topPerformers.length} top performers, ${pendingSlots.length} pending slots`,
    );

    const targetPillar = pendingSlots[0]?.pillar || 'shock_estimate';
    const userContent = `This week's target pillar: ${targetPillar}

Top performing past scripts:
${summarizeTopPerformers(topPerformers)}

Pending content slots this week: ${pendingSlots.length || 'none yet — generate generic exploratory variants'}

Generate 5 TikTok script variants. Mix pillars but lean into the target pillar.`;

    const scripts = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent,
      maxTokens: 4000,
    });

    if (!Array.isArray(scripts)) {
      throw new Error('Claude did not return an array of scripts');
    }

    const weekNum = weekNumber();
    logger.increment('records_processed', scripts.length);

    for (const s of scripts) {
      const row = {
        week_number: weekNum,
        publish_date: todayISO(),
        content_type: 'tiktok',
        pillar: s.pillar || targetPillar,
        script_status: 'draft',
        platform: 'tiktok',
        script_content: JSON.stringify(s, null, 2),
        agent_id: AGENT_ID,
      };
      if (!dryRun) {
        await nocodb.create('content_calendar', row);
        logger.increment('records_created');
      }
    }

    if (!dryRun && process.env.FAYDRA_EMAIL) {
      await sendEmail({
        to: process.env.FAYDRA_EMAIL,
        subject: `Week ${weekNum} TikTok scripts — 5 ready`,
        html: emailHtml({ scripts, weekNum }),
      });
      logger.increment('emails_sent');
    }

    await logger.finish('success');
    return { count: scripts.length, weekNum };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '01-tiktok-script.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
