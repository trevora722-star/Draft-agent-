// Thin wrapper around the Anthropic SDK used by every QAgent session.
//
// Two entry points:
//   - askClaude(prompt, opts)        — text-in, text-out
//   - askClaudeVision(prompt, path)  — text + image in, text-out
//
// Both retry on 429 and 5xx with exponential backoff (3 attempts, 1s/2s/4s).
// When `json: true`, the response is fence-stripped and JSON.parse'd.

import fs from 'node:fs/promises';
import path from 'node:path';
import Anthropic from '@anthropic-ai/sdk';

const DEFAULT_MODEL = process.env.QAGENT_MODEL_SONNET || 'claude-sonnet-4-6';
const VISION_MODEL = process.env.QAGENT_MODEL_OPUS || 'claude-opus-4-7';

let _client = null;
function client() {
  if (_client) return _client;
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    throw new Error(
      'ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.',
    );
  }
  _client = new Anthropic({ apiKey });
  return _client;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Return true if the SDK error is worth retrying.
 *
 * @param {unknown} err
 */
function isRetryable(err) {
  if (!err || typeof err !== 'object') return false;
  const status = /** @type {{status?: number}} */ (err).status;
  if (typeof status === 'number') {
    return status === 429 || status >= 500;
  }
  // Network errors from undici / node fetch have a `code` field.
  const code = /** @type {{code?: string}} */ (err).code;
  return ['ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN'].includes(
    code ?? '',
  );
}

/**
 * Run a function with up to N attempts and exponential backoff. Only retries
 * when `isRetryable(err)` is true.
 *
 * @template T
 * @param {() => Promise<T>} fn
 * @param {number} attempts
 * @returns {Promise<T>}
 */
async function withRetry(fn, attempts = 3) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (i === attempts - 1 || !isRetryable(err)) throw err;
      const backoff = 1000 * 2 ** i;
      await sleep(backoff);
    }
  }
  throw lastErr;
}

/**
 * Strip ```json fences and parse. Throws on parse failure with context.
 *
 * @param {string} raw
 * @returns {unknown}
 */
function parseJsonResponse(raw) {
  let s = raw.trim();
  // Drop leading ```json / ``` and trailing ```
  s = s.replace(/^```(?:json|JSON)?\s*\n?/, '').replace(/```\s*$/, '');
  // Some models prepend a "Here is the JSON:" preface — try to find the first { or [.
  const firstBrace = s.search(/[\[{]/);
  if (firstBrace > 0) s = s.slice(firstBrace);
  try {
    return JSON.parse(s);
  } catch (err) {
    const preview = raw.slice(0, 400);
    throw new Error(
      `Failed to parse Claude JSON response: ${err.message}\n--- response head ---\n${preview}`,
    );
  }
}

/**
 * Send a prompt to Claude.
 *
 * @param {string} prompt
 * @param {object} [opts]
 * @param {string} [opts.model]           Model id. Defaults to QAGENT_MODEL_SONNET.
 * @param {string} [opts.system]          Optional system prompt.
 * @param {boolean} [opts.json]           If true, parse the reply as JSON.
 * @param {number}  [opts.maxTokens]      Default 4096.
 * @param {number}  [opts.temperature]    Default 0.2.
 * @param {number}  [opts.attempts]       Retry attempts on 429/5xx. Default 3.
 * @returns {Promise<string | unknown>}   string, or parsed JSON when opts.json.
 */
export async function askClaude(prompt, opts = {}) {
  const {
    model = DEFAULT_MODEL,
    system,
    json = false,
    maxTokens = 4096,
    temperature = 0.2,
    attempts = 3,
  } = opts;

  const response = await withRetry(
    () =>
      client().messages.create({
        model,
        max_tokens: maxTokens,
        temperature,
        ...(system ? { system } : {}),
        messages: [{ role: 'user', content: prompt }],
      }),
    attempts,
  );

  const text = response.content
    .filter((b) => b.type === 'text')
    .map((b) => /** @type {{text: string}} */ (b).text)
    .join('\n')
    .trim();

  return json ? parseJsonResponse(text) : text;
}

/** Map a file extension to a media type Claude accepts. */
function mediaTypeFor(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  switch (ext) {
    case '.png':
      return 'image/png';
    case '.jpg':
    case '.jpeg':
      return 'image/jpeg';
    case '.gif':
      return 'image/gif';
    case '.webp':
      return 'image/webp';
    default:
      throw new Error(`Unsupported image extension for Claude vision: ${ext}`);
  }
}

/**
 * Send a prompt + image to Claude (vision-capable model).
 *
 * @param {string} prompt
 * @param {string} imagePath              Absolute or process-relative path.
 * @param {object} [opts]
 * @param {string} [opts.model]           Default QAGENT_MODEL_OPUS.
 * @param {string} [opts.system]
 * @param {boolean} [opts.json]
 * @param {number}  [opts.maxTokens]
 * @param {number}  [opts.temperature]
 * @param {number}  [opts.attempts]
 * @returns {Promise<string | unknown>}
 */
export async function askClaudeVision(prompt, imagePath, opts = {}) {
  const {
    model = VISION_MODEL,
    system,
    json = false,
    maxTokens = 4096,
    temperature = 0.2,
    attempts = 3,
  } = opts;

  const buf = await fs.readFile(imagePath);
  const data = buf.toString('base64');
  const media_type = mediaTypeFor(imagePath);

  const response = await withRetry(
    () =>
      client().messages.create({
        model,
        max_tokens: maxTokens,
        temperature,
        ...(system ? { system } : {}),
        messages: [
          {
            role: 'user',
            content: [
              { type: 'image', source: { type: 'base64', media_type, data } },
              { type: 'text', text: prompt },
            ],
          },
        ],
      }),
    attempts,
  );

  const text = response.content
    .filter((b) => b.type === 'text')
    .map((b) => /** @type {{text: string}} */ (b).text)
    .join('\n')
    .trim();

  return json ? parseJsonResponse(text) : text;
}

export const _internal = { parseJsonResponse, isRetryable, withRetry };
