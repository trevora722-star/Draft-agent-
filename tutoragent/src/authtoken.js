import crypto from 'node:crypto';
import { config } from './config.js';

// Stateless signed session token: works identically on the long-running
// Express server and on serverless (Netlify Functions), where in-memory
// session maps don't survive between invocations.
//
// Token = "<expiryMs>.<hmac>", keyed off APP_PASSPHRASE (or SESSION_SECRET
// if set, so rotating the passphrase doesn't log her out).

const TTL_MS = 30 * 24 * 60 * 60 * 1000;

function key() {
  const secret = process.env.SESSION_SECRET || config.passphrase;
  return crypto.createHash('sha256').update(`tutoragent:${secret}`).digest();
}

function sign(payload) {
  return crypto.createHmac('sha256', key()).update(payload).digest('base64url');
}

export function issueToken(now = Date.now()) {
  const exp = String(now + TTL_MS);
  return `${exp}.${sign(exp)}`;
}

export function verifyToken(token, now = Date.now()) {
  if (!config.passphrase && !process.env.SESSION_SECRET) return false;
  const [exp, sig] = String(token || '').split('.');
  if (!exp || !sig) return false;
  const expected = sign(exp);
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return false;
  return Number(exp) > now;
}

export function passphraseMatches(supplied, expected) {
  if (!expected) return false;
  const a = Buffer.from(String(supplied || ''));
  const b = Buffer.from(String(expected));
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}
