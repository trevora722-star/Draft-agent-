import 'dotenv/config';

const AGENT_FILES = [
  'content/01-tiktok-script.js',
  'content/02-blog-content.js',
  'content/03-email-sequence.js',
  'content/04-youtube-repurpose.js',
  'content/05-ad-copy.js',
  'content/06-case-study.js',
  'outreach/07-realtor-outreach.js',
  'outreach/08-inspector-outreach.js',
  'outreach/09-trades-outreach.js',
  'outreach/10-community-monitor.js',
  'outreach/11-followup.js',
  'seo/12-keyword-research.js',
  'seo/13-onpage-audit.js',
  'seo/14-content-brief.js',
  'seo/15-backlink-prospect.js',
  'crm/16-lead-scoring.js',
  'crm/17-upgrade-trigger.js',
  'crm/18-churn-detection.js',
  'crm/19-reengagement.js',
  'analytics/20-weekly-metrics.js',
  'analytics/21-content-performance.js',
  'analytics/22-funnel-analysis.js',
  'retention/23-referral-program.js',
  'retention/24-partner-dashboard.js',
  'retention/25-compliance.js',
  'command/26-growth-commander.js',
];

const results = [];

for (const file of AGENT_FILES) {
  const start = Date.now();
  try {
    const mod = await import(`../agents/${file}`);
    if (typeof mod.run !== 'function') {
      results.push({ file, ok: false, error: 'no run() export' });
      continue;
    }
    await mod.run({ dryRun: true });
    results.push({ file, ok: true, ms: Date.now() - start });
  } catch (e) {
    results.push({ file, ok: false, error: e.message, ms: Date.now() - start });
  }
}

console.log('\n=== DRY-RUN-ALL SUMMARY ===');
for (const r of results) {
  console.log(`${r.ok ? '✓' : '✗'} ${r.file}${r.ms ? ` (${r.ms}ms)` : ''}${r.error ? ' — ' + r.error : ''}`);
}
const failures = results.filter((r) => !r.ok).length;
process.exit(failures ? 1 : 0);
