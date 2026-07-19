// SM-2-lite: fixed interval ladder 1 -> 3 -> 7 -> 14 -> 30 days.
// A quiz score under 70% resets the interval to 1 day.

export const INTERVALS = [1, 3, 7, 14, 30];
export const PASS_THRESHOLD = 70;

export function nextInterval(currentIntervalDays, scorePct) {
  if (scorePct < PASS_THRESHOLD) return INTERVALS[0];
  const idx = INTERVALS.indexOf(currentIntervalDays);
  if (idx === -1) {
    // Off-ladder value (e.g. fresh row): snap to the first interval >= current, then advance.
    const next = INTERVALS.find((i) => i > currentIntervalDays);
    return next ?? INTERVALS[INTERVALS.length - 1];
  }
  return INTERVALS[Math.min(idx + 1, INTERVALS.length - 1)];
}

export function isoDate(d = new Date()) {
  return d.toISOString().slice(0, 10);
}

export function addDays(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return isoDate(d);
}

// Applies a quiz result to a queue row, returning the updated fields.
export function applyQuizResult(queueRow, scorePct, today = isoDate()) {
  const interval = nextInterval(Number(queueRow.interval_days) || 0, scorePct);
  return {
    last_reviewed: today,
    interval_days: interval,
    next_due: addDays(today, interval),
    ease: scorePct < PASS_THRESHOLD ? 'struggling' : 'ok',
  };
}

export function freshQueueRow(topicId, today = isoDate()) {
  return {
    topic_id: topicId,
    last_reviewed: null,
    next_due: today,
    interval_days: 0,
    ease: 'new',
  };
}
