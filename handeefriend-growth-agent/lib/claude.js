import Anthropic from '@anthropic-ai/sdk';

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

export const MODEL = 'claude-sonnet-4-20250514';

export async function callClaude({
  systemPrompt,
  userContent,
  maxTokens = 4000,
  thinkingEnabled = false,
}) {
  const params = {
    model: MODEL,
    max_tokens: maxTokens,
    system: systemPrompt,
    messages: [{ role: 'user', content: userContent }],
  };

  if (thinkingEnabled) {
    params.thinking = { type: 'enabled', budget_tokens: 8000 };
    params.max_tokens = Math.max(maxTokens, 12000);
  }

  const response = await client.messages.create(params);

  const text = response.content
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('');

  return text;
}

export async function callClaudeJSON({
  systemPrompt,
  userContent,
  maxTokens = 4000,
  thinkingEnabled = false,
}) {
  const result = await callClaude({
    systemPrompt:
      systemPrompt +
      '\n\nRESPOND ONLY WITH VALID JSON. No markdown, no backticks, no preamble.',
    userContent,
    maxTokens,
    thinkingEnabled,
  });

  const cleaned = result
    .trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/i, '')
    .trim();

  try {
    return JSON.parse(cleaned);
  } catch (e) {
    const start = cleaned.indexOf('{');
    const startArr = cleaned.indexOf('[');
    const first =
      start === -1 ? startArr : startArr === -1 ? start : Math.min(start, startArr);
    const lastObj = cleaned.lastIndexOf('}');
    const lastArr = cleaned.lastIndexOf(']');
    const last = Math.max(lastObj, lastArr);
    if (first !== -1 && last !== -1) {
      return JSON.parse(cleaned.slice(first, last + 1));
    }
    throw new Error(`Claude returned non-JSON: ${result.slice(0, 300)}`);
  }
}
