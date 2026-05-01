import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, nowISO } from '../../lib/utils.js';

const AGENT_ID = 7;
const AGENT_NAME = 'realtor-outreach';
const BATCH_SIZE = 50;

const SYSTEM_PROMPT = `You are Faydra Aldridge, founder of HandeeFriend (handeefriend.com). Write a warm, professional cold email to a BC realtor.

Value proposition: Realtors who join our Partner Program get:
- A co-branded referral link to give clients before listing
- Clients get a free AI Forensic Condition Report (surfaces hidden repair costs pre-listing)
- 10% commission on any client who converts to a paid plan
- Monthly report on your referrals and earnings

Do NOT use: "I hope this finds you well", "synergy", "leverage", "reach out"
DO use: specific reference to their city/market, a concrete number or benefit, a single clear CTA

Email 1: Value pitch — 4 sentences max
Email 2: Social proof reference + softer ask (2–3 sentences)
Email 3: Final attempt — mention the partner link expires, offer to answer questions

Return JSON: { "email_1": {"subject": "...", "html": "..."}, "email_2": {"subject": "...", "html": "..."}, "email_3": {"subject": "...", "html": "..."} }

HTML must be inline-styled, mobile-readable, max 600px wide. Always include unsubscribe + Faydra signature footer.`;

async function fetchRealtors({ limit = BATCH_SIZE }) {
  if (!process.env.APOLLO_API_KEY) {
    console.warn('APOLLO_API_KEY not configured — returning empty contact list');
    return [];
  }
  try {
    const res = await fetch('https://api.apollo.io/v1/mixed_people/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache',
        'X-Api-Key': process.env.APOLLO_API_KEY,
      },
      body: JSON.stringify({
        page: 1,
        per_page: limit,
        person_titles: ['real estate agent', 'realtor', 'real estate broker'],
        person_locations: ['British Columbia, Canada'],
        contact_email_status: ['verified'],
      }),
    });
    if (!res.ok) {
      console.warn(`Apollo API ${res.status}: ${await res.text()}`);
      return [];
    }
    const data = await res.json();
    return (data.people || data.contacts || []).map((p) => ({
      apollo_id: p.id,
      contact_name: p.name || `${p.first_name || ''} ${p.last_name || ''}`.trim(),
      first_name: p.first_name || (p.name || '').split(' ')[0],
      email: p.email,
      city: p.city || (p.locations || [])[0] || '',
      brokerage: p.organization?.name || p.organization_name || '',
    }));
  } catch (e) {
    console.warn(`Apollo fetch failed: ${e.message}`);
    return [];
  }
}

async function alreadyInPipeline(email) {
  const found = await nocodb.findOne('outreach_pipeline', `(email,eq,${email})`);
  return !!found;
}

async function generateSequence(contact) {
  const userContent = `Recipient details:
Name: ${contact.contact_name}
First name: ${contact.first_name}
City: ${contact.city || 'BC'}
Brokerage: ${contact.brokerage || '—'}

Subjects to use exactly:
- Email 1: "${contact.first_name} — free tool for your BC clients"
- Email 2: "Quick follow-up, ${contact.first_name}"
- Email 3: "Last note — partner link below"

Write all three emails personalized to this realtor.`;

  return callClaudeJSON({
    systemPrompt: SYSTEM_PROMPT,
    userContent,
    maxTokens: 3500,
  });
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const contacts = await fetchRealtors({ limit: options.batchSize || BATCH_SIZE });
    logger.log(`Fetched ${contacts.length} realtor contacts from Apollo`);

    let processed = 0;
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
          type: 'realtor',
          city: c.city,
          brokerage: c.brokerage,
          stage: 'email1_sent',
          email_1_sent_date: nowISO(),
          email_1_subject: seq.email_1.subject,
          email_1_html: seq.email_1.html,
          email_2_subject: seq.email_2.subject,
          email_2_html: seq.email_2.html,
          email_3_subject: seq.email_3.subject,
          email_3_html: seq.email_3.html,
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
          logger.log(`  [DRY-RUN] would queue ${c.email} (subject: ${seq.email_1.subject})`);
        }
        processed++;
      } catch (e) {
        logger.error(`Failed for ${c.email}: ${e.message}`);
      }
    }

    await logger.finish('success');
    return { processed };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '07-realtor-outreach.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
