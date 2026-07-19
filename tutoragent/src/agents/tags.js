// Machine-readable tag blocks the agents emit inside their replies.
// The server parses these after each completed turn and applies side effects;
// the frontend strips them from the rendered chat.

const TAG_RE = /<(setup_courses|quiz_result|update_course)>([\s\S]*?)<\/\1>/g;

export function extractTags(text) {
  const tags = [];
  let m;
  const re = new RegExp(TAG_RE.source, 'g');
  while ((m = re.exec(text)) !== null) {
    const [, name, body] = m;
    try {
      tags.push({ name, data: JSON.parse(body.trim()) });
    } catch {
      // Malformed tag body — skip rather than crash the turn.
    }
  }
  return tags;
}

export function stripTags(text) {
  return text.replace(new RegExp(TAG_RE.source, 'g'), '').trim();
}

// Applies parsed tags to the data layer. Returns human-readable event strings
// that the server forwards to the frontend as SSE "meta" events.
export async function applyTags(repo, tags) {
  const events = [];
  for (const tag of tags) {
    try {
      if (tag.name === 'setup_courses' && Array.isArray(tag.data)) {
        for (const name of tag.data) {
          if (typeof name !== 'string' || !name.trim()) continue;
          const { course, created } = await repo.seedCourse(name);
          events.push(created ? `Course set up: ${course.name} (generic mode)` : `Course already exists: ${course.name}`);
        }
      } else if (tag.name === 'update_course' && tag.data && tag.data.course) {
        const course = await repo.findCourseByName(tag.data.course);
        if (!course) {
          events.push(`Could not find course "${tag.data.course}" to update`);
          continue;
        }
        const allowed = ['textbook_title', 'textbook_edition', 'instructor', 'term'];
        const update = {};
        for (const key of allowed) {
          if (tag.data[key] != null) update[key] = String(tag.data[key]);
        }
        if (Object.keys(update).length) {
          await repo.store.update('courses', course.id, update);
          events.push(`Updated ${course.name}: ${Object.keys(update).join(', ')}`);
        }
      } else if (tag.name === 'quiz_result' && tag.data) {
        const { topic, course, questions_asked, questions_correct, weak_subtopics } = tag.data;
        let courseRow = course ? await repo.findCourseByName(course) : null;
        const topicRow = await repo.findTopicByName(topic, courseRow?.id);
        if (!topicRow) {
          events.push(`Quiz recorded topic "${topic}" not found — result not saved`);
          continue;
        }
        const { scorePct, queue } = await repo.recordQuizResult({
          topicId: topicRow.id,
          questionsAsked: questions_asked,
          questionsCorrect: questions_correct,
          weakSubtopics: Array.isArray(weak_subtopics) ? weak_subtopics : [],
        });
        events.push(`Quiz saved: ${topicRow.name} — ${scorePct}% (next review ${queue.next_due})`);
      }
    } catch (err) {
      events.push(`Failed to apply ${tag.name}: ${err.message}`);
    }
  }
  return events;
}
