// Pure, deterministic tread-depth math. No I/O, no LLM calls — see
// CLAUDE.md constraint #6. Everything here is unit tested with
// boundary-value tables in test/tread-math.test.js.
//
// Depth is always stored/passed as an integer number of 32nds of an
// inch. Millimetre values are a display-time conversion only — never
// store a converted float (see CLAUDE.md).

import { DEFAULT_THRESHOLDS } from "../config/thresholds.js";

const MM_PER_32ND = 25.4 / 32;

export function to32ndsFromMm(mm) {
  return mm / MM_PER_32ND;
}

export function toMmFrom32nds(value32nds) {
  return value32nds * MM_PER_32ND;
}

/**
 * Lowest of the three gauge positions for a single tire reading.
 * @param {{outer_32nds:number, centre_32nds:number, inner_32nds:number}} reading
 */
export function minDepth(reading) {
  const { outer_32nds, centre_32nds, inner_32nds } = reading;
  for (const [name, v] of Object.entries({ outer_32nds, centre_32nds, inner_32nds })) {
    if (typeof v !== "number" || !Number.isFinite(v) || v < 0) {
      throw new Error(`minDepth: invalid ${name}: ${v}`);
    }
  }
  return Math.min(outer_32nds, centre_32nds, inner_32nds);
}

function confidenceFromSample(count, span) {
  if (count >= 5 && span >= 5000) return "high";
  if (count >= 3 && span >= 2000) return "medium";
  return "low";
}

/**
 * Wear rate in 32nds lost per 1000 km, from paired dated tread readings
 * and paired odometer readings (same length, same order — each index is
 * one shop visit). Returns null rather than guessing from a single
 * reading or a zero/negative distance span.
 *
 * @param {{date:string, min_32nds:number}[]} readings
 * @param {{date:string, odometer_km:number}[]} odometerReadings
 * @returns {{value:number, confidence:string, readingCount:number, kmDriven:number}|null}
 */
export function wearRate(readings, odometerReadings) {
  if (!Array.isArray(readings) || !Array.isArray(odometerReadings)) return null;
  if (readings.length !== odometerReadings.length) {
    throw new Error("wearRate: readings and odometerReadings must be the same length and paired by index");
  }

  const pairs = readings
    .map((r, i) => ({
      date: r.date,
      min_32nds: r.min_32nds,
      odometer_km: odometerReadings[i]?.odometer_km,
    }))
    .filter(
      (p) =>
        typeof p.min_32nds === "number" &&
        typeof p.odometer_km === "number" &&
        p.date !== undefined &&
        p.date !== null
    )
    .sort((a, b) => new Date(a.date) - new Date(b.date));

  if (pairs.length < DEFAULT_THRESHOLDS.minReadingsForProjection) return null;

  const first = pairs[0];
  const last = pairs[pairs.length - 1];
  const kmDriven = last.odometer_km - first.odometer_km;
  if (kmDriven <= 0) return null;

  const depthLost32nds = first.min_32nds - last.min_32nds;
  const value = (depthLost32nds / kmDriven) * 1000;

  return {
    value,
    confidence: confidenceFromSample(pairs.length, kmDriven),
    readingCount: pairs.length,
    kmDriven,
  };
}

/**
 * Project the calendar date a tire will cross a given depth threshold,
 * from a chronological series of dated tread readings, using a linear
 * least-squares fit of depth over time. Returns null when there isn't
 * enough data, or when the fitted trend isn't decreasing (so no future
 * crossing can be defensibly predicted).
 *
 * @param {{date:string, min_32nds:number}[]} readings
 * @param {number} targetDepth32nds
 * @returns {{projectedDate:string, confidence:string, readingCount:number, alreadyBelow:boolean}|null}
 */
export function projectThreshold(readings, targetDepth32nds) {
  if (!Array.isArray(readings)) return null;
  const points = readings
    .filter((r) => typeof r.min_32nds === "number" && r.date)
    .map((r) => ({ t: new Date(r.date).getTime(), depth: r.min_32nds }))
    .sort((a, b) => a.t - b.t);

  if (points.length < DEFAULT_THRESHOLDS.minReadingsForProjection) return null;

  const latest = points[points.length - 1];
  if (latest.depth <= targetDepth32nds) {
    return {
      projectedDate: new Date(latest.t).toISOString(),
      confidence: confidenceFromSample(points.length, latest.t - points[0].t),
      readingCount: points.length,
      alreadyBelow: true,
    };
  }

  // Least-squares linear regression: depth = a + b*t (t in days since first reading)
  const t0 = points[0].t;
  const days = points.map((p) => (p.t - t0) / 86_400_000);
  const n = points.length;
  const meanT = days.reduce((s, v) => s + v, 0) / n;
  const meanD = points.reduce((s, p) => s + p.depth, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i++) {
    num += (days[i] - meanT) * (points[i].depth - meanD);
    den += (days[i] - meanT) ** 2;
  }
  const slopePerDay = den === 0 ? 0 : num / den; // 32nds per day

  if (slopePerDay >= 0) return null; // flat or increasing trend — can't project a future crossing

  const daysUntilThreshold = (targetDepth32nds - latest.depth) / slopePerDay; // both negative -> positive days
  if (!Number.isFinite(daysUntilThreshold) || daysUntilThreshold < 0) return null;

  const projectedMs = latest.t + daysUntilThreshold * 86_400_000;

  return {
    projectedDate: new Date(projectedMs).toISOString(),
    confidence: confidenceFromSample(n, days[n - 1] - days[0]),
    readingCount: n,
    alreadyBelow: false,
  };
}

