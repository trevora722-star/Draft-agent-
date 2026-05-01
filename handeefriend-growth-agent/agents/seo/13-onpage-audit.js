import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, todayISO, writeOutput, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 13;
const AGENT_NAME = 'onpage-audit';

const PAGES = ['', '/pricing', '/design-studio', '/forensic-report', '/repair-estimate', '/blog'];

const SYSTEM_PROMPT = `You are an SEO auditor reviewing a single HandeeFriend page.

Analyze the HTML and output JSON:
{
  "url": "...",
  "title_tag": { "value": "...", "length": 0, "issue": "ok|too_short|too_long|missing_keyword", "fix": "..." },
  "meta_description": { "value": "...", "length": 0, "issue": "ok|...", "fix": "..." },
  "h1_count": 0,
  "h2_count": 0,
  "internal_links_count": 0,
  "image_alt_missing": 0,
  "fixes": [
    { "priority": "p1|p2|p3", "area": "...", "issue": "...", "recommendation": "..." }
  ]
}`;

async function fetchPage(path) {
  const base = process.env.HANDEEFRIEND_BASE_URL || 'https://handeefriend.com';
  const url = `${base}${path}`;
  try {
    const res = await fetch(url);
    if (!res.ok) return { url, error: `HTTP ${res.status}` };
    const html = await res.text();
    return { url, html: html.slice(0, 80000) };
  } catch (e) {
    return { url, error: e.message };
  }
}

function htmlReport(audits) {
  return `<div style="font-family:system-ui,sans-serif;max-width:800px;margin:0 auto;padding:16px">
    <h1>On-Page SEO Audit — ${todayISO()}</h1>
    ${audits
      .map(
        (a) => `
      <div style="border:1px solid #eee;padding:12px;border-radius:8px;margin-bottom:12px">
        <h3>${escapeHtml(a.url)}</h3>
        ${a.error ? `<p style="color:red">Error: ${escapeHtml(a.error)}</p>` : `
        <p><strong>Title:</strong> ${escapeHtml(a.title_tag?.value || '')} (${a.title_tag?.length || 0} chars) — ${escapeHtml(a.title_tag?.issue || '')}</p>
        <p><strong>Meta:</strong> ${escapeHtml(a.meta_description?.value || '')} (${a.meta_description?.length || 0} chars) — ${escapeHtml(a.meta_description?.issue || '')}</p>
        <p>H1: ${a.h1_count} · H2: ${a.h2_count} · Internal links: ${a.internal_links_count} · Missing alts: ${a.image_alt_missing}</p>
        <ol>${(a.fixes || []).map((f) => `<li>[${escapeHtml(f.priority)}] <strong>${escapeHtml(f.area)}:</strong> ${escapeHtml(f.recommendation)}</li>`).join('')}</ol>
        `}
      </div>`,
      )
      .join('')}
  </div>`;
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const audits = [];
    for (const path of PAGES) {
      logger.increment('records_processed');
      const page = await fetchPage(path);
      if (page.error) {
        audits.push({ url: page.url, error: page.error, fixes: [] });
        continue;
      }
      try {
        const audit = await callClaudeJSON({
          systemPrompt: SYSTEM_PROMPT,
          userContent: `URL: ${page.url}\n\nHTML (truncated):\n${page.html}`,
          maxTokens: 2000,
        });
        audits.push({ url: page.url, ...audit });
      } catch (e) {
        audits.push({ url: page.url, error: `audit failed: ${e.message}`, fixes: [] });
      }
    }

    const html = htmlReport(audits);
    if (!dryRun) {
      const path = await writeOutput(`seo/onpage-audit-${todayISO()}.html`, html);
      logger.log(`Audit written to ${path}`);
      if (process.env.TREVOR_EMAIL) {
        await sendEmail({
          to: process.env.TREVOR_EMAIL,
          subject: `On-page SEO audit — ${todayISO()}`,
          html,
        });
        logger.increment('emails_sent');
      }
    } else {
      console.log('[DRY-RUN] audit summary count:', audits.length);
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '13-onpage-audit.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
