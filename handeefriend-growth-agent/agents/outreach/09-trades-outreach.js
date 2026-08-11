import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, nowISO } from '../../lib/utils.js';

const AGENT_ID = 9;
const AGENT_NAME = 'trades-outreach';
const BATCH_SIZE = 40;

const SYSTEM_PROMPT = `You are Faydra Aldridge writing to a BC contractor/trades owner about HandeeFriend.

Pitch: HandeeFriend pre-scopes homeowner repair needs. By the time a homeowner reaches a contractor, they already have an AI repair estimate and know what they need done. The contractor saves 30 min per quote and gets warm, qualified leads.

Tone: Direct, practical, respectful of their time. No fluff. Reference Canadian/BC context.

2-email sequence. Email 1: short pitch. Email 2: follow-up.

Return JSON: { "email_1": {"subject": "...", "html": "..."}, "email_2": {"subject": "...", "html": "..."} }

Inline-styled HTML, max 600px wide. Include unsubscribe + Faydra signature footer.`;

async function fetchTradesFromApollo({ limit = BATCH_SIZE }) {
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
        person_titles: ['general contractor', 'plumber', 'electrician', 'home builder', 'renovation contractor'],
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
      city: p.city || '',
      brokerage: p.organization?.name || '',
      title: p.title,
    }));
  } catch {
    return [];
  }
}

async function fetchTradesAgentXref() {
  const url = process.env.TRADESAGENT_NOCODB_URL;
  if (!url) return new Set();
  try {
    const res = await fetch(url);
    if (!res.ok) return new Set();
    const data = await res.json();
    const list = data.list || data.records || data;
    return new Set((Array.isArray(list) ? list : []).map((r) => (r.email || '').toLowerCase()));
  } catch {
    return new Set();
  }
}

async function alreadyInPipeline(email) {
  const found = await nocodb.findOne('outreach_pipeline', `(email,eq,${email})`);
  return !!found;
}

async function generateSequence(c) {
  const userContent = `Recipient: ${c.contact_name} (${c.title || 'trades'})
City: ${c.city}
Company: ${c.brokerage}

Subjects (use exactly):
- Email 1: "${c.first_name} — pre-qualified homeowner leads"
- Email 2: "Quick note, ${c.first_name}"

Write the 2-email sequence.`;
  return callClaudeJSON({ systemPrompt: SYSTEM_PROMPT, userContent, maxTokens: 2500 });
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const [contacts, tradesAgentSet] = await Promise.all([
      fetchTradesFromApollo({ limit: BATCH_SIZE }),
      fetchTradesAgentXref(),
    ]);
    logger.log(`Apollo: ${contacts.length}, TradesAgent xref: ${tradesAgentSet.size}`);

    for (const c of contacts) {
      if (!c.email) continue;
      logger.increment('records_processed');

      if (await alreadyInPipeline(c.email)) continue;

      // Notes on TradesAgent xref overlap (kept in reply_content for review)
      const overlap = tradesAgentSet.has(c.email.toLowerCase());

      try {
        const seq = await generateSequence(c);
        const row = {
          contact_name: c.contact_name,
          email: c.email,
          type: 'trades',
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
          reply_content: overlap ? '[xref:tradesagent_match]' : null,
        };

        if (!dryRun) {
          await nocodb.create('outreach_pipeline', row);
          logger.increment('records_created');
          await sendEmail({ to: c.email, subject: seq.email_1.subject, html: seq.email_1.html });
          logger.increment('emails_sent');
        } else {
          logger.log(`  [DRY-RUN] would queue ${c.email}${overlap ? ' (xref match)' : ''}`);
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

if (isCliEntry(import.meta, '09-trades-outreach.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
