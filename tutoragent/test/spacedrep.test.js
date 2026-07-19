import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextInterval, applyQuizResult, addDays, freshQueueRow, INTERVALS } from '../src/spacedrep.js';

test('interval ladder advances 1 -> 3 -> 7 -> 14 -> 30 on passing scores', () => {
  assert.equal(nextInterval(1, 85), 3);
  assert.equal(nextInterval(3, 70), 7);
  assert.equal(nextInterval(7, 100), 14);
  assert.equal(nextInterval(14, 90), 30);
  assert.equal(nextInterval(30, 90), 30); // caps at 30
});

test('sub-70% score resets interval to 1', () => {
  for (const i of INTERVALS) assert.equal(nextInterval(i, 69), 1);
  assert.equal(nextInterval(30, 0), 1);
});

test('fresh (off-ladder) rows advance to the first interval', () => {
  assert.equal(nextInterval(0, 80), 1);
  assert.equal(nextInterval(0, 50), 1);
});

test('applyQuizResult computes next_due from today + interval', () => {
  const row = freshQueueRow(42, '2026-09-01');
  const pass = applyQuizResult({ ...row, interval_days: 3 }, 80, '2026-09-01');
  assert.equal(pass.interval_days, 7);
  assert.equal(pass.next_due, '2026-09-08');
  assert.equal(pass.ease, 'ok');

  const fail = applyQuizResult({ ...row, interval_days: 14 }, 40, '2026-09-01');
  assert.equal(fail.interval_days, 1);
  assert.equal(fail.next_due, '2026-09-02');
  assert.equal(fail.ease, 'struggling');
});

test('addDays crosses month boundaries', () => {
  assert.equal(addDays('2026-08-30', 3), '2026-09-02');
  assert.equal(addDays('2026-12-31', 1), '2027-01-01');
});
