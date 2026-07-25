import { test, describe } from "node:test";
import assert from "node:assert/strict";
import {
  getShopLocalParts,
  shopLocalISODate,
  isWithinQuietHours,
  addCalendarDays,
  civilDateToISO,
  addDaysFromShopToday,
  isWithinAnnualWindow,
} from "../src/lib/dates.js";

describe("getShopLocalParts", () => {
  test("converts a UTC instant to Vancouver local parts", () => {
    // 2026-01-15T20:00:00Z is noon PST (UTC-8) in Vancouver in January.
    const parts = getShopLocalParts(new Date("2026-01-15T20:00:00.000Z"), "America/Vancouver");
    assert.equal(parts.hour, 12);
    assert.equal(parts.day, 15);
  });

  test("converts a UTC instant to Toronto local parts", () => {
    // 2026-01-15T20:00:00Z is 3pm EST (UTC-5) in Toronto in January.
    const parts = getShopLocalParts(new Date("2026-01-15T20:00:00.000Z"), "America/Toronto");
    assert.equal(parts.hour, 15);
  });
});

describe("shopLocalISODate", () => {
  test("can shift the calendar date across the timezone boundary", () => {
    // 2026-01-01T02:00:00Z is still Dec 31 evening in Vancouver (UTC-8).
    const iso = shopLocalISODate(new Date("2026-01-01T02:00:00.000Z"), "America/Vancouver");
    assert.equal(iso, "2025-12-31");
  });
});

describe("isWithinQuietHours", () => {
  const shop = { timeZone: "America/Vancouver", quietHoursStart: "21:00", quietHoursEnd: "08:00" };

  test("inside a midnight-crossing window, late evening", () => {
    // 2026-01-15T06:00Z = 22:00 PST Jan 14 — inside 21:00-08:00.
    assert.equal(isWithinQuietHours(shop, new Date("2026-01-15T06:00:00.000Z")), true);
  });

  test("inside a midnight-crossing window, early morning", () => {
    // 2026-01-15T13:00Z = 05:00 PST — inside 21:00-08:00.
    assert.equal(isWithinQuietHours(shop, new Date("2026-01-15T13:00:00.000Z")), true);
  });

  test("outside the window during business hours", () => {
    // 2026-01-15T20:00Z = 12:00 PST — outside 21:00-08:00.
    assert.equal(isWithinQuietHours(shop, new Date("2026-01-15T20:00:00.000Z")), false);
  });

  test("boundary: exactly at start is inside (inclusive start)", () => {
    // 21:00 PST = 05:00Z next day
    assert.equal(
      isWithinQuietHours(shop, new Date("2026-01-15T05:00:00.000Z")),
      true
    );
  });

  test("boundary: exactly at end is outside (exclusive end)", () => {
    // 08:00 PST = 16:00Z
    assert.equal(
      isWithinQuietHours(shop, new Date("2026-01-15T16:00:00.000Z")),
      false
    );
  });

  test("same-day (non-wrapping) window", () => {
    const daytime = { timeZone: "America/Toronto", quietHoursStart: "12:00", quietHoursEnd: "13:00" };
    // 12:30 EST = 17:30Z
    assert.equal(isWithinQuietHours(daytime, new Date("2026-01-15T17:30:00.000Z")), true);
    // 14:00 EST = 19:00Z
    assert.equal(isWithinQuietHours(daytime, new Date("2026-01-15T19:00:00.000Z")), false);
  });

  test("no quiet hours configured never blocks", () => {
    assert.equal(isWithinQuietHours({ timeZone: "America/Vancouver" }, new Date()), false);
  });

  test("respects DST transition — spring forward in Toronto (2026-03-08)", () => {
    // Toronto springs forward at 2am local on 2026-03-08 (EST->EDT).
    // 06:30 UTC = 01:30 EST (before the jump, still previous offset).
    const before = new Date("2026-03-08T06:30:00.000Z");
    // 07:30 UTC = 03:30 EDT (after the jump).
    const after = new Date("2026-03-08T07:30:00.000Z");
    const nightShop = { timeZone: "America/Toronto", quietHoursStart: "21:00", quietHoursEnd: "08:00" };
    assert.equal(isWithinQuietHours(nightShop, before), true); // 01:30 is within 21:00-08:00
    assert.equal(isWithinQuietHours(nightShop, after), true); // 03:30 is within 21:00-08:00
  });

  test("respects DST transition — fall back in Toronto (2026-11-01)", () => {
    const shopTz = { timeZone: "America/Toronto", quietHoursStart: "21:00", quietHoursEnd: "08:00" };
    // Clocks fall back at 2am local; pick an instant that's 09:00 local (outside window) post-transition.
    const afterFallback = new Date("2026-11-01T14:00:00.000Z"); // 09:00 EST after fallback
    assert.equal(isWithinQuietHours(shopTz, afterFallback), false);
  });
});

describe("addCalendarDays / civilDateToISO", () => {
  test("adds days across a month boundary", () => {
    const result = addCalendarDays({ year: 2026, month: 1, day: 30 }, 5);
    assert.deepEqual(result, { year: 2026, month: 2, day: 4 });
  });

  test("adds days across a leap-year February", () => {
    const result = addCalendarDays({ year: 2028, month: 2, day: 27 }, 3);
    assert.deepEqual(result, { year: 2028, month: 3, day: 1 }); // 2028 is a leap year, Feb 29 exists
  });

  test("civilDateToISO pads single digits", () => {
    assert.equal(civilDateToISO({ year: 2026, month: 3, day: 5 }), "2026-03-05");
  });
});

describe("addDaysFromShopToday", () => {
  test("computes a deadline N days from the shop's local today", () => {
    // 2026-01-15T02:00Z is Jan 14 local in Vancouver (UTC-8).
    const deadline = addDaysFromShopToday("America/Vancouver", 14, new Date("2026-01-15T02:00:00.000Z"));
    assert.equal(deadline, "2026-01-28");
  });
});

describe("isWithinAnnualWindow", () => {
  test("simple non-wrapping window", () => {
    assert.equal(isWithinAnnualWindow("06-15", "06-01", "08-31"), true);
    assert.equal(isWithinAnnualWindow("09-01", "06-01", "08-31"), false);
  });

  test("BC winter window wraps the new year (Oct 1 - Apr 30)", () => {
    assert.equal(isWithinAnnualWindow("12-25", "10-01", "04-30"), true);
    assert.equal(isWithinAnnualWindow("02-14", "10-01", "04-30"), true);
    assert.equal(isWithinAnnualWindow("07-04", "10-01", "04-30"), false);
    assert.equal(isWithinAnnualWindow("10-01", "10-01", "04-30"), true); // inclusive start
    assert.equal(isWithinAnnualWindow("04-30", "10-01", "04-30"), true); // inclusive end
  });
});
