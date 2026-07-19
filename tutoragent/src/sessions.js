import crypto from 'node:crypto';

// Single-user session handling: passphrase -> random token in an HTTP-only
// cookie. Conversation history lives in memory per session, per agent —
// nothing conversational is persisted except study_session summaries.

const MAX_TURNS = 40; // messages kept per agent conversation (user+assistant)

const sessions = new Map(); // token -> { createdAt, conversations, pendingImages, startedAt }

export function createSession() {
  const token = crypto.randomBytes(32).toString('hex');
  sessions.set(token, {
    createdAt: Date.now(),
    conversations: {}, // agent -> [{role, content}]
    pendingImages: {}, // agent -> [{media_type, data (base64)}]
    agentStartedAt: {}, // agent -> ms timestamp of first message (for duration logging)
  });
  return token;
}

export function getSession(token) {
  return token ? sessions.get(token) || null : null;
}

export function destroySession(token) {
  sessions.delete(token);
}

export function getConversation(session, agent) {
  if (!session.conversations[agent]) session.conversations[agent] = [];
  return session.conversations[agent];
}

export function pushTurn(session, agent, role, content) {
  const conv = getConversation(session, agent);
  conv.push({ role, content });
  if (conv.length > MAX_TURNS) conv.splice(0, conv.length - MAX_TURNS);
}

export function takePendingImages(session, agent) {
  const imgs = session.pendingImages[agent] || [];
  session.pendingImages[agent] = [];
  return imgs;
}

export function addPendingImage(session, agent, image) {
  if (!session.pendingImages[agent]) session.pendingImages[agent] = [];
  session.pendingImages[agent].push(image);
}

export function markAgentActivity(session, agent) {
  if (!session.agentStartedAt[agent]) session.agentStartedAt[agent] = Date.now();
}

export function takeAgentDuration(session, agent) {
  const started = session.agentStartedAt[agent];
  delete session.agentStartedAt[agent];
  if (!started) return 0;
  return Math.max(1, Math.round((Date.now() - started) / 60000));
}

// timing-safe passphrase comparison
export function passphraseMatches(supplied, expected) {
  if (!expected) return false;
  const a = Buffer.from(String(supplied || ''));
  const b = Buffer.from(String(expected));
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}
