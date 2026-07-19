import { topicSequenceFor, titleFor } from './seed-data.js';
import { applyQuizResult, freshQueueRow, isoDate, addDays, PASS_THRESHOLD } from './spacedrep.js';

function parseJSON(text, fallback) {
  if (text == null || text === '') return fallback;
  if (typeof text === 'object') return text;
  try {
    return JSON.parse(text);
  } catch {
    return fallback;
  }
}

// Higher-level data operations shared by routes, agents, and the digest.
export class Repo {
  constructor(store) {
    this.store = store;
  }

  // ---- courses ------------------------------------------------------------

  async listCourses() {
    return this.store.list('courses');
  }

  async getCourse(id) {
    const rows = await this.store.list('courses', { where: `(Id,eq,${id})` });
    if (rows.length) return rows[0];
    const all = await this.store.list('courses');
    return all.find((c) => Number(c.id) === Number(id)) || null;
  }

  async findCourseByName(name) {
    const all = await this.store.list('courses');
    const target = String(name || '').trim().toUpperCase();
    return all.find((c) => String(c.name || '').trim().toUpperCase() === target) || null;
  }

  // Phase 1: seed a course in generic mode with a default topic sequence.
  // Idempotent — an existing course with the same name is returned untouched.
  async seedCourse(name, { term = '' } = {}) {
    const existing = await this.findCourseByName(name);
    if (existing) return { course: existing, created: false };

    const course = await this.store.create('courses', {
      name: name.trim(),
      title: titleFor(name),
      instructor: '',
      term,
      textbook_title: '',
      textbook_edition: '',
      grading_weights: '',
      outline_file_url: '',
      status: 'generic',
    });

    const sequence = topicSequenceFor(name);
    for (let i = 0; i < sequence.length; i++) {
      const topic = await this.store.create('topics', {
        course_id: course.id,
        name: sequence[i],
        week_number: i + 1,
        textbook_chapter: '',
        status: i === 0 ? 'current' : 'upcoming',
        difficulty_flag: 'normal',
      });
      await this.store.create('spaced_rep_queue', freshQueueRow(topic.id));
    }
    return { course, created: true };
  }

  // ---- Phase 2 enrichment -------------------------------------------------

  // Replaces the generic scaffold with outline-extracted data. Additive across
  // courses: only this course's topics/dates/queue rows are rebuilt.
  async applyOutlineExtraction(courseId, extraction, outlineUrl = '') {
    const course = await this.getCourse(courseId);
    if (!course) throw new Error(`Course ${courseId} not found`);

    const courseUpdate = {
      status: 'enriched',
      outline_file_url: outlineUrl || course.outline_file_url || '',
    };
    if (extraction.instructor) courseUpdate.instructor = extraction.instructor;
    if (extraction.term) courseUpdate.term = extraction.term;
    if (extraction.textbook_title) courseUpdate.textbook_title = extraction.textbook_title;
    if (extraction.textbook_edition) courseUpdate.textbook_edition = String(extraction.textbook_edition);
    if (extraction.grading_weights && typeof extraction.grading_weights === 'object') {
      courseUpdate.grading_weights = JSON.stringify(extraction.grading_weights);
    }
    if (extraction.lab_report_format) courseUpdate.lab_report_format = String(extraction.lab_report_format);
    await this.store.update('courses', course.id, courseUpdate);

    // Rebuild topics + their queue rows for this course only.
    const oldTopics = await this.store.list('topics', { where: `(course_id,eq,${course.id})` });
    const oldTopicIds = new Set(oldTopics.map((t) => t.id));
    for (const t of oldTopics) await this.store.remove('topics', t.id);
    const queue = await this.store.list('spaced_rep_queue');
    for (const q of queue) {
      if (oldTopicIds.has(Number(q.topic_id))) await this.store.remove('spaced_rep_queue', q.id);
    }

    const topics = Array.isArray(extraction.topics) ? extraction.topics : [];
    const today = isoDate();
    for (let i = 0; i < topics.length; i++) {
      const t = topics[i];
      const topic = await this.store.create('topics', {
        course_id: course.id,
        name: String(t.name || `Topic ${i + 1}`),
        week_number: Number(t.week_number) || i + 1,
        textbook_chapter: t.textbook_chapter != null ? String(t.textbook_chapter) : '',
        status: (Number(t.week_number) || i + 1) === 1 ? 'current' : 'upcoming',
        difficulty_flag: 'normal',
      });
      await this.store.create('spaced_rep_queue', freshQueueRow(topic.id, today));
    }

    // Rebuild key dates for this course only.
    const oldDates = await this.store.list('key_dates', { where: `(course_id,eq,${course.id})` });
    for (const d of oldDates) await this.store.remove('key_dates', d.id);
    const dates = Array.isArray(extraction.key_dates) ? extraction.key_dates : [];
    for (const d of dates) {
      if (!d.date) continue;
      await this.store.create('key_dates', {
        course_id: course.id,
        type: String(d.type || 'assignment'),
        title: String(d.title || d.type || 'Untitled'),
        date: String(d.date),
        weight_pct: Number(d.weight_pct) || 0,
      });
    }

    return this.getCourse(course.id);
  }

