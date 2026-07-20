// Platform-agnostic request handlers, shared by the Express server
// (DigitalOcean droplet) and the Netlify Functions adapter. Conversation
// history is held by the client and sent with each request, so these handlers
// are fully stateless — nothing conversational is stored server-side except
// the study_session summaries.

import { getAgent } from './agents/prompts.js';
import { extractTags, applyTags } from './agents/tags.js';
import { streamAgentTurn, jsonCall } from './claude.js';
import { extractPdfText, extractOutline } from './pdf-extract.js';
import { uploadToSpaces } from './spaces.js';
import { features } from './config.js';

const MAX_HISTORY = 40;

// Client history entry: {role: 'user'|'assistant', content: string | [
//   {type: 'text', text} | {type: 'image', media_type, data}
// ]} -> Anthropic messages format.
export function normalizeHistory(history) {
  if (!Array.isArray(history)) return [];
  const out = [];
  for (const m of history.slice(-MAX_HISTORY)) {
    if (!m || (m.role !== 'user' && m.role !== 'assistant')) continue;
    if (typeof m.content === 'string') {
      if (m.content.trim()) out.push({ role: m.role, content: m.content.slice(0, 30000) });
      continue;
    }
    if (!Array.isArray(m.content)) continue;
    const blocks = [];
    for (const b of m.content) {
      if (b?.type === 'text' && typeof b.text === 'string' && b.text.trim()) {
        blocks.push({ type: 'text', text: b.text.slice(0, 30000) });
      } else if (b?.type === 'image' && typeof b.data === 'string' && b.data.length < 8_000_000) {
        const mt = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(b.media_type)
          ? b.media_type : 'image/jpeg';
        blocks.push({ type: 'image', source: { type: 'base64', media_type: mt, data: b.data } });
      }
    }
    if (blocks.length) out.push({ role: m.role, content: blocks });
  }
  return out;
}

// Async generator: yields {type:'text',text} chunks, then optional
// {type:'meta',events}, then {type:'done'}. Errors yield {type:'error',error}.
export async function* agentTurnEvents(repo, { agentKey, message, history }) {
  const agent = getAgent(agentKey);
  if (!agent) return yield { type: 'error', error: `Unknown agent: ${agentKey}` };
  if (!features.ai) return yield { type: 'error', error: 'AI is not configured (missing ANTHROPIC_API_KEY).' };
  const userText = String(message || '').slice(0, 20000);
  if (!userText.trim()) return yield { type: 'error', error: 'Empty message.' };

  try {
    const messages = normalizeHistory(history);
    // The new user turn comes last; the client includes any pending photo as
    // image blocks inside the final history entry, so `message` here is just
    // the text (also used for validation/logging).
    if (!messages.length || messages[messages.length - 1].role !== 'user') {
      messages.push({ role: 'user', content: userText });
    }

    const studentContext = await repo.studentContext().catch((e) => `(context unavailable: ${e.message})`);
    const system = `${agent.system}\n\nSTUDENT CONTEXT (live from the database, do not show verbatim):\n${studentContext}`;

    const queue = [];
    let notify = null;
    let finished = false;
    let failure = null;

    const streamDone = streamAgentTurn({
      system,
      messages,
      onText: (text) => {
        queue.push(text);
        notify?.();
      },
    }).then(
      (full) => { finished = true; notify?.(); return full; },
      (err) => { failure = err; finished = true; notify?.(); }
    );

    while (!finished || queue.length) {
      if (!queue.length) {
        await new Promise((resolve) => { notify = resolve; });
        notify = null;
        continue;
      }
      yield { type: 'text', text: queue.splice(0).join('') };
    }
    const fullText = await streamDone;
    if (failure) throw failure;

    const tags = extractTags(fullText || '');
    if (tags.length) {
      const events = await applyTags(repo, tags);
      if (events.length) yield { type: 'meta', events };
    }
    yield { type: 'done' };
  } catch (err) {
    console.error('[agent]', err);
    yield { type: 'error', error: err.message };
  }
}

export async function handleOutlineUpload(repo, { buffer, filename, courseName }) {
  if (!features.ai) return { status: 503, json: { error: 'AI is not configured — cannot extract the outline.' } };
  const pdfText = await extractPdfText(buffer);
  if (pdfText.length < 100) {
    return { status: 422, json: { error: 'Could not read text from that PDF (is it a scan? try a text-based PDF).' } };
  }
  const extraction = await extractOutline(pdfText);

  const resolvedName = String(courseName || extraction.course_name || '').trim();
  if (!resolvedName) {
    return { status: 422, json: { error: 'Could not tell which course this outline is for — pick the course and re-upload.' } };
  }
  let course = await repo.findCourseByName(resolvedName);
  if (!course) ({ course } = await repo.seedCourse(resolvedName));

  const outlineUrl = await uploadToSpaces('outlines', filename || 'outline.pdf', buffer, 'application/pdf').catch((e) => {
    console.warn('[spaces] outline upload failed:', e.message);
    return '';
  });

  const updated = await repo.applyOutlineExtraction(course.id, extraction, outlineUrl);
  return {
    status: 200,
    json: {
      ok: true,
      course: updated.name,
      status: updated.status,
      topics: extraction.topics.length,
      key_dates: extraction.key_dates.length,
      textbook: extraction.textbook_title || null,
      grading_weights: extraction.grading_weights || null,
      outline_file_url: outlineUrl || null,
    },
  };
}

// Archives a work photo to Spaces. The client keeps the base64 itself and
// attaches it to the next chat turn, so this endpoint is archival only.
export async function handleWorkPhoto({ buffer, filename, mimetype }) {
  const allowed = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
  if (!allowed.includes(mimetype)) {
    return { status: 400, json: { error: 'Photos must be JPEG, PNG, GIF, or WebP.' } };
  }
  const url = await uploadToSpaces('work-photos', filename || 'photo.jpg', buffer, mimetype).catch((e) => {
    console.warn('[spaces] photo upload failed:', e.message);
    return '';
  });
  return { status: 200, json: { ok: true, url: url || null } };
}

export async function handleSessionClose(repo, { agentKey, history, durationMin }) {
  const agent = getAgent(agentKey);
  if (!agent) return { status: 400, json: { error: 'Unknown agent.' } };
  const conv = normalizeHistory(history);
  if (!conv.length) return { status: 200, json: { ok: true, logged: false, reason: 'No conversation to summarize.' } };

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
    agent: agentKey,
    durationMin: Math.max(1, Number(durationMin) || 1),
    notes,
  });

  for (const name of summary.topics_covered || []) {
    const t = await repo.findTopicByName(name, course?.id);
    if (t && t.status !== 'covered') await repo.setTopicStatus(t.id, 'covered');
  }

  return { status: 200, json: { ok: true, logged: true, notes, duration_min: Math.max(1, Number(durationMin) || 1) } };
}

export function sseSerialize(event) {
  return `data: ${JSON.stringify(event)}\n\n`;
}
