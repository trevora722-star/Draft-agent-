import express from 'express';
import cookieParser from 'cookie-parser';
import multer from 'multer';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { config, features, reportMissingConfig } from './src/config.js';
import { createStore } from './src/store.js';
import { Repo } from './src/repo.js';
import { AGENTS } from './src/agents/prompts.js';
import { issueToken, verifyToken, passphraseMatches } from './src/authtoken.js';
import {
  agentTurnEvents, handleOutlineUpload, handleWorkPhoto, handleSessionClose, sseSerialize,
} from './src/handlers.js';
import { renderDigestHtml, sendDigest, scheduleWeeklyDigest } from './src/digest.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();
app.use(express.json({ limit: '12mb' })); // photo-bearing turns carry base64 image blocks
app.use(cookieParser());
app.use(express.static(path.join(__dirname, 'public')));

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 15 * 1024 * 1024 } });

const store = createStore();
const repo = new Repo(store);

// ---------------------------------------------------------------------------
// Auth (stateless signed cookie — same token logic as the Netlify functions)
// ---------------------------------------------------------------------------

const COOKIE = 'ta_session';
const loginAttempts = { count: 0, resetAt: 0 };

app.post('/api/auth', (req, res) => {
  if (!features.auth) {
    return res.status(503).json({ error: 'APP_PASSPHRASE is not set on the server — logins are disabled.' });
  }
  const now = Date.now();
  if (now > loginAttempts.resetAt) {
    loginAttempts.count = 0;
    loginAttempts.resetAt = now + 15 * 60 * 1000;
  }
  if (loginAttempts.count >= 20) {
    return res.status(429).json({ error: 'Too many attempts. Try again in a few minutes.' });
  }
  if (!passphraseMatches(req.body?.passphrase, config.passphrase)) {
    loginAttempts.count++;
    return res.status(401).json({ error: 'That passphrase is not right.' });
  }
  res.cookie(COOKIE, issueToken(), {
    httpOnly: true,
    sameSite: 'lax',
    secure: req.secure || req.headers['x-forwarded-proto'] === 'https',
    maxAge: 30 * 24 * 60 * 60 * 1000,
  });
  res.json({ ok: true });
});

app.post('/api/logout', (req, res) => {
  res.clearCookie(COOKIE);
  res.json({ ok: true });
});

function requireAuth(req, res, next) {
  if (!verifyToken(req.cookies[COOKIE])) return res.status(401).json({ error: 'Not signed in.' });
  next();
}

app.get('/api/me', (req, res) => {
  res.json({ authed: verifyToken(req.cookies[COOKIE]), features });
});

// ---------------------------------------------------------------------------
// Agent chat (SSE streaming; history supplied by the client)
// ---------------------------------------------------------------------------

app.post('/api/agent/:name', requireAuth, async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders?.();
  try {
    for await (const event of agentTurnEvents(repo, {
      agentKey: req.params.name,
      message: req.body?.message,
      history: req.body?.history,
    })) {
      res.write(sseSerialize(event));
    }
  } finally {
    res.end();
  }
});

// ---------------------------------------------------------------------------
// Uploads
// ---------------------------------------------------------------------------

app.post('/api/upload/outline', requireAuth, upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded.' });
    if (req.file.mimetype !== 'application/pdf') {
      return res.status(400).json({ error: 'Course outlines must be PDFs.' });
    }
    const out = await handleOutlineUpload(repo, {
      buffer: req.file.buffer,
      filename: req.file.originalname,
      courseName: req.body?.course,
    });
    res.status(out.status).json(out.json);
  } catch (err) {
    console.error('[outline]', err);
    res.status(500).json({ error: `Outline processing failed: ${err.message}` });
  }
});

app.post('/api/upload/work-photo', requireAuth, upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded.' });
    const out = await handleWorkPhoto({
      buffer: req.file.buffer,
      filename: req.file.originalname,
      mimetype: req.file.mimetype,
    });
    res.status(out.status).json(out.json);
  } catch (err) {
    console.error('[work-photo]', err);
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Session close, dashboard, digest
// ---------------------------------------------------------------------------

app.post('/api/session/close', requireAuth, async (req, res) => {
  try {
    const out = await handleSessionClose(repo, {
      agentKey: String(req.body?.agent || ''),
      history: req.body?.history,
      durationMin: req.body?.duration_min,
    });
    res.status(out.status).json(out.json);
  } catch (err) {
    console.error('[session/close]', err);
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/dashboard', requireAuth, async (req, res) => {
  try {
    res.json(await repo.dashboard());
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/digest/preview', requireAuth, async (req, res) => {
  try {
    res.type('html').send(renderDigestHtml(await repo.digestData()));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/digest/send', requireAuth, async (req, res) => {
  try {
    const sent = await sendDigest(repo);
    res.json({ ok: true, id: sent?.id || null });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/agents', requireAuth, (req, res) => {
  res.json(Object.entries(AGENTS).map(([key, a]) => ({ key, name: a.name })));
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function main() {
  reportMissingConfig();
  try {
    await store.init();
    console.log(`[boot] Storage ready (${features.nocodb ? 'NocoDB' : 'in-memory fallback'})`);
  } catch (err) {
    console.error(`[boot] Storage init failed: ${err.message}`);
    console.error('[boot] Continuing — data operations will fail until NocoDB is reachable.');
  }
  scheduleWeeklyDigest(repo);
  app.listen(config.port, () => {
    console.log(`[boot] TutorAgent listening on http://localhost:${config.port}`);
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main();
}

export { app, repo, store };
