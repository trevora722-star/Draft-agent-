// Netlify Functions v2 adapter: the whole TutorAgent API as one catch-all
// function using web-standard Request/Response. Shares all logic with the
// Express server via ../src/handlers.js — handlers are stateless (the client
// holds conversation history), which is what makes serverless viable.

import { config as appConfig, features } from '../src/config.js';
import { createStore } from '../src/store.js';
import { Repo } from '../src/repo.js';
import { AGENTS } from '../src/agents/prompts.js';
import { issueToken, verifyToken, passphraseMatches } from '../src/authtoken.js';
import {
  agentTurnEvents, handleOutlineUpload, handleWorkPhoto, handleSessionClose, sseSerialize,
} from '../src/handlers.js';
import { renderDigestHtml, sendDigest } from '../src/digest.js';

const COOKIE = 'ta_session';

// Module scope survives warm invocations; init runs once per cold start.
const store = createStore();
const repo = new Repo(store);
let initPromise = null;
function ensureInit() {
  if (!initPromise) {
    initPromise = store.init().catch((err) => {
      console.error('[boot] Storage init failed:', err.message);
      initPromise = null; // retry next invocation
      throw err;
    });
  }
  return initPromise;
}

function json(status, body, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...extraHeaders },
  });
}

function getCookie(req, name) {
  const raw = req.headers.get('cookie') || '';
  for (const part of raw.split(/;\s*/)) {
    const eq = part.indexOf('=');
    if (eq > 0 && part.slice(0, eq) === name) return decodeURIComponent(part.slice(eq + 1));
  }
  return '';
}

function authed(req) {
  return verifyToken(getCookie(req, COOKIE));
}

export default async function handler(req) {
  const url = new URL(req.url);
  const route = url.pathname.replace(/\/+$/, '');

  try {
    await ensureInit().catch(() => {}); // degrade like the Express server does

    // ---- public routes ----------------------------------------------------
    if (route === '/api/auth' && req.method === 'POST') {
      if (!features.auth) return json(503, { error: 'APP_PASSPHRASE is not set — logins are disabled.' });
      const body = await req.json().catch(() => ({}));
      if (!passphraseMatches(body.passphrase, appConfig.passphrase)) {
        return json(401, { error: 'That passphrase is not right.' });
      }
      const cookie = `${COOKIE}=${issueToken()}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=${30 * 24 * 3600}`;
      return json(200, { ok: true }, { 'set-cookie': cookie });
    }
    if (route === '/api/logout' && req.method === 'POST') {
      return json(200, { ok: true }, { 'set-cookie': `${COOKIE}=; Path=/; HttpOnly; Max-Age=0` });
    }
    if (route === '/api/me') {
      return json(200, { authed: authed(req), features });
    }

    // ---- everything below requires auth ------------------------------------
    if (!authed(req)) return json(401, { error: 'Not signed in.' });

    if (route.startsWith('/api/agent/') && req.method === 'POST') {
      const agentKey = route.slice('/api/agent/'.length);
      const body = await req.json().catch(() => ({}));
      const encoder = new TextEncoder();
      const stream = new ReadableStream({
        async start(controller) {
          try {
            for await (const event of agentTurnEvents(repo, {
              agentKey,
              message: body.message,
              history: body.history,
            })) {
              controller.enqueue(encoder.encode(sseSerialize(event)));
            }
          } catch (err) {
            controller.enqueue(encoder.encode(sseSerialize({ type: 'error', error: err.message })));
          } finally {
            controller.close();
          }
        },
      });
      return new Response(stream, {
        headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      });
    }

    if (route === '/api/upload/outline' && req.method === 'POST') {
      const form = await req.formData();
      const file = form.get('file');
      if (!file || typeof file === 'string') return json(400, { error: 'No file uploaded.' });
      if (file.type !== 'application/pdf') return json(400, { error: 'Course outlines must be PDFs.' });
      const out = await handleOutlineUpload(repo, {
        buffer: Buffer.from(await file.arrayBuffer()),
        filename: file.name,
        courseName: form.get('course') || '',
      });
      return json(out.status, out.json);
    }

    if (route === '/api/upload/work-photo' && req.method === 'POST') {
      const form = await req.formData();
      const file = form.get('file');
      if (!file || typeof file === 'string') return json(400, { error: 'No file uploaded.' });
      const out = await handleWorkPhoto({
        buffer: Buffer.from(await file.arrayBuffer()),
        filename: file.name,
        mimetype: file.type,
      });
      return json(out.status, out.json);
    }

    if (route === '/api/session/close' && req.method === 'POST') {
      const body = await req.json().catch(() => ({}));
      const out = await handleSessionClose(repo, {
        agentKey: String(body.agent || ''),
        history: body.history,
        durationMin: body.duration_min,
      });
      return json(out.status, out.json);
    }

    if (route === '/api/dashboard') {
      return json(200, await repo.dashboard());
    }

    if (route === '/api/digest/preview') {
      return new Response(renderDigestHtml(await repo.digestData()), {
        headers: { 'content-type': 'text/html; charset=utf-8' },
      });
    }

    if (route === '/api/digest/send' && req.method === 'POST') {
      const sent = await sendDigest(repo);
      return json(200, { ok: true, id: sent?.id || null });
    }

    if (route === '/api/agents') {
      return json(200, Object.entries(AGENTS).map(([key, a]) => ({ key, name: a.name })));
    }

    return json(404, { error: `No such route: ${route}` });
  } catch (err) {
    console.error('[api]', err);
    return json(500, { error: err.message });
  }
}

export const config = { path: '/api/*' };
