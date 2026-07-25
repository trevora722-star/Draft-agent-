// Per-shop timezone date handling. A `Date` is always a UTC instant;
// every "what time is it for this shop" question goes through here so
// no code path silently uses server-local time (CLAUDE.md constraint
// #8). Uses Node's built-in Intl (ICU) — no extra dependency, and it
// applies real IANA DST rules for whatever timezone a shop configures.

/**
 * Break a UTC instant into shop-local calendar/clock parts.
 * @param {Date} date
 * @param {string} timeZone IANA zone, e.g. "America/Vancouver"
 */
export function getShopLocalParts(date, timeZone) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    weekday: "short",
  });
  const parts = Object.fromEntries(formatter.formatToParts(date).map((p) => [p.type, p.value]));
  const weekdayMap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    second: Number(parts.second),
    weekday: weekdayMap[parts.weekday],
  };
}

/** "YYYY-MM-DD" for the shop's local calendar date at this instant. */
export function shopLocalISODate(date, timeZone) {
  const { year, month, day } = getShopLocalParts(date, timeZone);
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function parseHHMM(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) {
    throw new Error(`dates.js: invalid HH:MM value "${hhmm}"`);
  }
  return h * 60 + m;
}

/**
 * Is `date` (a UTC instant, defaults to now) within the shop's quiet
 * hours, expressed as local "HH:MM" strings? Handles windows that cross
 * midnight (e.g. 21:00–08:00) and DST transitions correctly because it
 * always re-derives local wall-clock minutes from the instant via ICU.
 */
export function isWithinQuietHours({ timeZone, quietHoursStart, quietHoursEnd }, date = new Date()) {
  if (!timeZone) throw new Error("isWithinQuietHours: timeZone is required");
  if (!quietHoursStart || !quietHoursEnd) return false; // no quiet hours configured

  const { hour, minute } = getShopLocalParts(date, timeZone);
  const nowMinutes = hour * 60 + minute;
  const startMinutes = parseHHMM(quietHoursStart);
  const endMinutes = parseHHMM(quietHoursEnd);

  if (startMinutes === endMinutes) return false; // zero-width window = disabled
  if (startMinutes < endMinutes) {
    // Same-day window, e.g. 12:00–13:00
    return nowMinutes >= startMinutes && nowMinutes < endMinutes;
  }
  // Crosses midnight, e.g. 21:00–08:00
  return nowMinutes >= startMinutes || nowMinutes < endMinutes;
}

/** Pure calendar-day arithmetic on a {year,month,day} civil date — no timezone involved once you have the civil date. */
export function addCalendarDays({ year, month, day }, days) {
  const d = new Date(Date.UTC(year, month - 1, day));
  d.setUTCDate(d.getUTCDate() + days);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
}

export function civilDateToISO({ year, month, day }) {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function isoToCivilDate(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return { year, month, day };
}

/**
 * Add N calendar days to a shop's *current local calendar date* and
 * return the resulting ISO date string — used for statutory deadline
 * calculation (e.g. "14 days from today" for a dormancy notice).
 */
export function addDaysFromShopToday(timeZone, days, from = new Date()) {
  const today = getShopLocalParts(from, timeZone);
  return civilDateToISO(addCalendarDays(today, days));
}

/**
 * Is a given month/day (e.g. today, or a target date) within an annual
 * window that may wrap the new year, such as a provincial winter-tire
 * season (Oct 1–Apr 30)? Bounds are inclusive, given as "MM-DD".
 */
export function isWithinAnnualWindow(monthDay, windowStartMMDD, windowEndMMDD) {
  const toNum = (md) => {
    const [m, d] = md.split("-").map(Number);
    return m * 100 + d;
  };
  const target = toNum(monthDay);
  const start = toNum(windowStartMMDD);
  const end = toNum(windowEndMMDD);
  if (start <= end) return target >= start && target <= end;
  return target >= start || target <= end; // wraps the new year
}
