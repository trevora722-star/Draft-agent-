import 'dotenv/config';
import { callClaudeJSON } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, nowISO, emailFooter } from '../../lib/utils.js';

const AGENT_ID = 3;
const AGENT_NAME = 'email-sequence';

const SYSTEM_PROMPT = `You are writing marketing emails for HandeeFriend (handeefriend.com), sent from Faydra Aldridge.

Faydra's email voice: warm, direct, like a knowledgeable friend who happens to run an AI platform. First-person. Occasional humour. Short paragraphs. Never pushy.

Every email must have:
- Subject line under 45 characters (aim for 35)
- Preview text under 90 characters
- Opening that names a specific benefit the user has already experienced or could experience
- One clear CTA button (label and URL)
- Unsubscribe footer: "You're receiving this because you signed up at handeefriend.com. Unsubscribe anytime: {{unsubscribe_url}}"
- Sender footer: "HandeeFriend · West Kelowna, BC · faydra@handeefriend.com"

Merge tags available: {{first_name}}, {{plan_tier}}, {{reports_generated}}, {{renders_saved}}, {{referral_link}}, {{promo_code}}, {{unsubscribe_url}}

Return as JSON: { subject, preview_text, html_body }
HTML should be clean, inline-styled, mobile-readable. Max 600px wide. No external CSS.`;

const SEQUENCES = {
  welcome: {
    description: '5-email welcome sequence for new free signups',
    emails: [
      { day: 0, brief: 'Day 0 — immediate welcome. Thank them, give one specific quick-win action they can do right now (run their first Forensic Condition Report). Friendly, not corporate.' },
      { day: 2, brief: 'Day 2 — the Design Studio Render. Show them what visual transformation looks like with a short story of a real BC homeowner. CTA: try the render tool.' },
      { day: 5, brief: 'Day 5 — repair estimate value. Share that contractors mark up by X% and how the AI gives an unbiased estimate. CTA: get a repair estimate.' },
      { day: 10, brief: 'Day 10 — strategic portfolio. Explain how to track the home over time. Casual, like a friend giving advice.' },
      { day: 14, brief: 'Day 14 — light upgrade nudge. Mention plan tiers without pressure. Offer a simple way to ask questions. Sign-off: warm.' },
    ],
  },
  upgrade_nudge: {
    description: 'Triggered when free user hits 2 reports — nudge toward Starter/Pro',
    emails: [
      { day: 0, brief: 'You hit 2 reports — congrats! Highlight what they unlock at Starter ($X/mo). Specific ROI numbers. CTA: upgrade.' },
      { day: 3, brief: 'Soft follow-up. Mention a specific feature they likely need based on usage (Design Studio renders, multi-property tracking). CTA: upgrade.' },
      { day: 7, brief: 'Last nudge. Offer 20% off code. Hard expiry. CTA: claim code.' },
    ],
  },
  realtor_onboard: {
    description: 'Welcome a new realtor partner',
    emails: [
      { day: 0, brief: 'Welcome to the Partner Program. Their unique referral link is {{referral_link}}. Quick-start: send to one client today. Commission structure: 10%.' },
      { day: 2, brief: 'Quick-start guide. 3 use cases for realtors: pre-listing condition report, buyer disclosure complement, post-sale gift. Link to partner portal.' },
    ],
  },
  inspector_onboard: {
    description: 'Welcome a new inspector partner',
    emails: [
      { day: 0, brief: 'Welcome to the Partner Program. The Forensic Condition Report complements your inspection. Their referral link is {{referral_link}}. Commission: 10%.' },
      { day: 2, brief: 'Quick-start: hand the link to clients after every inspection. Show how it helps them act on your findings, not replace them. Link to portal.' },
    ],
  },
  reengagement: {
    description: 'Re-engage signup ghosts and one-and-dones',
    emails: [
      { day: 0, brief: 'Light, curious tone. "I noticed you signed up but haven\'t taken your first report yet — anything I can help with?" CTA: try first report.' },
      { day: 4, brief: 'Show one specific thing the platform does that they probably don\'t know about. Concrete example. CTA: see it in action.' },
      { day: 10, brief: 'Final attempt. Offer to chat directly via reply. No pressure. Genuine.' },
    ],
  },
  win_back: {
    description: 'Win-back for cancelled subscribers 60+ days after cancel',
    emails: [
      { day: 0, brief: 'No guilt. "We launched X since you left." Specific new feature. Soft offer: 50% off first month back if they want to try it again.' },
      { day: 7, brief: 'Final win-back. Tell them their old data is still here for them. Reactivate link. Genuine, no pressure.' },
    ],
  },
  monthly_digest: {
    description: 'HomeOwner Insights — sent 1st of each month',
    emails: [
      { day: 0, brief: 'Monthly newsletter. 3 sections: This Month\'s Most-Quoted Repair (with average BC cost), Design Trend We Loved, Question We Got Most This Month (answered). Friendly, useful, not promotional.' },
    ],
  },
  referral_confirmation: {
    description: 'Notify a user that someone signed up via their referral link',
    emails: [
      { day: 0, brief: 'Quick celebratory note: "{{first_name}} — someone joined HandeeFriend through your link. You\'re {{X}} away from your free month credit." Show the leaderboard if relevant.' },
    ],
  },
  case_study_permission: {
    description: 'Outreach to a power user asking permission to feature them as a case study',
    emails: [
      { day: 0, brief: 'Personal note from Faydra to a real user. Acknowledge their specific usage. Ask permission to write a short anonymous-or-named case study. No obligation. Reply to consent.' },
    ],
  },
};

