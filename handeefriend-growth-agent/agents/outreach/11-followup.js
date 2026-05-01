import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, nowISO, daysAgoISO } from '../../lib/utils.js';

const AGENT_ID = 11;
const AGENT_NAME = 'followup';

const SYSTEM_PROMPT = `You are Faydra Aldridge sending a follow-up email to a partner-program prospect (realtor, inspector, or trades). Reference the prior emails, keep it brief and human, never pushy. Return JSON: { "subject": "...", "html": "..." }`;

async function generateRetouch90(row) {
  const userContent = `Recipient: ${row.contact_name} (${row.type}), ${row.city}, ${row.brokerage || ''}
This contact went cold ~90 days ago. Send a fresh, low-pressure re-touch. Subject: short. Mention something concrete that's new with HandeeFriend (you can reference Forensic Condition Report improvements or the new Design Studio Render).`;
  return callClaudeJSON({ systemPrompt: SYSTEM_PROMPT, userContent, maxTokens: 1200 });
}

async function processStage({ logger, dryRun, currentStage, nextStage, sendField, subjectField, htmlField, dateField }) {
  const cutoff = daysAgoISO(5);
  const where = `(stage,eq,${currentStage})~and(${dateField},le,${cutoff})~and(reply_received,eq,false)`;
  const res = await nocodb.list('outreach_pipeline', { where, limit: 100 });
  const rows = res.list || res.records || [];
  logger.log(`stage=${currentStage} → ${rows.length} due for follow-up`);

  for (const row of rows) {
    logger.increment('records_processed');
    const subject = row[subjectField];
    const html = row[htmlField];
    if (!subject || !html) {
      logger.error(`missing ${subjectField}/${htmlField} for row ${row.id}`);
      continue;
    }
    try {
      if (!dryRun) {
        await sendEmail({ to: row.email, subject, html });
        logger.increment('emails_sent');
        await nocodb.update('outreach_pipeline', row.id, {
          stage: nextStage,
          [sendField]: nowISO(),
        });
      } else {
        logger.log(`  [DRY-RUN] would send "${subject}" to ${row.email}`);
      }
    } catch (e) {
      logger.error(`send failed for ${row.email}: ${e.message}`);
    }
  }
}

async function processCold({ logger, dryRun }) {
  const cutoff = daysAgoISO(90);
  const where = `(stage,eq,cold)~and(email_3_sent_date,le,${cutoff})`;
  const res = await nocodb.list('outreach_pipeline', { where, limit: 50 });
  const rows = res.list || res.records || [];
  logger.log(`cold contacts due for 90-day re-touch: ${rows.length}`);

  for (const row of rows) {
    logger.increment('records_processed');
    try {
      const email = await generateRetouch90(row);
      if (!dryRun) {
        await sendEmail({ to: row.email, subject: email.subject, html: email.html });
        logger.increment('emails_sent');
        await nocodb.update('outreach_pipeline', row.id, {
          stage: 'retouch_90',
        });
      } else {
        logger.log(`  [DRY-RUN] would send 90-day retouch to ${row.email}`);
      }
    } catch (e) {
      logger.error(`retouch failed for ${row.email}: ${e.message}`);
    }
  }
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    // Email 1 → Email 2 (5+ days, no reply)
    await processStage({
      logger,
      dryRun,
      currentStage: 'email1_sent',
      nextStage: 'email2_sent',
      sendField: 'email_2_sent_date',
      subjectField: 'email_2_subject',
      htmlField: 'email_2_html',
      dateField: 'email_1_sent_date',
    });

    // Email 2 → Email 3
    await processStage({
      logger,
      dryRun,
      currentStage: 'email2_sent',
      nextStage: 'email3_sent',
      sendField: 'email_3_sent_date',
      subjectField: 'email_3_subject',
      htmlField: 'email_3_html',
      dateField: 'email_2_sent_date',
    });

    // Email 3 → cold (no email sent, just status update)
    {
      const cutoff = daysAgoISO(5);
      const where = `(stage,eq,email3_sent)~and(email_3_sent_date,le,${cutoff})~and(reply_received,eq,false)`;
      const res = await nocodb.list('outreach_pipeline', { where, limit: 100 });
      const rows = res.list || res.records || [];
      logger.log(`email3 → cold: ${rows.length}`);
      for (const row of rows) {
        logger.increment('records_processed');
        if (!dryRun) {
          await nocodb.update('outreach_pipeline', row.id, { stage: 'cold' });
        }
      }
    }

    await processCold({ logger, dryRun });

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '11-followup.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
