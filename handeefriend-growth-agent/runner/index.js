import 'dotenv/config';
import express from 'express';
import { handleStripeWebhook } from '../webhooks/stripe.js';

const app = express();

// Stripe webhook needs raw body BEFORE json middleware
app.post('/webhooks/stripe', express.raw({ type: 'application/json' }), handleStripeWebhook);

app.use(express.json());

const AGENT_MAP = {
  '01': 'content/01-tiktok-script.js',
  '02': 'content/02-blog-content.js',
  '03': 'content/03-email-sequence.js',
  '04': 'content/04-youtube-repurpose.js',
  '05': 'content/05-ad-copy.js',
  '06': 'content/06-case-study.js',
  '07': 'outreach/07-realtor-outreach.js',
  '08': 'outreach/08-inspector-outreach.js',
  '09': 'outreach/09-trades-outreach.js',
  '10': 'outreach/10-community-monitor.js',
  '11': 'outreach/11-followup.js',
  '12': 'seo/12-keyword-research.js',
  '13': 'seo/13-onpage-audit.js',
  '14': 'seo/14-content-brief.js',
  '15': 'seo/15-backlink-prospect.js',
  '16': 'crm/16-lead-scoring.js',
  '17': 'crm/17-upgrade-trigger.js',
  '18': 'crm/18-churn-detection.js',
  '19': 'crm/19-reengagement.js',
  '20': 'analytics/20-weekly-metrics.js',
  '21': 'analytics/21-content-performance.js',
  '22': 'analytics/22-funnel-analysis.js',
  '23': 'retention/23-referral-program.js',
  '24': 'retention/24-partner-dashboard.js',
  '25': 'retention/25-compliance.js',
  '26': 'command/26-growth-commander.js',
};

function authRequired(req, res, next) {
  const token = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  if (!process.env.RUNNER_SECRET || token !== process.env.RUNNER_SECRET) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  next();
}

app.get('/health', (req, res) => {
  res.json({ ok: true, agents: Object.keys(AGENT_MAP).length });
});

app.post('/run/:agentFile', authRequired, async (req, res) => {
  const { agentFile } = req.params;
  const filePath = AGENT_MAP[agentFile] || agentFile;
  const importPath = `../agents/${filePath}`;

  // Acknowledge immediately, run async
  res.json({ status: 'started', agent: agentFile, dryRun: !!req.body?.dry_run });

  setImmediate(async () => {
    try {
      const mod = await import(importPath);
      if (typeof mod.run !== 'function') {
        throw new Error(`Agent ${agentFile} does not export run()`);
      }
      await mod.run({ ...req.body, dryRun: req.body?.dry_run });
    } catch (e) {
      console.error(`[runner] Agent ${agentFile} failed:`, e);
    }
  });
});

// Public referral webhook (Make.com calls this from a Stripe checkout completed flow)
app.post('/referral-event', authRequired, async (req, res) => {
  try {
    const { handleReferralEvent } = await import('../agents/retention/23-referral-program.js');
    const result = await handleReferralEvent({
      refId: req.body.refId,
      referredEmail: req.body.referredEmail,
      eventType: req.body.eventType || 'signup',
      utm: req.body.utm,
      dryRun: !!req.body.dry_run,
    });
    res.json({ ok: true, result });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

const port = process.env.PORT || 3001;
app.listen(port, () => {
  console.log(`HandeeFriend runner listening on :${port} — ${Object.keys(AGENT_MAP).length} agents registered`);
});
