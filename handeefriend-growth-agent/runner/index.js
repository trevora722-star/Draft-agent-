import 'dotenv/config';
import express from 'express';
import { readdir, readFile, stat } from 'node:fs/promises';
import { join, normalize } from 'node:path';
import { handleStripeWebhook } from '../webhooks/stripe.js';
import { AGENTS, DEPTS, findAgent } from '../lib/agent-registry.js';
import { nocodb } from '../lib/nocodb.js';

const app = express();

// Stripe webhook needs raw body BEFORE json middleware
app.post('/webhooks/stripe', express.raw({ type: 'application/json' }), handleStripeWebhook);

app.use(express.json());

// CORS — open by default; tighten via RUNNER_CORS_ORIGIN
app.use((req, res, next) => {
  const origin = process.env.RUNNER_CORS_ORIGIN || '*';
  res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PATCH,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

function authRequired(req, res, next) {
  const headerToken = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const queryToken = req.query.token; // SSE EventSource cannot send headers
  const token = headerToken || queryToken;
  if (!process.env.RUNNER_SECRET || token !== process.env.RUNNER_SECRET) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  next();
}

app.get('/health', (req, res) => {
  res.json({ ok: true, agents: AGENTS.length });
});

// Public read of registry — no secrets, just descriptions
app.get('/agents', (req, res) => {
  res.json({ agents: AGENTS, depts: DEPTS });
});

// Latest N runs of a given agent
app.get('/agents/:code/logs', authRequired, async (req, res) => {
  const agent = findAgent(req.params.code);
  if (!agent) return res.status(404).json({ error: 'unknown agent' });
  const limit = Math.min(50, Number(req.query.limit) || 10);
  try {
    const data = await nocodb.list('agent_logs', {
      where: `(agent_id,eq,${agent.id})`,
      sort: '-run_date',
      limit,
    });
    res.json({ list: data.list || data.records || [] });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Combined detail — registry + recent logs + recent output files
app.get('/agents/:code', authRequired, async (req, res) => {
  const agent = findAgent(req.params.code);
  if (!agent) return res.status(404).json({ error: 'unknown agent' });

  let logs = [];
  try {
    const data = await nocodb.list('agent_logs', {
      where: `(agent_id,eq,${agent.id})`,
      sort: '-run_date',
      limit: 10,
    });
    logs = data.list || data.records || [];
  } catch {}

  const outputDir = `output/${agent.dept}`;
  let files = [];
  try {
    const entries = await readdir(outputDir);
    files = await Promise.all(
      entries.slice(0, 20).map(async (name) => {
        const full = join(outputDir, name);
        const s = await stat(full);
        return { name, size: s.size, mtime: s.mtime, path: `${agent.dept}/${name}` };
      }),
    );
    files.sort((a, b) => b.mtime - a.mtime);
  } catch {}

  res.json({ agent, logs, files });
});

// Read a single output file (path-traversal safe)
app.get('/output/*', authRequired, async (req, res) => {
  const rel = req.params[0] || '';
  const safe = normalize(rel).replace(/^(\.\.[/\\])+/, '');
  if (safe.startsWith('..') || safe.includes('..\\') || safe.includes('../')) {
    return res.status(400).json({ error: 'invalid path' });
  }
  const full = join('output', safe);
  try {
    const body = await readFile(full, 'utf8');
    if (full.endsWith('.json')) res.type('application/json');
    else if (full.endsWith('.html')) res.type('text/html');
    else res.type('text/plain');
    res.send(body);
  } catch (e) {
    res.status(404).json({ error: e.message });
  }
});

// Recent agent_logs across all agents (dashboard summary)
app.get('/logs/recent', authRequired, async (req, res) => {
  const limit = Math.min(100, Number(req.query.limit) || 30);
  try {
    const data = await nocodb.list('agent_logs', {
      sort: '-run_date',
      limit,
    });
    res.json({ list: data.list || data.records || [] });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Trigger an agent (fire-and-forget)
app.post('/run/:code', authRequired, async (req, res) => {
  const agent = findAgent(req.params.code);
  if (!agent) return res.status(404).json({ error: 'unknown agent' });

  res.json({ status: 'started', agent: agent.code, dryRun: !!req.body?.dry_run });

  setImmediate(async () => {
    try {
      const mod = await import(`../agents/${agent.file}`);
      if (typeof mod.run !== 'function') {
        throw new Error(`Agent ${agent.code} does not export run()`);
      }
      await mod.run({ ...req.body, dryRun: req.body?.dry_run });
    } catch (e) {
      console.error(`[runner] Agent ${agent.code} failed:`, e);
    }
  });
});

// SSE — runs an agent and streams console output to the browser in real-time.
// Browsers' EventSource can't send Authorization headers, so we accept ?token=.
app.get('/run-stream/:code', authRequired, async (req, res) => {
  const agent = findAgent(req.params.code);
  if (!agent) return res.status(404).json({ error: 'unknown agent' });

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders?.();

  const send = (event, data) => {
    res.write(`event: ${event}\n`);
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  send('open', { agent: agent.code, name: agent.name, dryRun: req.query.dry_run === '1' });

  // Capture console output for the duration of this run
  const origLog = console.log;
  const origErr = console.error;
  const origWarn = console.warn;

  const tap = (level) => (...args) => {
    const msg = args
      .map((a) => (typeof a === 'string' ? a : (() => { try { return JSON.stringify(a); } catch { return String(a); } })()))
      .join(' ');
    send('log', { level, msg, ts: new Date().toISOString() });
    // Still write to the underlying server console
    if (level === 'error') origErr.apply(console, args);
    else if (level === 'warn') origWarn.apply(console, args);
    else origLog.apply(console, args);
  };

  console.log = tap('info');
  console.error = tap('error');
  console.warn = tap('warn');

  let closed = false;
  req.on('close', () => { closed = true; });

  try {
    const mod = await import(`../agents/${agent.file}`);
    if (typeof mod.run !== 'function') throw new Error('agent has no run()');
    const dryRun = req.query.dry_run === '1';
    const opts = { dryRun };
    if (req.query.user_id) opts.user_id = req.query.user_id;
    if (req.query.sequence) opts.sequence = req.query.sequence;
    if (req.query.content_calendar_id) opts.content_calendar_id = req.query.content_calendar_id;
    const result = await mod.run(opts);
    if (!closed) send('done', { ok: true, result: result ?? null });
  } catch (e) {
    if (!closed) send('done', { ok: false, error: e.message, stack: e.stack });
  } finally {
    console.log = origLog;
    console.error = origErr;
    console.warn = origWarn;
    res.end();
  }
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

// Serve the bundled web interfaces directly from the runner so the user has
// a single URL to open. Static files come from interfaces/.
app.use('/', express.static('interfaces'));

const port = process.env.PORT || 3001;
app.listen(port, () => {
  console.log(`HandeeFriend runner on :${port} — ${AGENTS.length} agents registered`);
  console.log(`Open http://localhost:${port}/agents.html for the control center`);
});
