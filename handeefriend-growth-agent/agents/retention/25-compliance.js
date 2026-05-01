import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, todayISO, writeOutput, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 25;
const AGENT_NAME = 'compliance';

const SYSTEM_PROMPT = `You are a compliance reviewer for HandeeFriend (Canadian SaaS).
Review the findings and produce a JSON checklist with status:
{
  "casl": { "status": "pass|fail|warning", "notes": "..." },
  "asc": { "status": "pass|fail|warning", "notes": "..." },
  "bc_consumer_protection": { "status": "pass|fail|warning", "notes": "..." },
  "issues": [{ "severity": "high|medium|low", "area": "...", "issue": "...", "recommendation": "..." }]
}`;

async function fetchSitePagesForAffiliate() {
  const base = process.env.HANDEEFRIEND_BASE_URL || 'https://handeefriend.com';
  const paths = ['/blog', '/'];
  const results = [];
  for (const p of paths) {
    try {
      const res = await fetch(`${base}${p}`);
      if (!res.ok) continue;
      const html = await res.text();
      const hasAffiliate = /affiliate|commission|partner link/i.test(html);
      const hasDisclosure = /disclosure|affiliate disclosure|partner disclosure|in partnership/i.test(html);
      results.push({ url: `${base}${p}`, hasAffiliate, hasDisclosure });
    } catch (e) {
      results.push({ url: `${base}${p}`, error: e.message });
    }
  }
  return results;
}

async function checkEmailTemplates() {
  const tmpls = await nocodb.listAll('email_templates');
  const issues = [];
  for (const t of tmpls) {
    const body = t.html_body || '';
    if (!/{{unsubscribe_url}}/.test(body)) {
      issues.push({ template: `${t.sequence_name}#${t.email_number}`, problem: 'missing {{unsubscribe_url}}' });
    }
    if (!/handeefriend|West Kelowna/i.test(body)) {
      issues.push({ template: `${t.sequence_name}#${t.email_number}`, problem: 'missing sender footer' });
    }
  }
  return { total: tmpls.length, issues };
}

async function checkConsent() {
  const violators = await nocodb.listAll('users', { where: '(consent_status,eq,false)' });
  return { count: violators.length, sample: violators.slice(0, 5).map((v) => v.email) };
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const [sitePages, emailCheck, consentCheck] = await Promise.all([
      fetchSitePagesForAffiliate(),
      checkEmailTemplates(),
      checkConsent(),
    ]);

    logger.increment('records_processed', sitePages.length + emailCheck.total);

    const checklist = await callClaudeJSON({
      systemPrompt: SYSTEM_PROMPT,
      userContent: `Findings:
Site affiliate disclosure: ${JSON.stringify(sitePages, null, 2)}
Email template issues: ${JSON.stringify(emailCheck, null, 2)}
Users without consent receiving messaging: ${JSON.stringify(consentCheck, null, 2)}`,
      maxTokens: 2000,
    });

    if (!dryRun) {
      const path = await writeOutput(`compliance/compliance-${todayISO()}.json`, {
        date: todayISO(),
        sitePages,
        emailCheck,
        consentCheck,
        checklist,
      });
      logger.log(`Compliance report → ${path}`);

      const hasHigh = (checklist.issues || []).some((i) => i.severity === 'high');
      if (hasHigh && process.env.TREVOR_EMAIL) {
        await sendEmail({
          to: process.env.TREVOR_EMAIL,
          subject: `[ALERT] Compliance issues detected — ${todayISO()}`,
          html: `<div style="font-family:system-ui,sans-serif">
            <h2 style="color:#c00">Compliance issues detected</h2>
            <pre style="background:#f7f7f7;padding:12px">${escapeHtml(JSON.stringify(checklist, null, 2))}</pre>
          </div>`,
        });
        logger.increment('emails_sent');
      }
    } else {
      console.log('[DRY-RUN]', JSON.stringify(checklist, null, 2));
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '25-compliance.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