async function generateEmail({ sequenceName, emailNumber, dayLabel, brief }) {
  const userContent = `Sequence: ${sequenceName}
Email number: ${emailNumber} of the sequence
Day after trigger: D${dayLabel}
Brief: ${brief}

Write the full email. Include the unsubscribe footer and sender footer. Use this HTML scaffold pattern but customize content:

${emailFooter()}

Return JSON only: { "subject": "...", "preview_text": "...", "html_body": "<full HTML email here>" }`;

  return callClaudeJSON({
    systemPrompt: SYSTEM_PROMPT,
    userContent,
    maxTokens: 3000,
  });
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);
  const sequenceName =
    options.sequence ||
    process.argv.find((a) => a.startsWith('--sequence='))?.split('=')[1] ||
    'welcome';

  try {
    const seq = SEQUENCES[sequenceName];
    if (!seq) throw new Error(`Unknown sequence: ${sequenceName}. Known: ${Object.keys(SEQUENCES).join(', ')}`);

    logger.log(`Generating sequence "${sequenceName}" — ${seq.emails.length} emails`);

    for (let i = 0; i < seq.emails.length; i++) {
      const e = seq.emails[i];
      logger.increment('records_processed');
      const emailNumber = i + 1;

      logger.log(`  → email ${emailNumber} (D${e.day}) generating…`);
      const result = await generateEmail({
        sequenceName,
        emailNumber,
        dayLabel: e.day,
        brief: e.brief,
      });

      const row = {
        sequence_name: sequenceName,
        email_number: emailNumber,
        subject: result.subject,
        preview_text: result.preview_text,
        html_body: result.html_body,
        last_updated: nowISO(),
        agent_id: AGENT_ID,
      };

      if (!dryRun) {
        // Upsert: check if existing row exists for (sequence_name, email_number)
        const existing = await nocodb.findOne(
          'email_templates',
          `(sequence_name,eq,${sequenceName})~and(email_number,eq,${emailNumber})`,
        );
        if (existing) {
          await nocodb.update('email_templates', existing.id, row);
          logger.log(`    updated existing template (id=${existing.id})`);
        } else {
          const created = await nocodb.create('email_templates', row);
          logger.log(`    created template (id=${created.id || 'unknown'})`);
          logger.increment('records_created');
        }
      } else {
        logger.log(`    [DRY-RUN] would save subject="${result.subject}"`);
      }
    }

    await logger.finish('success');
    return { sequence: sequenceName, count: seq.emails.length };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '03-email-sequence.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
