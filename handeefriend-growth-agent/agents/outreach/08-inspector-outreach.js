import 'dotenv/config';
import { readFile } from 'node:fs/promises';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, nowISO } from '../../lib/utils.js';

const AGENT_ID = 8;
const AGENT_NAME = 'inspector-outreach';
const BATCH_SIZE = 25;

const SYSTEM_PROMPT = `You are Faydra Aldridge, founder of HandeeFriend. Write to a BC-licensed home inspector.

HandeeFriend's Forensic Condition Report is a complement to (not replacement for) a professional inspection. After inspectors do their job, homeowners often feel anxious and unsure what to prioritize.

HandeeFriend gives them:
- An AI that tracks each issue from the inspector's report
- Repair cost estimates for each flagged item
- A priority order (safety first, then value-impact)

Inspector benefit: Your clients feel supported after the inspection. You become the inspector who gave them a next step. Referral commission: 10% on paid conversions.

2-email sequence. Email 1: introduce the idea, include demo link. Email 2: follow-up with a "30 seconds to set it up" CTA.

Return JSON: { "email_1": {"subject": "...", "html": "..."}, "email_2": {"subject": "...", "html": "..."} }

HTML must be inline-styled, mobile-readable, max 600px wide. Include unsubscribe + Faydra signature footer.`;

async function fetchInspectorsFromCSV() {
  try {
    const text = await readFile('input/inspectors.csv', 'utf8');
    const lines = text.trim().split(/\r?\n/);
    const headers = lines.shift().split(',').map((h) => h.trim().toLowerCase());
    return lines.map((line) => {
      const values = line.split(',');
      const row = Object.fromEntries(headers.map((h, i) => [h, (values[i] || '').trim()]));
      return {
        contact_name: row.name || row.full_name || row.contact_name || '',
        first_name: (row.name || row.full_name || '').split(' ')[0] || '',
        email: row.email,
        city: row.city || 'BC',
        brokerage: row.company || row.business || '',
        apollo_id: row.id || '',
      };
    });
  } catch {
    return [];
  }
}

async function fetchInspectorsFromApollo({ limit = BATCH_SIZE }) {
  if (!process.env.APOLLO_API_KEY) return [];
  try {
    const res = await fetch('https://api.apollo.io/v1/mixed_people/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Api-Key': process.env.APOLLO_API_KEY,
      },
      body: JSON.stringify({
        page: 1,
        per_page: limit,
        person_titles: ['home inspector', 'building inspector', 'property inspector'],
        person_locations: ['British Columbia, Canada'],
        contact_email_status: ['verified'],
      }),
    });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.people || data.contacts || []).map((p) => ({
      apollo_id: p.id,
      contact_name: p.name || `${p.first_name || ''} ${p.last_name || ''}`.trim(),
      first_name: p.first_name || (p.name || '').split(' ')[0],
      email: p.email,
      city: p.city || (p.locations || [])[0] || '',
      brokerage: p.organization?.name || '',
    }));
  } catch {
    return [];
  }
}

async function alreadyInPipeline(email) {
  const found = await nocodb.findOne('outreach_pipeline', `(email,eq,${email})`);
  return !!found;
}

async function generateSequence(contact) {
  const userContent = `Recipient: ${contact.contact_name}, ${contact.city}, ${contact.brokerage || 'independent inspector'}
First name: ${contact.first_name}

Subjects to use exactly:
- Email 1: "${contact.first_name} — what your clients do *after* the inspection"
- Email 2: "30 seconds to set it up, ${contact.first_name}"

Write the 2-email sequence.`;

  return callClaudeJSON({
    systemPrompt: SYSTEM_PROMPT,
    userContent,
    maxTokens: 2500,
  });
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const csvContacts = await fetchInspectorsFromCSV();
    const apolloContacts = csvContacts.length ? [] : await fetchInspectorsFromApollo({ limit: BATCH_SIZE });
    const contacts = (csvContacts.length ? csvContacts : apolloContacts).slice(0, BATCH_SIZE);
    logger.log(
      `Sourced ${contacts.length} inspectors (csv=${csvContacts.length}, apollo=${apolloContacts.length})`,
    );

    for (const c of contacts) {
      if (!c.email) continue;
      logger.increment('records_processed');

      if (await alreadyInPipeline(c.email)) {
        logger.log(`  skip ${c.email} — already in pipeline`);
        continue;
      }

      try {
        const seq = await generateSequence(c);
        const row = {
          contact_name: c.contact_name,
          email: c.email,
          type: 'inspector',
          city: c.city,
          brokerage: c.brokerage,
          stage: 'email1_sent',
          email_1_sent_date: nowISO(),
          email_1_subject: seq.email_1.subject,
          email_1_html: seq.email_1.html,
          email_2_subject: seq.email_2.subject,
          email_2_html: seq.email_2.html,
          apollo_id: c.apollo_id,
          reply_received: false,
        };

        if (!dryRun) {
          await nocodb.create('outreach_pipeline', row);
          logger.increment('records_created');
          await sendEmail({
            to: c.email,
            subject: seq.email_1.subject,
            html: seq.email_1.html,
          });
          logger.increment('emails_sent');
        } else {
          logger.log(`  [DRY-RUN] would queue ${c.email}`);
        }
      } catch (e) {
        logger.error(`Failed for ${c.email}: ${e.message}`);
      }
    }

    await logger.finish('success');
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '08-inspector-outreach.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