  // ---- topics -------------------------------------------------------------

  async listTopics(courseId) {
    return this.store.list('topics', courseId ? { where: `(course_id,eq,${courseId})` } : {});
  }

  async findTopicByName(name, courseId) {
    const topics = await this.listTopics(courseId);
    const target = String(name || '').trim().toLowerCase();
    return (
      topics.find((t) => String(t.name || '').trim().toLowerCase() === target) ||
      topics.find((t) => String(t.name || '').toLowerCase().includes(target) && target.length >= 4) ||
      null
    );
  }

  async setTopicStatus(topicId, status) {
    return this.store.update('topics', topicId, { status });
  }

  // ---- quizzes & spaced repetition ---------------------------------------

  async recordQuizResult({ topicId, questionsAsked, questionsCorrect, weakSubtopics = [] }) {
    const asked = Math.max(1, Number(questionsAsked) || 1);
    const correct = Math.max(0, Math.min(asked, Number(questionsCorrect) || 0));
    const scorePct = Math.round((correct / asked) * 100);
    const today = isoDate();

    const result = await this.store.create('quiz_results', {
      topic_id: topicId,
      date: today,
      questions_asked: asked,
      questions_correct: correct,
      score_pct: scorePct,
      weak_subtopics: JSON.stringify(weakSubtopics),
    });

    // Update (or create) the spaced-rep queue row for this topic.
    const queue = await this.store.list('spaced_rep_queue', { where: `(topic_id,eq,${topicId})` });
    let queueRow = queue.find((q) => Number(q.topic_id) === Number(topicId));
    if (!queueRow) queueRow = await this.store.create('spaced_rep_queue', freshQueueRow(topicId, today));
    const updated = applyQuizResult(queueRow, scorePct, today);
    await this.store.update('spaced_rep_queue', queueRow.id, updated);

    // Flag repeated sub-70% scores as a known weak point on the topic.
    const history = await this.store.list('quiz_results', { where: `(topic_id,eq,${topicId})` });
    const failures = history.filter((r) => Number(r.score_pct) < PASS_THRESHOLD).length;
    if (failures >= 2) {
      await this.store.update('topics', topicId, { difficulty_flag: 'known_weak_point' });
    }

    return { result, queue: { id: queueRow.id, ...updated }, scorePct };
  }

  async dueReviews(today = isoDate()) {
    const queue = await this.store.list('spaced_rep_queue');
    const due = queue.filter((q) => q.next_due && q.next_due <= today);
    if (!due.length) return [];
    const topics = await this.store.list('topics');
    const courses = await this.store.list('courses');
    const topicById = new Map(topics.map((t) => [Number(t.id), t]));
    const courseById = new Map(courses.map((c) => [Number(c.id), c]));
    return due
      .map((q) => {
        const topic = topicById.get(Number(q.topic_id));
        if (!topic) return null;
        const course = courseById.get(Number(topic.course_id));
        return {
          queue_id: q.id,
          topic_id: topic.id,
          topic: topic.name,
          course: course?.name || '',
          next_due: q.next_due,
          interval_days: q.interval_days,
          ease: q.ease,
          difficulty_flag: topic.difficulty_flag,
        };
      })
      .filter(Boolean);
  }

  // ---- study sessions -----------------------------------------------------

  async logStudySession({ courseId = null, topicId = null, agent, durationMin = 0, notes = '' }) {
    return this.store.create('study_sessions', {
      date: isoDate(),
      course_id: courseId,
      topic_id: topicId,
      agent_used: agent,
      duration_min: Math.round(durationMin),
      notes,
    });
  }

  // ---- aggregate views ----------------------------------------------------

  async upcomingDates(withinDays = 60, limit = 50) {
    const today = isoDate();
    const horizon = addDays(today, withinDays);
    const dates = await this.store.list('key_dates');
    const courses = await this.store.list('courses');
    const courseById = new Map(courses.map((c) => [Number(c.id), c]));
    return dates
      .filter((d) => d.date && d.date >= today && d.date <= horizon)
      .sort((a, b) => (a.date < b.date ? -1 : 1))
      .slice(0, limit)
      .map((d) => ({ ...d, course: courseById.get(Number(d.course_id))?.name || '' }));
  }

  async dashboard() {
    const [courses, topics, due, upcoming] = await Promise.all([
      this.listCourses(),
      this.store.list('topics'),
      this.dueReviews(),
      this.upcomingDates(60, 10),
    ]);
    const currentTopics = courses.map((c) => ({
      course: c.name,
      status: c.status,
      topics: topics
        .filter((t) => Number(t.course_id) === Number(c.id) && t.status === 'current')
        .map((t) => t.name),
    }));
    return {
      next_dates: upcoming.slice(0, 3),
      current_topics: currentTopics,
      due_reviews: due,
      course_count: courses.length,
    };
  }

