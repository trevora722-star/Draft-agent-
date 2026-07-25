import { test, describe } from "node:test";
import assert from "node:assert/strict";
import {
  minDepth,
  wearRate,
  projectThreshold,
  classify,
  irregularWear,
  to32ndsFromMm,
  toMmFrom32nds,
  TREAD_STATUS,
} from "../src/lib/tread-math.js";

describe("minDepth", () => {
  const cases = [
    { reading: { outer_32nds: 8, centre_32nds: 6, inner_32nds: 7 }, expected: 6 },
    { reading: { outer_32nds: 2, centre_32nds: 2, inner_32nds: 2 }, expected: 2 },
    { reading: { outer_32nds: 0, centre_32nds: 5, inner_32nds: 5 }, expected: 0 },
  ];
  for (const { reading, expected } of cases) {
    test(`min of ${JSON.stringify(reading)} is ${expected}`, () => {
      assert.equal(minDepth(reading), expected);
    });
  }

  test("throws on missing/invalid field", () => {
    assert.throws(() => minDepth({ outer_32nds: 5, centre_32nds: 5 }));
    assert.throws(() => minDepth({ outer_32nds: -1, centre_32nds: 5, inner_32nds: 5 }));
  });
});

describe("32nds <-> mm conversion", () => {
  test("round trips within floating tolerance", () => {
    const mm = toMmFrom32nds(4);
    const back = to32ndsFromMm(mm);
    assert.ok(Math.abs(back - 4) < 1e-9);
  });

  test("3.5mm winter designation is ~4.4/32", () => {
    assert.ok(Math.abs(to32ndsFromMm(3.5) - 4.409) < 0.01);
  });
});

describe("wearRate", () => {
  test("returns null for a single reading", () => {
    assert.equal(
      wearRate([{ date: "2025-04-01", min_32nds: 8 }], [{ date: "2025-04-01", odometer_km: 10000 }]),
      null
    );
  });

  test("returns null when odometer hasn't moved", () => {
    const r = wearRate(
      [
        { date: "2025-04-01", min_32nds: 8 },
        { date: "2025-10-01", min_32nds: 7 },
      ],
      [
        { date: "2025-04-01", odometer_km: 10000 },
        { date: "2025-10-01", odometer_km: 10000 },
      ]
    );
    assert.equal(r, null);
  });

  test("computes 32nds per 1000km for two readings", () => {
    const r = wearRate(
      [
        { date: "2025-04-01", min_32nds: 10 },
        { date: "2025-10-01", min_32nds: 8 },
      ],
      [
        { date: "2025-04-01", odometer_km: 0 },
        { date: "2025-10-01", odometer_km: 4000 },
      ]
    );
    assert.ok(r);
    assert.equal(r.value, 0.5); // 2/32nds lost over 4000km = 0.5/32 per 1000km
    assert.equal(r.confidence, "low"); // only 2 readings
  });

  test("higher confidence with more readings and more distance", () => {
    const dates = ["2024-04-01", "2024-10-01", "2025-04-01", "2025-10-01", "2026-04-01"];
    const depths = [10, 9, 8, 7, 6];
    const odos = [0, 3000, 6000, 9000, 12000];
    const r = wearRate(
      dates.map((date, i) => ({ date, min_32nds: depths[i] })),
      dates.map((date, i) => ({ date, odometer_km: odos[i] }))
    );
    assert.equal(r.confidence, "high");
    assert.equal(r.readingCount, 5);
  });

  test("throws on mismatched array lengths", () => {
    assert.throws(() =>
      wearRate([{ date: "2025-01-01", min_32nds: 8 }], [])
    );
  });

  test("null for non-array input", () => {
    assert.equal(wearRate(null, null), null);
  });
});

describe("projectThreshold", () => {
  test("returns null with fewer than 2 readings", () => {
    assert.equal(projectThreshold([{ date: "2025-01-01", min_32nds: 8 }], 4), null);
  });

  test("returns null when trend is flat or increasing", () => {
    const r = projectThreshold(
      [
        { date: "2025-01-01", min_32nds: 8 },
        { date: "2025-06-01", min_32nds: 8 },
      ],
      4
    );
    assert.equal(r, null);
  });

  test("flags alreadyBelow when latest reading is already at/under target", () => {
    const r = projectThreshold(
      [
        { date: "2025-01-01", min_32nds: 6 },
        { date: "2025-06-01", min_32nds: 3 },
      ],
      4
    );
    assert.ok(r);
    assert.equal(r.alreadyBelow, true);
  });

  test("projects a future date for a clear linear decline", () => {
    // Loses 1/32 per 30 days: from 10 on day 0 to 8 on day 60. Target 4 -> 120 more days from day 60.
    const r = projectThreshold(
      [
        { date: "2025-01-01T00:00:00.000Z", min_32nds: 10 },
        { date: "2025-03-02T00:00:00.000Z", min_32nds: 8 },
      ],
      4
    );
    assert.ok(r);
    assert.equal(r.alreadyBelow, false);
    const projected = new Date(r.projectedDate);
    const latest = new Date("2025-03-02T00:00:00.000Z");
    const daysAhead = (projected - latest) / 86_400_000;
    assert.ok(Math.abs(daysAhead - 120) < 1, `expected ~120 days ahead, got ${daysAhead}`);
  });
});

