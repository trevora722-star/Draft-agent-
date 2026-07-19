import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Repo } from '../src/repo.js';
import { isoDate, addDays } from '../src/spacedrep.js';

// The memory store mirrors the NocoDB store's interface, so repo logic is
// exercised the same way it runs in production.
async function freshRepo() {
  const { createStore } = await import('../src/store.js');
  const store = createStore(); // env has no NocoDB config in tests -> MemoryStore
  await store.init();
  return new Repo(store);
}

test('seedCourse creates generic course, topics, and spaced-rep rows; idempotent', async () => {
  const repo = await freshRepo();
  const { course, created } = await repo.seedCourse('CHEM 1110');
  assert.equal(created, true);
  assert.equal(course.status, 'generic');
  assert.equal(course.title, 'Chemistry for the Sciences I');

  const topics = await repo.listTopics(course.id);
  assert.equal(topics.length, 12);
  assert.equal(topics[0].status, 'current');
  assert.equal(topics[1].status, 'upcoming');

  const queue = await repo.store.list('spaced_rep_queue');
  assert.equal(queue.length, 12);

  const again = await repo.seedCourse('chem 1110');
  assert.equal(again.created, false);
});

test('four-course generic onboarding', async () => {
  const repo = await freshRepo();
  for (const name of ['BIOL 1110', 'CHEM 1110', 'PHYS 1101', 'MATH 1120']) {
    await repo.seedCourse(name);
  }
  const courses = await repo.listCourses();
  assert.equal(courses.length, 4);
  const topics = await repo.store.list('topics');
  assert.equal(topics.length, 48);
});

test('quiz results update the queue and repeated failures flag weak points', async () => {
  const repo = await freshRepo();
  const { course } = await repo.seedCourse('MATH 1120');
  const topic = (await repo.listTopics(course.id))[0];

  // Pass: fresh row (interval 0) -> 1 day
  const r1 = await repo.recordQuizResult({ topicId: topic.id, questionsAsked: 5, questionsCorrect: 5 });
  assert.equal(r1.scorePct, 100);
  assert.equal(r1.queue.interval_days, 1);
  assert.equal(r1.queue.next_due, addDays(isoDate(), 1));

  // Pass again: 1 -> 3
  const r2 = await repo.recordQuizResult({ topicId: topic.id, questionsAsked: 4, questionsCorrect: 3 });
  assert.equal(r2.scorePct, 75);
  assert.equal(r2.queue.interval_days, 3);

  // Fail: resets to 1
  const r3 = await repo.recordQuizResult({ topicId: topic.id, questionsAsked: 5, questionsCorrect: 2 });
  assert.equal(r3.scorePct, 40);
  assert.equal(r3.queue.interval_days, 1);

  // Second failure -> known_weak_point
  await repo.recordQuizResult({ topicId: topic.id, questionsAsked: 5, questionsCorrect: 1 });
  const flagged = (await repo.listTopics(course.id)).find((t) => t.id === topic.id);
  assert.equal(flagged.difficulty_flag, 'known_weak_point');
});

test('outline enrichment rebuilds topics/dates for one course without touching others', async () => {
  const repo = await freshRepo();
  const { course: chem } = await repo.seedCourse('CHEM 1110');
  const { course: math } = await repo.seedCourse('MATH 1120');

  const extraction = {
    instructor: 'Dr. Nagra',
    term: 'Fall 2026',
    textbook_title: 'Chemistry: The Central Science',
    textbook_edition: '15th',
    grading_weights: { final: 40, midterm: 25, labs: 20, quizzes: 15 },
    lab_report_format: 'Abstract under 150 words; graphs hand-drawn on graph paper.',
    topics: [
      { name: 'Matter & measurement', week_number: 1, textbook_chapter: '1' },
      { name: 'Atoms, molecules & ions', week_number: 2, textbook_chapter: '2' },
      { name: 'Stoichiometry', week_number: 3, textbook_chapter: '3' },
    ],
    key_dates: [
      { type: 'midterm', title: 'Midterm 1', date: '2026-10-15', weight_pct: 25 },
      { type: 'final', title: 'Final exam', date: '2026-12-10', weight_pct: 40 },
    ],
  };

  const updated = await repo.applyOutlineExtraction(chem.id, extraction, 'https://spaces.example/outline.pdf');
  assert.equal(updated.status, 'enriched');
  assert.equal(updated.textbook_title, 'Chemistry: The Central Science');
  assert.equal(updated.instructor, 'Dr. Nagra');
  assert.ok(updated.lab_report_format.includes('150 words'));

  const chemTopics = await repo.listTopics(chem.id);
  assert.equal(chemTopics.length, 3);
  assert.equal(chemTopics[0].textbook_chapter, '1');

  // Other course untouched (Phase 2 is additive per course).
  const mathCourse = await repo.getCourse(math.id);
  assert.equal(mathCourse.status, 'generic');
  assert.equal((await repo.listTopics(math.id)).length, 12);

  const dates = await repo.store.list('key_dates');
  assert.equal(dates.length, 2);

  // Queue reseeded for the new chem topics + original math topics.
  const queue = await repo.store.list('spaced_rep_queue');
  assert.equal(queue.length, 3 + 12);
});

test('dueReviews joins topic and course names', async () => {
  const repo = await freshRepo();
  const { course } = await repo.seedCourse('PHYS 1101');
  const due = await repo.dueReviews();
  assert.equal(due.length, 12); // fresh rows are due immediately
  assert.equal(due[0].course, 'PHYS 1101');
  assert.ok(due[0].topic.length > 0);
});

test('dashboard and digest data assemble without error', async () => {
  const repo = await freshRepo();
  const { course } = await repo.seedCourse('BIOL 1110');
  const topic = (await repo.listTopics(course.id))[0];
  await repo.logStudySession({ courseId: course.id, topicId: topic.id, agent: 'coach', durationMin: 25, notes: 'Worked on membranes; struggled with osmosis direction' });
  await repo.recordQuizResult({ topicId: topic.id, questionsAsked: 5, questionsCorrect: 2, weakSubtopics: ['osmosis'] });

  const dash = await repo.dashboard();
  assert.equal(dash.course_count, 1);
  assert.ok(Array.isArray(dash.due_reviews));

  const digest = await repo.digestData();
  assert.equal(digest.sessions.length, 1);
  assert.equal(digest.quizzes.length, 1);
  assert.ok(digest.weakAreas.some((w) => w.course === 'BIOL 1110'));
  assert.ok(digest.minutesByCourse['BIOL 1110'] === 25);
});