  // Student context injected into every agent's system prompt.
  async studentContext() {
    const [courses, topics, due, upcoming, sessions] = await Promise.all([
      this.listCourses(),
      this.store.list('topics'),
      this.dueReviews(),
      this.upcomingDates(30, 12),
      this.store.list('study_sessions', { sort: '-date', limit: 10 }),
    ]);

    if (!courses.length) {
      return 'No courses are set up yet. If the student wants tutoring, gently suggest starting with the "Set Up My Courses" agent first — but still help with whatever they ask.';
    }

    const lines = [];
    for (const c of courses) {
      const cTopics = topics.filter((t) => Number(t.course_id) === Number(c.id));
      const current = cTopics.filter((t) => t.status === 'current').map((t) => t.name);
      const weak = cTopics.filter((t) => t.difficulty_flag === 'known_weak_point').map((t) => t.name);
      const weights = parseJSON(c.grading_weights, null);
      lines.push(
        `- ${c.name}${c.title ? ` (${c.title})` : ''} — mode: ${c.status || 'generic'}` +
          (c.textbook_title ? `; textbook: "${c.textbook_title}"${c.textbook_edition ? `, ${c.textbook_edition} ed.` : ''}` : '') +
          (current.length ? `; current topic(s): ${current.join('; ')}` : '') +
          (weak.length ? `; KNOWN WEAK POINTS: ${weak.join('; ')}` : '') +
          (weights ? `; grading: ${Object.entries(weights).map(([k, v]) => `${k} ${v}%`).join(', ')}` : '') +
          (c.lab_report_format ? `; instructor lab-report format: ${String(c.lab_report_format).slice(0, 300)}` : '')
      );
    }

    const dateLines = upcoming.map((d) => `- ${d.date}: ${d.course} ${d.type} — ${d.title}${d.weight_pct ? ` (${d.weight_pct}%)` : ''}`);
    const dueLines = due.map((r) => `- ${r.course}: ${r.topic}${r.difficulty_flag === 'known_weak_point' ? ' (known weak point)' : ''}`);
    const recentLines = sessions
      .slice(0, 5)
      .map((s) => `- ${s.date} [${s.agent_used}] ${String(s.notes || '').slice(0, 160)}`);

    return [
      'CURRENT COURSES:',
      ...lines,
      '',
      upcoming.length ? 'UPCOMING DATES (next 30 days):' : 'UPCOMING DATES: none recorded yet.',
      ...dateLines,
      '',
      due.length ? 'SPACED-REPETITION REVIEWS DUE TODAY:' : 'SPACED-REPETITION REVIEWS DUE TODAY: none.',
      ...dueLines,
      '',
      recentLines.length ? 'RECENT STUDY SESSIONS:' : '',
      ...recentLines,
    ]
      .filter((l) => l !== '')
      .join('\n');
  }

  // Data bundle for the weekly digest.
  async digestData() {
    const today = isoDate();
    const weekAgo = addDays(today, -7);
    const [courses, topics, sessions, quizzes, upcoming] = await Promise.all([
      this.listCourses(),
      this.store.list('topics'),
      this.store.list('study_sessions'),
      this.store.list('quiz_results'),
      this.upcomingDates(14, 50),
    ]);
    const courseById = new Map(courses.map((c) => [Number(c.id), c]));
    const topicById = new Map(topics.map((t) => [Number(t.id), t]));

    const weekSessions = sessions.filter((s) => s.date >= weekAgo && s.date <= today);
    const minutesByCourse = {};
    for (const s of weekSessions) {
      const name = courseById.get(Number(s.course_id))?.name || 'General';
      minutesByCourse[name] = (minutesByCourse[name] || 0) + (Number(s.duration_min) || 0);
    }

    const weekQuizzes = quizzes.filter((q) => q.date >= weekAgo && q.date <= today);
    const weakAreas = [];
    for (const t of topics) {
      if (t.difficulty_flag === 'known_weak_point') {
        weakAreas.push({ topic: t.name, course: courseById.get(Number(t.course_id))?.name || '' });
      }
    }
    for (const q of weekQuizzes) {
      if (Number(q.score_pct) < PASS_THRESHOLD) {
        const t = topicById.get(Number(q.topic_id));
        if (t && !weakAreas.some((w) => w.topic === t.name)) {
          weakAreas.push({ topic: t.name, course: courseById.get(Number(t.course_id))?.name || '' });
        }
      }
    }

    const coveredTopics = weekSessions
      .map((s) => topicById.get(Number(s.topic_id)))
      .filter(Boolean)
      .map((t) => ({ topic: t.name, course: courseById.get(Number(t.course_id))?.name || '' }));

    return {
      range: { from: weekAgo, to: today },
      sessions: weekSessions,
      minutesByCourse,
      coveredTopics,
      quizzes: weekQuizzes,
      weakAreas,
      upcoming,
      courses,
    };
  }
}
