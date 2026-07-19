import express from 'express';
import cookieParser from 'cookie-parser';
import multer from 'multer';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { config, features, reportMissingConfig } from './src/config.js';
import { createStore } from './src/store.js';
import { Repo } from './src/repo.js';
import { getAgent, AGENTS } from './src/agents/prompts.js';
import { extractTags, applyTags } from './src/agents/tags.js';
import { streamAgentTurn, jsonCall } from './src/claude.js';
import { extractPdfText, extractOutline } from './src/pdf-extract.js';
import { uploadToSpaces } from './src/spaces.js';
import { renderDigestHtml, sendDigest, scheduleWeeklyDigest } from './src/digest.js';
import {
  createSession, getSession, destroySession, getConversation, pushTurn,
  takePendingImages, addPendingImage, markAgentActivity, takeAgentDuration,
  passphraseMatches,
} from './src/sessions.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();
app.use(express.json({ limit: '2mb' }));
app.use(cookieParser());
app.use(express.static(path.join(__dirname, 'public')));

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 15 * 1024 * 1024 } });

const store = createStore();
const repo = new Repo(store);

// ---------------------------------------------------------------------------
// Auth
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
  const { passphrase } = req.body || {};
  if (!passphraseMatches(passphrase, config.passphrase)) {
    loginAttempts.count++;
    return res.status(401).json({ error: 'That passphrase is not right.' });
  }
  const token = createSession();
  res.cookie(COOKIE, token, {
    httpOnly: true,
    sameSite: 'lax',
    secure: req.secure || req.headers['x-forwarded-proto'] === 'https',
    maxAge: 30 * 24 * 60 * 60 * 1000,
  });
  res.json({ ok: true });
});

app.post('/api/logout', (req, res) => {
  destroySession(req.cookies[COOKIE]);
  res.clearCookie(COOKIE);
  res.json({ ok: true });
});

function requireAuth(req, res, next) {
  const session = getSession(req.cookies[COOKIE]);
  if (!session) return res.status(401).json({ error: 'Not signed in.' });
  req.session = session;
  next();
}

app.get('/api/me', (req, res) => {
  const session = getSession(req.cookies[COOKIE]);
  res.json({ authed: Boolean(session), features });
});

// ---------------------------------------------------------------------------
// Agent chat (SSE streaming)
// ---------------------------------------------------------------------------

app.post('/api/agent/:name', requireAuth, async (req, res) => {
  const agent = getAgent(req.params.name);
  if (!agent) return res.status(404).json({ error: `Unknown agent: ${req.params.name}` });
  const userText = String(req.body?.message || '').slice(0, 20000);
  if (!userText.trim()) return res.status(400).json({ error: 'Empty message.' });
  if (!features.ai) return res.status(503).json({ error: 'AI is not configured (missing ANTHROPIC_API_KEY).' });

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders?.();
  const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);

  try {
    markAgentActivity(req.session, req.params.name);

    // Build this turn's user content: pending work photos (if any) + text.
    const images = takePendingImages(req.session, req.params.name);
    const content = [
      ...images.map((img) => ({
        type: 'image',
        source: { type: 'base64', media_type: img.media_type, data: img.data },
      })),
      { type: 'text', text: userText },
    ];
    pushTurn(req.session, req.params.name, 'user', content);

    const studentContext = await repo.studentContext().catch((e) => `(context unavailable: ${e.message})`);
    const system = `${agent.system}\n\nSTUDENT CONTEXT (live from the database, do not show verbatim):\n${studentContext}`;

    const fullText = await streamAgentTurn({
      system,
      messages: getConversation(req.session, req.params.name),
      onText: (text) => send({ type: 'text', text }),
    });

    pushTurn(req.session, req.params.name, 'assistant', fullText);

    // Apply any machine-readable tag blocks (course setup, quiz results, ...).
    const tags = extractTags(fullText);
    if (tags.length) {
      const events = await applyTags(repo, tags);
      if (events.length) send({ type: 'meta', events });
    }
    send({ type: 'done' });
  } catch (err) {
    console.error('[agent]', err);
    send({ type: 'error', error: err.message });
  } finally {
    res.end();
  }
});

// ---------------------------------------------------------------------------
// Uploads
// ---------------------------------------------------------------------------

