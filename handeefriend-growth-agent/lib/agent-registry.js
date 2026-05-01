// Single source of truth for agent metadata. Used by the runner
// to expose /agents and by the UI to render the control center.

export const AGENTS = [
  // Content
  {
    id: 1, code: '01', name: 'TikTok Script', dept: 'content',
    file: 'content/01-tiktok-script.js',
    cadence: 'Weekly (Mon)',
    description: '5 TikTok script variants per week. Pulls top performers + target pillar from content_calendar, generates hook/body/CTA/shot-notes, saves drafts, emails Faydra for review.',
    inputs: ['content_calendar (pending tiktok rows)', 'top performers (performance_score)'],
    outputs: ['content_calendar (5 draft rows)', 'email to FAYDRA_EMAIL'],
  },
  {
    id: 2, code: '02', name: 'Blog Content', dept: 'content',
    file: 'content/02-blog-content.js',
    cadence: 'Per-brief (auto from Agent 14)',
    description: 'Writes a 900-word SEO blog post from a content brief. H1 + 5–7 H2s + FAQ schema + meta description + 2 internal links.',
    inputs: ['content_calendar row with brief_json (script_status=briefed)'],
    outputs: ['content_calendar.script_content (HTML)', 'output/blog/{slug}.html'],
  },
  {
    id: 3, code: '03', name: 'Email Sequence', dept: 'content',
    file: 'content/03-email-sequence.js',
    cadence: 'On-demand',
    description: '8 named sequences: welcome, upgrade_nudge, realtor_onboard, inspector_onboard, reengagement, win_back, monthly_digest, referral_confirmation, case_study_permission.',
    options: [{ name: 'sequence', default: 'welcome', enum: ['welcome','upgrade_nudge','realtor_onboard','inspector_onboard','reengagement','win_back','monthly_digest','referral_confirmation','case_study_permission'] }],
    outputs: ['email_templates (upserted)'],
  },
  {
    id: 4, code: '04', name: 'YouTube Repurpose', dept: 'content',
    file: 'content/04-youtube-repurpose.js',
    cadence: 'On approve (Make.com)',
    description: 'Turns an approved TikTok script into YouTube Shorts metadata: title, description, 8 hashtags, pinned comment, thumbnail text.',
    options: [{ name: 'content_calendar_id', required: true }],
  },
  {
    id: 5, code: '05', name: 'Ad Copy', dept: 'content',
    file: 'content/05-ad-copy.js',
    cadence: 'Monthly (M4+)',
    description: 'Facebook/Instagram ad creative deck. 3 campaigns × 3 headlines × 3 primary text variants. before_after, lead_magnet, testimonial.',
  },
  {
    id: 6, code: '06', name: 'Case Study', dept: 'content',
    file: 'content/06-case-study.js',
    cadence: 'Monthly (1st)',
    description: 'Drafts case studies for top 3 power users (lead_score≥70, reports_generated≥3). Queues drafts for human approval — never auto-publishes or auto-emails the user.',
    safety: 'Drafts only — review queue required.',
  },

  // Outreach
  {
    id: 7, code: '07', name: 'Realtor Outreach', dept: 'outreach',
    file: 'outreach/07-realtor-outreach.js',
    cadence: 'Weekly',
    description: 'Pulls 50 BC realtors from Apollo, generates personalized 3-email sequence, sends Email 1, schedules 2 + 3 via outreach_pipeline timestamps.',
    deps: ['APOLLO_API_KEY'],
  },
  {
    id: 8, code: '08', name: 'Inspector Outreach', dept: 'outreach',
    file: 'outreach/08-inspector-outreach.js',
    cadence: 'Bi-weekly',
    description: '25 BC inspectors per run from input/inspectors.csv (or Apollo). 2-email sequence centered on the Forensic Condition Report.',
    deps: ['input/inspectors.csv or APOLLO_API_KEY'],
  },
  {
    id: 9, code: '09', name: 'Trades Outreach', dept: 'outreach',
    file: 'outreach/09-trades-outreach.js',
    cadence: 'Monthly',
    description: '40 BC contractors per run. 2-email sequence pitching pre-qualified leads. Cross-references TRADESAGENT_NOCODB_URL when set.',
  },
  {
    id: 10, code: '10', name: 'Community Monitor', dept: 'outreach',
    file: 'outreach/10-community-monitor.js',
    cadence: 'Daily 08:00',
    description: 'Searches Reddit r/canadianhomeowners, r/BritishColumbia, r/FirstTimeHomeBuyer, r/renovations for relevant posts. Drafts replies (max 10/day) and queues them for human approval.',
    safety: 'Drafts only — review queue required.',
    deps: ['REDDIT_*'],
  },
  {
    id: 11, code: '11', name: 'Follow-Up', dept: 'outreach',
    file: 'outreach/11-followup.js',
    cadence: 'Daily',
    description: 'Walks outreach_pipeline through email1 → email2 → email3 → cold → retouch_90 based on dates and reply_received.',
  },

  // SEO
  {
    id: 12, code: '12', name: 'Keyword Research', dept: 'seo',
    file: 'seo/12-keyword-research.js',
    cadence: 'Weekly Mon 06:00',
    description: 'Generates 10 new keyword ideas + 3 quick-wins. Pulls GSC data when configured. Upserts to keyword_tracker, flags top 3 as p1.',
  },
  {
    id: 13, code: '13', name: 'On-Page Audit', dept: 'seo',
    file: 'seo/13-onpage-audit.js',
    cadence: 'Monthly (1st)',
    description: 'Audits 6 key handeefriend.com pages: title, meta, headings, internal links, image alts. Outputs prioritized fix list.',
  },
  {
    id: 14, code: '14', name: 'Content Brief', dept: 'seo',
    file: 'seo/14-content-brief.js',
    cadence: 'Weekly',
    description: 'Generates blog content brief from top P1 keyword. Auto-triggers Agent 02 to write the post.',
  },
  {
    id: 15, code: '15', name: 'Backlink Prospect', dept: 'seo',
    file: 'seo/15-backlink-prospect.js',
    cadence: 'Monthly',
    description: '20 BC/Canadian site prospects + 5 ready-to-send drafts emailed to Trevor.',
  },

  // CRM
  {
    id: 16, code: '16', name: 'Lead Scoring', dept: 'crm',
    file: 'crm/16-lead-scoring.js',
    cadence: 'Daily 07:00',
    description: 'Scores all free users 0–100 and assigns segment (cold/warm/hot/upgrade_ready). Triggers Agent 17 when a user enters upgrade_ready.',
  },
  {
    id: 17, code: '17', name: 'Upgrade Trigger', dept: 'crm',
    file: 'crm/17-upgrade-trigger.js',
    cadence: 'Event (from Agent 16)',
    description: 'Generates a Stripe promo code (20% off, 72h, single-use) + writes a hyper-personalized upgrade email + sets in-app banner.',
    options: [{ name: 'user_id', required: true }],
  },
  {
    id: 18, code: '18', name: 'Churn Detection', dept: 'crm',
    file: 'crm/18-churn-detection.js',
    cadence: 'Daily 09:00',
    description: 'Scans paid users for at-risk signals (inactive 14d+, cancel_at_period_end). Sends save-sequence emails. Alerts Trevor on $200+/yr at-risk.',
  },
  {
    id: 19, code: '19', name: 'Re-engagement', dept: 'crm',
    file: 'crm/19-reengagement.js',
    cadence: 'Weekly Sun',
    description: 'Three segments: signup_ghost, one_and_done, cancelled_60. Generates segment-specific re-engagement emails.',
  },

  // Analytics
  {
    id: 20, code: '20', name: 'Weekly Metrics', dept: 'analytics',
    file: 'analytics/20-weekly-metrics.js',
    cadence: 'Mon 06:00',
    description: 'Aggregates users, outreach, referrals, content, Stripe MRR + agent_logs. Claude analyzes for 3 wins + 3 concerns + 1 recommended action.',
  },
  {
    id: 21, code: '21', name: 'Content Performance', dept: 'analytics',
    file: 'analytics/21-content-performance.js',
    cadence: 'Wed weekly',
    description: 'Ranks last 14d content by trial_starts_attributed. Extracts the pattern from top performers and writes insights back to content_calendar.',
  },
  {
    id: 22, code: '22', name: 'Funnel Analysis', dept: 'analytics',
    file: 'analytics/22-funnel-analysis.js',
    cadence: 'Monthly (1st)',
    description: 'Identifies the single biggest drop-off point in the funnel and proposes one testable fix. Uses extended thinking.',
  },

  // Retention
  {
    id: 23, code: '23', name: 'Referral Program', dept: 'retention',
    file: 'retention/23-referral-program.js',
    cadence: 'Event + Monthly leaderboard',
    description: 'Logs referral_events. Applies free-month Stripe credit at 3 referrals. Builds a leaderboard once a month.',
  },
  {
    id: 24, code: '24', name: 'Partner Dashboard', dept: 'retention',
    file: 'retention/24-partner-dashboard.js',
    cadence: 'Monthly (1st)',
    description: 'Calculates monthly partner commissions, applies Stripe credit, sends each partner a personalized report. Quarterly spotlight on top partner.',
  },
  {
    id: 25, code: '25', name: 'Compliance', dept: 'retention',
    file: 'retention/25-compliance.js',
    cadence: 'Monthly (1st)',
    description: 'Checks CASL (unsubscribe + footer in every email), ASC (affiliate disclosure on commission links), BC Consumer Protection Act. Alerts Trevor on high-severity issues.',
  },

  // Command
  {
    id: 26, code: '26', name: 'Growth Commander', dept: 'command',
    file: 'command/26-growth-commander.js',
    cadence: 'Sun 21:00',
    description: 'Synthesizes all 25 other agents into a weekly executive brief: subscriber count vs target, MRR, 3 wins, 3 blockers, 5 priority actions, agent health.',
  },
];

export const DEPTS = {
  content: { label: 'Content', color: '#f97316' },
  outreach: { label: 'Outreach', color: '#3b82f6' },
  seo: { label: 'SEO', color: '#10b981' },
  crm: { label: 'CRM', color: '#8b5cf6' },
  analytics: { label: 'Analytics', color: '#eab308' },
  retention: { label: 'Retention', color: '#ec4899' },
  command: { label: 'Command', color: '#dc2626' },
};

export function findAgent(idOrCode) {
  const s = String(idOrCode);
  return AGENTS.find((a) => String(a.id) === s || a.code === s.padStart(2, '0'));
}
