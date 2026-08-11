import 'dotenv/config';

const BASE = process.env.NOCODB_BASE_URL;
const KEY = process.env.NOCODB_API_KEY;
const BASE_ID = process.env.NOCODB_BASE_ID;

if (!BASE || !KEY || !BASE_ID) {
  console.error('Missing NOCODB_BASE_URL, NOCODB_API_KEY, or NOCODB_BASE_ID in .env');
  process.exit(1);
}

async function ncReq(path, method = 'GET', body = null) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      'xc-token': KEY,
      'Content-Type': 'application/json',
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`NocoDB ${method} ${path}: ${res.status} ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// NocoDB column type aliases (uidt — UI Data Type)
const T = {
  id: { uidt: 'ID' },
  num: { uidt: 'Number' },
  text: { uidt: 'SingleLineText' },
  long: { uidt: 'LongText' },
  email: { uidt: 'Email' },
  url: { uidt: 'URL' },
  bool: { uidt: 'Checkbox' },
  date: { uidt: 'Date' },
  datetime: { uidt: 'DateTime' },
  currency: { uidt: 'Currency' },
  select: (options) => ({
    uidt: 'SingleSelect',
    dtxp: options.map((o) => `'${o}'`).join(','),
    colOptions: { options: options.map((title) => ({ title })) },
  }),
};

const TABLES = {
  users: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'email', title: 'email', ...T.email },
    { column_name: 'name', title: 'name', ...T.text },
    { column_name: 'plan_tier', title: 'plan_tier', ...T.select(['free', 'starter', 'pro', 'annual']) },
    { column_name: 'stripe_customer_id', title: 'stripe_customer_id', ...T.text },
    { column_name: 'stripe_subscription_id', title: 'stripe_subscription_id', ...T.text },
    { column_name: 'signup_date', title: 'signup_date', ...T.datetime },
    { column_name: 'referral_source', title: 'referral_source', ...T.text },
    { column_name: 'referral_link', title: 'referral_link', ...T.text },
    { column_name: 'referral_count', title: 'referral_count', ...T.num },
    { column_name: 'reports_generated', title: 'reports_generated', ...T.num },
    { column_name: 'renders_saved', title: 'renders_saved', ...T.num },
    { column_name: 'lead_score', title: 'lead_score', ...T.num },
    { column_name: 'segment', title: 'segment', ...T.select(['cold', 'warm', 'hot', 'upgrade_ready']) },
    { column_name: 'consent_status', title: 'consent_status', ...T.bool },
    { column_name: 'consent_date', title: 'consent_date', ...T.datetime },
    { column_name: 'last_active', title: 'last_active', ...T.datetime },
    { column_name: 'partner_type', title: 'partner_type', ...T.select(['none', 'realtor', 'inspector', 'trades']) },
    { column_name: 'partner_commission_total', title: 'partner_commission_total', ...T.currency },
    { column_name: 'active_promo_code', title: 'active_promo_code', ...T.text },
    { column_name: 'promo_expires', title: 'promo_expires', ...T.datetime },
    { column_name: 'upgrade_banner_html', title: 'upgrade_banner_html', ...T.long },
    { column_name: 'cancelled_date', title: 'cancelled_date', ...T.datetime },
  ],

  content_calendar: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'week_number', title: 'week_number', ...T.num },
    { column_name: 'publish_date', title: 'publish_date', ...T.date },
    { column_name: 'content_type', title: 'content_type', ...T.select(['tiktok', 'blog', 'email', 'youtube', 'ad', 'insights']) },
    { column_name: 'pillar', title: 'pillar', ...T.select(['before_after', 'shock_estimate', 'myth_bust', 'live_report']) },
    { column_name: 'keyword', title: 'keyword', ...T.text },
    { column_name: 'script_status', title: 'script_status', ...T.select(['pending', 'briefed', 'draft', 'approved', 'published', 'pending_permission', 'rejected']) },
    { column_name: 'approved_by', title: 'approved_by', ...T.text },
    { column_name: 'platform', title: 'platform', ...T.select(['tiktok', 'instagram', 'youtube', 'blog', 'email']) },
    { column_name: 'script_content', title: 'script_content', ...T.long },
    { column_name: 'brief_json', title: 'brief_json', ...T.long },
    { column_name: 'performance_score', title: 'performance_score', ...T.num },
    { column_name: 'trial_starts_attributed', title: 'trial_starts_attributed', ...T.num },
    { column_name: 'utm_tag', title: 'utm_tag', ...T.text },
    { column_name: 'agent_id', title: 'agent_id', ...T.num },
  ],

  outreach_pipeline: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'contact_name', title: 'contact_name', ...T.text },
    { column_name: 'email', title: 'email', ...T.email },
    { column_name: 'type', title: 'type', ...T.select(['realtor', 'inspector', 'trades']) },
    { column_name: 'city', title: 'city', ...T.text },
    { column_name: 'brokerage', title: 'brokerage', ...T.text },
    { column_name: 'stage', title: 'stage', ...T.select(['new', 'email1_sent', 'email2_sent', 'email3_sent', 'replied', 'partner_signed', 'cold', 'retouch_90']) },
    { column_name: 'email_1_sent_date', title: 'email_1_sent_date', ...T.datetime },
    { column_name: 'email_2_sent_date', title: 'email_2_sent_date', ...T.datetime },
    { column_name: 'email_3_sent_date', title: 'email_3_sent_date', ...T.datetime },
    { column_name: 'reply_received', title: 'reply_received', ...T.bool },
    { column_name: 'reply_content', title: 'reply_content', ...T.long },
    { column_name: 'partner_signed_date', title: 'partner_signed_date', ...T.datetime },
    { column_name: 'referrals_generated', title: 'referrals_generated', ...T.num },
    { column_name: 'commission_paid_total', title: 'commission_paid_total', ...T.currency },
    { column_name: 'apollo_id', title: 'apollo_id', ...T.text },
    { column_name: 'email_1_subject', title: 'email_1_subject', ...T.text },
    { column_name: 'email_1_html', title: 'email_1_html', ...T.long },
    { column_name: 'email_2_subject', title: 'email_2_subject', ...T.text },
    { column_name: 'email_2_html', title: 'email_2_html', ...T.long },
    { column_name: 'email_3_subject', title: 'email_3_subject', ...T.text },
    { column_name: 'email_3_html', title: 'email_3_html', ...T.long },
  ],

  keyword_tracker: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'keyword', title: 'keyword', ...T.text },
    { column_name: 'monthly_volume', title: 'monthly_volume', ...T.num },
    { column_name: 'kd_estimate', title: 'kd_estimate', ...T.num },
    { column_name: 'current_rank', title: 'current_rank', ...T.num },
    { column_name: 'target_url', title: 'target_url', ...T.url },
    { column_name: 'content_calendar_id', title: 'content_calendar_id', ...T.num },
    { column_name: 'indexed_date', title: 'indexed_date', ...T.date },
    { column_name: 'rank_history', title: 'rank_history', ...T.long },
    { column_name: 'trial_starts', title: 'trial_starts', ...T.num },
    { column_name: 'priority', title: 'priority', ...T.select(['p1', 'p2', 'p3']) },
  ],

  referral_events: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'referrer_user_id', title: 'referrer_user_id', ...T.num },
    { column_name: 'referred_email', title: 'referred_email', ...T.email },
    { column_name: 'referred_user_id', title: 'referred_user_id', ...T.num },
    { column_name: 'event_type', title: 'event_type', ...T.select(['click', 'signup', 'trial_start', 'paid_convert']) },
    { column_name: 'event_date', title: 'event_date', ...T.datetime },
    { column_name: 'utm_campaign', title: 'utm_campaign', ...T.text },
    { column_name: 'stripe_promo_code', title: 'stripe_promo_code', ...T.text },
    { column_name: 'reward_applied', title: 'reward_applied', ...T.bool },
    { column_name: 'reward_applied_date', title: 'reward_applied_date', ...T.datetime },
  ],

  partner_registry: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'outreach_pipeline_id', title: 'outreach_pipeline_id', ...T.num },
    { column_name: 'name', title: 'name', ...T.text },
    { column_name: 'email', title: 'email', ...T.email },
    { column_name: 'type', title: 'type', ...T.select(['realtor', 'inspector', 'trades']) },
    { column_name: 'city', title: 'city', ...T.text },
    { column_name: 'referral_link', title: 'referral_link', ...T.text },
    { column_name: 'total_referrals', title: 'total_referrals', ...T.num },
    { column_name: 'total_conversions', title: 'total_conversions', ...T.num },
    { column_name: 'total_commission_cad', title: 'total_commission_cad', ...T.currency },
    { column_name: 'stripe_credit_applied', title: 'stripe_credit_applied', ...T.currency },
    { column_name: 'status', title: 'status', ...T.select(['active', 'inactive']) },
    { column_name: 'joined_date', title: 'joined_date', ...T.datetime },
  ],

  email_templates: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'sequence_name', title: 'sequence_name', ...T.text },
    { column_name: 'email_number', title: 'email_number', ...T.num },
    { column_name: 'subject', title: 'subject', ...T.text },
    { column_name: 'preview_text', title: 'preview_text', ...T.text },
    { column_name: 'html_body', title: 'html_body', ...T.long },
    { column_name: 'resend_template_id', title: 'resend_template_id', ...T.text },
    { column_name: 'last_updated', title: 'last_updated', ...T.datetime },
    { column_name: 'agent_id', title: 'agent_id', ...T.num },
  ],

  agent_logs: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'agent_id', title: 'agent_id', ...T.num },
    { column_name: 'agent_name', title: 'agent_name', ...T.text },
    { column_name: 'run_date', title: 'run_date', ...T.datetime },
    { column_name: 'status', title: 'status', ...T.select(['success', 'partial', 'failed']) },
    { column_name: 'records_processed', title: 'records_processed', ...T.num },
    { column_name: 'records_created', title: 'records_created', ...T.num },
    { column_name: 'emails_sent', title: 'emails_sent', ...T.num },
    { column_name: 'errors', title: 'errors', ...T.long },
    { column_name: 'dry_run', title: 'dry_run', ...T.bool },
    { column_name: 'duration_ms', title: 'duration_ms', ...T.num },
  ],

  ad_creative: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'campaign_type', title: 'campaign_type', ...T.select(['before_after', 'lead_magnet', 'testimonial', 'feature_spotlight']) },
    { column_name: 'headline_1', title: 'headline_1', ...T.text },
    { column_name: 'headline_2', title: 'headline_2', ...T.text },
    { column_name: 'headline_3', title: 'headline_3', ...T.text },
    { column_name: 'primary_text_1', title: 'primary_text_1', ...T.long },
    { column_name: 'primary_text_2', title: 'primary_text_2', ...T.long },
    { column_name: 'primary_text_3', title: 'primary_text_3', ...T.long },
    { column_name: 'description', title: 'description', ...T.long },
    { column_name: 'audience_tag', title: 'audience_tag', ...T.select(['bc_homeowners', 'realtors', 'diy', 'lookalike']) },
    { column_name: 'status', title: 'status', ...T.select(['draft', 'approved', 'live', 'paused']) },
    { column_name: 'created_month', title: 'created_month', ...T.num },
  ],

  community_queue: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'platform', title: 'platform', ...T.text },
    { column_name: 'post_url', title: 'post_url', ...T.url },
    { column_name: 'post_title', title: 'post_title', ...T.text },
    { column_name: 'post_excerpt', title: 'post_excerpt', ...T.long },
    { column_name: 'draft_reply', title: 'draft_reply', ...T.long },
    { column_name: 'status', title: 'status', ...T.select(['pending', 'approved', 'posted', 'rejected']) },
    { column_name: 'created_date', title: 'created_date', ...T.datetime },
    { column_name: 'reviewed_by', title: 'reviewed_by', ...T.text },
  ],

  backlink_prospects: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'domain', title: 'domain', ...T.text },
    { column_name: 'da_estimate', title: 'da_estimate', ...T.num },
    { column_name: 'pitch_angle', title: 'pitch_angle', ...T.long },
    { column_name: 'contact_email', title: 'contact_email', ...T.email },
    { column_name: 'status', title: 'status', ...T.select(['draft', 'sent', 'replied', 'placed', 'rejected']) },
    { column_name: 'outreach_date', title: 'outreach_date', ...T.datetime },
    { column_name: 'draft_email', title: 'draft_email', ...T.long },
  ],

  weekly_metrics: [
    { column_name: 'id', title: 'id', ...T.id, pk: true },
    { column_name: 'week_date', title: 'week_date', ...T.date },
    { column_name: 'total_subscribers', title: 'total_subscribers', ...T.num },
    { column_name: 'new_subscribers', title: 'new_subscribers', ...T.num },
    { column_name: 'mrr_cad', title: 'mrr_cad', ...T.currency },
    { column_name: 'churn_count', title: 'churn_count', ...T.num },
    { column_name: 'conversion_rate', title: 'conversion_rate', ...T.num },
    { column_name: 'top_referral_source', title: 'top_referral_source', ...T.text },
    { column_name: 'emails_sent', title: 'emails_sent', ...T.num },
    { column_name: 'community_posts', title: 'community_posts', ...T.num },
    { column_name: 'snapshot_json', title: 'snapshot_json', ...T.long },
  ],
};

async function ensureTable(name, columns) {
  // Check if table exists
  const existing = await ncReq(`/api/v2/meta/bases/${BASE_ID}/tables`).catch(() => ({ list: [] }));
  const found = (existing.list || []).find((t) => t.title === name || t.table_name === name);

  if (found) {
    console.log(`✓ Table "${name}" already exists (id=${found.id})`);
    return found;
  }

  console.log(`+ Creating table "${name}"...`);
  const created = await ncReq(`/api/v2/meta/bases/${BASE_ID}/tables`, 'POST', {
    table_name: name,
    title: name,
    columns,
  });
  console.log(`  → created id=${created.id}`);
  return created;
}

async function main() {
  console.log(`Setting up NocoDB schema in base ${BASE_ID} at ${BASE}\n`);

  for (const [name, cols] of Object.entries(TABLES)) {
    try {
      await ensureTable(name, cols);
    } catch (e) {
      console.error(`✗ Failed to set up table "${name}": ${e.message}`);
    }
  }

  console.log('\nSchema setup complete.');
}

main().catch((e) => {
  console.error('Fatal:', e);
  process.exit(1);
});