// Course outline PDF -> Spaces -> text extraction -> Claude -> NocoDB enrichment.
app.post('/api/upload/outline', requireAuth, upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded.' });
    if (req.file.mimetype !== 'application/pdf') {
      return res.status(400).json({ error: 'Course outlines must be PDFs.' });
    }
    if (!features.ai) return res.status(503).json({ error: 'AI is not configured — cannot extract the outline.' });

    const pdfText = await extractPdfText(req.file.buffer);
    if (pdfText.length < 100) {
      return res.status(422).json({ error: 'Could not read text from that PDF (is it a scan? try a text-based PDF).' });
    }

    const extraction = await extractOutline(pdfText);

    // Resolve the course: explicit courseName field wins, else what the model found.
    const courseName = String(req.body?.course || extraction.course_name || '').trim();
    if (!courseName) {
      return res.status(422).json({ error: 'Could not tell which course this outline is for — pick the course and re-upload.' });
    }
    let course = await repo.findCourseByName(courseName);
    if (!course) ({ course } = await repo.seedCourse(courseName));

    const outlineUrl = await uploadToSpaces('outlines', req.file.originalname, req.file.buffer, 'application/pdf')
      .catch((e) => {
        console.warn('[spaces] outline upload failed:', e.message);
        return '';
      });

    const updated = await repo.applyOutlineExtraction(course.id, extraction, outlineUrl);

    // Let the onboarding agent know, so the conversation can continue naturally.
    pushTurn(req.session, 'onboarding', 'user',
      `[System note — not typed by the student] Outline for ${updated.name} was processed automatically: ` +
      `${extraction.topics.length} weekly topics, ${extraction.key_dates.length} key dates` +
      `${extraction.grading_weights ? ', grading weights captured' : ''}` +
      `${extraction.textbook_title ? `, textbook "${extraction.textbook_title}"` : ''}. ` +
      `Course is now in enriched mode. Summarize this for the student and ask her to sanity-check the dates.`);

    res.json({
      ok: true,
      course: updated.name,
      status: updated.status,
      topics: extraction.topics.length,
      key_dates: extraction.key_dates.length,
      textbook: extraction.textbook_title || null,
      grading_weights: extraction.grading_weights || null,
      outline_file_url: outlineUrl || null,
    });
  } catch (err) {
    console.error('[outline]', err);
    res.status(500).json({ error: `Outline processing failed: ${err.message}` });
  }
});

// Handwritten-work photo -> Spaces -> queued as vision input for the next turn.
app.post('/api/upload/work-photo', requireAuth, upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded.' });
    const allowed = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    if (!allowed.includes(req.file.mimetype)) {
      return res.status(400).json({ error: 'Photos must be JPEG, PNG, GIF, or WebP.' });
    }
    const agent = String(req.body?.agent || 'problem');
    if (!getAgent(agent)) return res.status(400).json({ error: 'Unknown agent.' });

    const url = await uploadToSpaces('work-photos', req.file.originalname, req.file.buffer, req.file.mimetype)
      .catch((e) => {
        console.warn('[spaces] photo upload failed:', e.message);
        return '';
      });

    addPendingImage(req.session, agent, {
      media_type: req.file.mimetype,
      data: req.file.buffer.toString('base64'),
    });

    res.json({ ok: true, url: url || null, note: 'Photo attached — it will be included with your next message.' });
  } catch (err) {
    console.error('[work-photo]', err);
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Session close -> study_sessions summary
// ---------------------------------------------------------------------------

app.post('/api/session/close', requireAuth, async (req, res) => {
  try {
    const agentName = String(req.body?.agent || '');
    const agent = getAgent(agentName);
    if (!agent) return res.status(400).json({ error: 'Unknown agent.' });
    const conv = getConversation(req.session, agentName);
    if (!conv.length) return res.json({ ok: true, logged: false, reason: 'No conversation to summarize.' });

    const durationMin = takeAgentDuration(req.session, agentName) || Number(req.body?.duration_min) || 1;

    let summary = { summary: `Worked with ${agent.name}.`, course: null, topic: null, struggles: [], topics_covered: [] };
    if (features.ai) {
      const transcript = conv
        .map((m) => {
          const text = Array.isArray(m.content)
            ? m.content.filter((b) => b.type === 'text').map((b) => b.text).join(' ')
            : String(m.content);
          return `${m.role.toUpperCase()}: ${text.slice(0, 1500)}`;
        })
        .join('\n')
        .slice(-24000);
      try {
        summary = await jsonCall({
          system: `You summarize tutoring sessions. Return ONLY JSON:
{"summary": "<one line: what was worked on and where the student struggled>",
 "course": "<course code like CHEM 1110 or null>",
 "topic": "<main topic name or null>",
 "struggles": ["<specific struggle>", ...],
 "topics_covered": ["<topic names that were substantively covered>", ...]}`,
          user: `Agent: ${agent.name}\nTranscript:\n${transcript}`,
          maxTokens: 800,
        });
      } catch (e) {
        console.warn('[session/close] summary failed:', e.message);
      }
    }

    const course = summary.course ? await repo.findCourseByName(summary.course) : null;
    const topic = summary.topic ? await repo.findTopicByName(summary.topic, course?.id) : null;

    const notes = [summary.summary, summary.struggles?.length ? `Struggled with: ${summary.struggles.join('; ')}` : '']
      .filter(Boolean)
      .join(' | ');

    await repo.logStudySession({
      courseId: course?.id ?? null,
      topicId: topic?.id ?? null,
      agent: agentName,
      durationMin,
      notes,
    });

    // Mark substantively-covered topics as covered.
    for (const name of summary.topics_covered || []) {
      const t = await repo.findTopicByName(name, course?.id);
      if (t && t.status !== 'covered') await repo.setTopicStatus(t.id, 'covered');
    }

    // Reset the conversation so the next visit starts fresh.
    req.session.conversations[agentName] = [];

    res.json({ ok: true, logged: true, notes, duration_min: durationMin });
  } catch (err) {
    console.error('[session/close]', err);
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Dashboard & digest
// ---------------------------------------------------------------------------

app.get('/api/dashboard', requireAuth, async (req, res) => {
  try {
    res.json(await repo.dashboard());
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/digest/preview', requireAuth, async (req, res) => {
  try {
    const data = await repo.digestData();
    res.type('html').send(renderDigestHtml(data));
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

// Only start the server when run directly (lets tests import the app).
if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main();
}

export { app, repo, store };
