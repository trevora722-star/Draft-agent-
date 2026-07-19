import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractTags, stripTags, applyTags } from '../src/agents/tags.js';
import { Repo } from '../src/repo.js';

async function freshRepo() {
  const { createStore } = await import('../src/store.js');
  const store = createStore();
  await store.init();
  return new Repo(store);
}

test('extractTags parses setup_courses and quiz_result blocks', () => {
  const text = `Great, let's get you set up!
<setup_courses>["CHEM 1110", "MATH 1120"]</setup_courses>
All done. Also here's a quiz wrap-up:
<quiz_result>{"topic": "Limits & limit laws", "course": "MATH 1120", "questions_asked": 5, "questions_correct": 3, "weak_subtopics": ["one-sided limits"]}</quiz_result>`;
  const tags = extractTags(text);
  assert.equal(tags.length, 2);
  assert.deepEqual(tags[0].data, ['CHEM 1110', 'MATH 1120']);
  assert.equal(tags[1].data.questions_asked, 5);

  const stripped = stripTags(text);
  assert.ok(!stripped.includes('<setup_courses>'));
  assert.ok(!stripped.includes('<quiz_result>'));
  assert.ok(stripped.includes("let's get you set up"));
});

test('malformed tag bodies are skipped, not fatal', () => {
  const tags = extractTags('<quiz_result>{not json}</quiz_result> hi');
  assert.equal(tags.length, 0);
});

test('applyTags seeds courses and records quiz results end-to-end', async () => {
  const repo = await freshRepo();
  const events1 = await applyTags(repo, extractTags('<setup_courses>["CHEM 1110", "MATH 1120"]</setup_courses>'));
  assert.equal(events1.length, 2);
  assert.equal((await repo.listCourses()).length, 2);

  const events2 = await applyTags(
    repo,
    extractTags('<quiz_result>{"topic": "Limits & limit laws", "course": "MATH 1120", "questions_asked": 4, "questions_correct": 2, "weak_subtopics": ["squeeze theorem"]}</quiz_result>')
  );
  assert.equal(events2.length, 1);
  assert.match(events2[0], /50%/);

  const results = await repo.store.list('quiz_results');
  assert.equal(results.length, 1);
  assert.equal(results[0].score_pct, 50);

  const events3 = await applyTags(
    repo,
    extractTags('<update_course>{"course": "CHEM 1110", "textbook_title": "Chemistry: The Central Science", "textbook_edition": "15th"}</update_course>')
  );
  assert.match(events3[0], /textbook_title/);
  const chem = await repo.findCourseByName('CHEM 1110');
  assert.equal(chem.textbook_title, 'Chemistry: The Central Science');
});