const STATUS = {
  OK: "ok",
  MONITOR: "monitor",
  REPLACE_RECOMMENDED: "replace_recommended",
  BELOW_WINTER_DESIGNATION: "below_winter_designation",
  BELOW_LEGAL: "below_legal",
};

/**
 * Classify a single reading against provincial regulation config and
 * shop-configurable thresholds. Returns the most severe applicable
 * status plus every reason that matched (not just the winning one).
 *
 * @param {{outer_32nds?:number, centre_32nds?:number, inner_32nds?:number, min_32nds?:number}} reading
 * @param {{legal_min_32nds:number, winter_designation_min_mm?:number}} regulation
 * @param {{practicalReplacement32nds?:number, season?: "winter"|"summer"|"all_season"}} [shopConfig]
 */
export function classify(reading, regulation, shopConfig = {}) {
  const depth = typeof reading.min_32nds === "number" ? reading.min_32nds : minDepth(reading);
  const practicalReplacement =
    shopConfig.practicalReplacement32nds ?? DEFAULT_THRESHOLDS.practicalReplacement32nds;

  const reasons = [];
  let status = STATUS.OK;

  if (depth <= regulation.legal_min_32nds) {
    status = STATUS.BELOW_LEGAL;
    reasons.push(
      `Tread depth ${depth}/32" is at or below the legal minimum of ${regulation.legal_min_32nds}/32".`
    );
  }

  if (
    shopConfig.season === "winter" &&
    regulation.winter_designation_min_mm !== undefined &&
    regulation.winter_designation_min_mm !== null
  ) {
    const winterMin32nds = to32ndsFromMm(regulation.winter_designation_min_mm);
    if (depth < winterMin32nds) {
      reasons.push(
        `Tread depth ${depth}/32" is below the ${regulation.winter_designation_min_mm}mm winter-tire designation minimum.`
      );
      if (status !== STATUS.BELOW_LEGAL) status = STATUS.BELOW_WINTER_DESIGNATION;
    }
  }

  if (status === STATUS.OK && depth <= practicalReplacement) {
    status = STATUS.REPLACE_RECOMMENDED;
    reasons.push(
      `Tread depth ${depth}/32" is at or below the shop's practical replacement threshold of ${practicalReplacement}/32".`
    );
  } else if (status === STATUS.OK && depth <= practicalReplacement + 2) {
    status = STATUS.MONITOR;
    reasons.push(
      `Tread depth ${depth}/32" is approaching the shop's practical replacement threshold of ${practicalReplacement}/32".`
    );
  }

  if (reasons.length === 0) {
    reasons.push(`Tread depth ${depth}/32" is within normal range.`);
  }

  return { status, reasons, depth32nds: depth };
}

/**
 * Flags a shoulder-vs-centre spread beyond tolerance for a tech to
 * physically inspect. This is a flag, never a diagnosis — see
 * CLAUDE.md and the spec's irregular-wear note.
 *
 * @param {{outer_32nds:number, centre_32nds:number, inner_32nds:number}} reading
 * @param {number} [tolerance32nds]
 */
export function irregularWear(reading, tolerance32nds = DEFAULT_THRESHOLDS.irregularWearSpread32nds) {
  const { outer_32nds, centre_32nds, inner_32nds } = reading;
  const shoulderAvg = (outer_32nds + inner_32nds) / 2;
  const centreVsShoulder = centre_32nds - shoulderAvg;
  const shoulderSpread = Math.abs(outer_32nds - inner_32nds);
  const spread32nds = Math.max(Math.abs(centreVsShoulder), shoulderSpread);

  const reasons = [];
  let flagged = false;

  if (centreVsShoulder <= -tolerance32nds) {
    flagged = true;
    reasons.push("centre_worn_faster_than_shoulders");
  }
  if (centreVsShoulder >= tolerance32nds) {
    flagged = true;
    reasons.push("shoulders_worn_faster_than_centre");
  }
  if (shoulderSpread >= tolerance32nds) {
    flagged = true;
    reasons.push("uneven_shoulder_wear");
  }

  return {
    flagged,
    spread32nds,
    reasons,
    note: flagged
      ? "Uneven wear pattern detected — flag for tech inspection (possible inflation, alignment, or cupping cause). Not an automated diagnosis."
      : "Wear pattern within tolerance.",
  };
}

export const TREAD_STATUS = STATUS;