describe("classify", () => {
  const regulation = { legal_min_32nds: 2, winter_designation_min_mm: 3.5 }; // ~4.4/32
  const shopConfig = { practicalReplacement32nds: 4, season: "winter" };

  // In winter season, the BC winter designation floor (~4.4/32) sits
  // above the shop's practical-replacement threshold (4/32), so it
  // preempts replace_recommended for any depth below it — a stricter
  // regulatory check dominates a shop heuristic. replace_recommended is
  // exercised separately below, outside winter season.
  const cases = [
    { depth: 10, expected: TREAD_STATUS.OK },
    { depth: 6, expected: TREAD_STATUS.MONITOR }, // within +2 of replacement(4), above winter floor(4.4)
    { depth: 4, expected: TREAD_STATUS.BELOW_WINTER_DESIGNATION }, // below 4.4/32 winter designation
    { depth: 3, expected: TREAD_STATUS.BELOW_WINTER_DESIGNATION }, // below 4.4/32 winter designation, above legal min(2)
    { depth: 2, expected: TREAD_STATUS.BELOW_LEGAL }, // at legal minimum
    { depth: 1, expected: TREAD_STATUS.BELOW_LEGAL },
  ];

  for (const { depth, expected } of cases) {
    test(`depth ${depth}/32 classifies as ${expected}`, () => {
      const result = classify({ min_32nds: depth }, regulation, shopConfig);
      assert.equal(result.status, expected);
      assert.ok(result.reasons.length > 0);
    });
  }

  test("replace_recommended triggers on its own outside winter season", () => {
    const result = classify({ min_32nds: 4 }, regulation, { practicalReplacement32nds: 4, season: "summer" });
    assert.equal(result.status, TREAD_STATUS.REPLACE_RECOMMENDED);
  });

  test("winter designation check is skipped outside winter season", () => {
    const result = classify({ min_32nds: 3 }, regulation, { practicalReplacement32nds: 4, season: "summer" });
    // 3/32 <= practicalReplacement(4) -> replace_recommended, not below_winter_designation
    assert.equal(result.status, TREAD_STATUS.REPLACE_RECOMMENDED);
  });

  test("accepts raw three-position reading and computes min itself", () => {
    const result = classify(
      { outer_32nds: 10, centre_32nds: 1, inner_32nds: 10 },
      regulation,
      shopConfig
    );
    assert.equal(result.status, TREAD_STATUS.BELOW_LEGAL);
  });

  test("uses default practical replacement threshold when shopConfig omitted", () => {
    const result = classify({ min_32nds: 4 }, regulation);
    assert.equal(result.status, TREAD_STATUS.REPLACE_RECOMMENDED);
  });
});

describe("irregularWear", () => {
  const cases = [
    {
      name: "even wear, not flagged",
      reading: { outer_32nds: 7, centre_32nds: 7, inner_32nds: 8 },
      flagged: false,
    },
    {
      name: "centre worn faster than shoulders (over-inflation candidate)",
      reading: { outer_32nds: 8, centre_32nds: 4, inner_32nds: 8 },
      flagged: true,
      reason: "centre_worn_faster_than_shoulders",
    },
    {
      name: "shoulders worn faster than centre (under-inflation candidate)",
      reading: { outer_32nds: 3, centre_32nds: 8, inner_32nds: 3 },
      flagged: true,
      reason: "shoulders_worn_faster_than_centre",
    },
    {
      name: "one shoulder worn much more than the other (alignment/cupping candidate)",
      reading: { outer_32nds: 8, centre_32nds: 7, inner_32nds: 3 },
      flagged: true,
      reason: "uneven_shoulder_wear",
    },
    {
      name: "spread exactly at tolerance boundary is flagged (>=)",
      reading: { outer_32nds: 8, centre_32nds: 5, inner_32nds: 8 }, // spread = 3, tolerance default 3
      flagged: true,
    },
    {
      name: "spread just under tolerance boundary is not flagged",
      reading: { outer_32nds: 8, centre_32nds: 5.5, inner_32nds: 8 }, // spread = 2.5 < 3
      flagged: false,
    },
  ];

  for (const c of cases) {
    test(c.name, () => {
      const result = irregularWear(c.reading);
      assert.equal(result.flagged, c.flagged);
      if (c.reason) assert.ok(result.reasons.includes(c.reason));
      assert.match(result.note, /inspect|tolerance/);
    });
  }

  test("never claims a diagnosis, only a flag", () => {
    const result = irregularWear({ outer_32nds: 8, centre_32nds: 4, inner_32nds: 8 });
    assert.match(result.note, /candidate|Not an automated diagnosis|tolerance/i);
  });
});
