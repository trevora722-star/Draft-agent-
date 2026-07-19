import Anthropic from '@anthropic-ai/sdk';
import { config, features } from './config.js';

let client = null;
export function getClient() {
  if (!features.ai) return null;
  if (!client) client = new Anthropic({ apiKey: config.anthropicApiKey });
  return client;
}

export const MODEL = config.model;

// Streams a tutoring turn. onText receives incremental text; returns the full
// final text. `messages` is standard Anthropic messages content (may include
// image blocks for work photos).
export async function streamAgentTurn({ system, messages, onText, maxTokens = 4096 }) {
  const anthropic = getClient();
  if (!anthropic) throw new Error('AI is not configured (missing ANTHROPIC_API_KEY)');
  const stream = anthropic.messages.stream({
    model: MODEL,
    max_tokens: maxTokens,
    system,
    messages,
  });
  stream.on('text', (text) => onText?.(text));
  const final = await stream.finalMessage();
  return final.content
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('');
}

// One-shot non-streaming call that must return JSON. Used for outline
// extraction and session summaries. Tolerates code fences around the JSON.
export async function jsonCall({ system, user, maxTokens = 4096 }) {
  const anthropic = getClient();
  if (!anthropic) throw new Error('AI is not configured (missing ANTHROPIC_API_KEY)');
  const res = await anthropic.messages.create({
    model: MODEL,
    max_tokens: maxTokens,
    system,
    messages: [{ role: 'user', content: user }],
  });
  const text = res.content
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('')
    .trim();
  return parseLooseJSON(text);
}

export function parseLooseJSON(text) {
  let t = String(text).trim();
  const fence = /```(?:json)?\s*([\s\S]*?)```/.exec(t);
  if (fence) t = fence[1].trim();
  try {
    return JSON.parse(t);
  } catch {
    // Last resort: grab the outermost JSON object/array.
    const start = t.search(/[[{]/);
    if (start !== -1) {
      const open = t[start];
      const close = open === '{' ? '}' : ']';
      const end = t.lastIndexOf(close);
      if (end > start) return JSON.parse(t.slice(start, end + 1));
    }
    throw new Error('Model did not return valid JSON');
  }
}
