import { test, before } from 'node:test';
import assert from 'node:assert/strict';

// Env must be set before the config module is first imported.
process.env.APP_PASSPHRASE = 'test-pass-123';

const { normalizeHistory } = await import('../src/handlers.js');
const { issueToken, verifyToken } = await import('../src/authtoken.js');
const apiHandler = (await import('../netlify-functions/api.mjs')).default;

test('normalizeHistory accepts strings and block arrays, drops junk', () => {
  const messages = normalizeHistory([
    { role: 'user', content: 'hello' },
    { role: 'assistant', content: 'hi there' },
    { role: 'user', content: [
      { type: 'image', media_type: 'image/jpeg', data: 'aGVsbG8=' },
      { type: 'text', text: 'check my work' },
    ] },
    { role: 'system', content: 'ignored role' },
    { role: 'user', content: [{ type: 'weird' }] },
    null,
  ]);
  assert.equal(messages.length, 3);
  assert.equal(messages[0].content, 'hello');
  assert.equal(messages[2].content[0].type, 'image');
  assert.equal(messages[2].content[0].source.type, 'base64');
  assert.equal(messages[2].content[1].text, 'check my work');
});

test('normalizeHistory caps at 40 turns', () => {
  const long = Array.from({ length: 100 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: `m${i}` }));
  assert.equal(normalizeHistory(long).length, 40);
});

test('auth tokens verify and expire', () => {
  const token = issueToken();
  assert.equal(verifyToken(token), true);
  assert.equal(verifyToken(token + 'x'), false);
  assert.equal(verifyToken('12345.forged'), false);
  const past = issueToken(Date.now() - 40 * 24 * 60 * 60 * 1000);
  assert.equal(verifyToken(past), false);
});

// ---------------------------------------------------------------------------
// Netlify Functions adapter, driven with real Request objects
// ---------------------------------------------------------------------------

function jsonReq(path, body, cookie) {
  return new Request(`http://localhost${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(cookie ? { cookie } : {}) },
    body: JSON.stringify(body),
  });
}

let cookie;
before(async () => {
  const res = await apiHandler(jsonReq('/api/auth', { passphrase: 'test-pass-123' }));
  assert.equal(res.status, 200);
  cookie = res.headers.get('set-cookie').split(';')[0];
});

test('function adapter: wrong passphrase is rejected', async () => {
  const res = await apiHandler(jsonReq('/api/auth', { passphrase: 'nope' }));
  assert.equal(res.status, 401);
});

test('function adapter: auth is required for API routes', async () => {
  const res = await apiHandler(new Request('http://localhost/api/dashboard'));
  assert.equal(res.status, 401);
});

test('function adapter: dashboard returns data with a valid cookie', async () => {
  const res = await apiHandler(new Request('http://localhost/api/dashboard', { headers: { cookie } }));
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.ok(Array.isArray(data.next_dates));
  assert.ok(Array.isArray(data.due_reviews));
});

test('function adapter: agent route streams an SSE error when AI is unconfigured', async () => {
  const res = await apiHandler(jsonReq('/api/agent/coach', { message: 'hi', history: [] }, cookie));
  assert.equal(res.headers.get('content-type'), 'text/event-stream');
  const text = await res.text();
  assert.match(text, /^data: /m);
  assert.match(text, /AI is not configured/);
});

test('function adapter: unknown routes 404', async () => {
  const res = await apiHandler(new Request('http://localhost/api/nope', { headers: { cookie } }));
  assert.equal(res.status, 404);
});

test('function adapter: session close with empty history is a no-op', async () => {
  const res = await apiHandler(jsonReq('/api/session/close', { agent: 'coach', history: [] }, cookie));
  const data = await res.json();
  assert.equal(data.logged, false);
});

test('function adapter: digest preview renders HTML', async () => {
  const res = await apiHandler(new Request('http://localhost/api/digest/preview', { headers: { cookie } }));
  assert.equal(res.status, 200);
  assert.match(await res.text(), /Your week in review/);
});
