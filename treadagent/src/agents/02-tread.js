#!/usr/bin/env node
// Agent 2: tread — reading validation, wear-rate math, threshold flags,
// projection to the practical (~3mm) and legal (1.6mm) thresholds.
// Deterministic — no LLM; all math delegates to lib/tread-math.js.

import { createRecord, findOne, listRecords } from "../lib/nocodb.js";
import { append } from "../lib/ledger.js";
import { withAgentRun } from "../lib/agent-run.js";
import { minDepth, classify, irregularWear, wearRate, projectThreshold, to32ndsFromMm } from "../lib/tread-math.js";

const PRACTICAL_MM_TARGET = 3;

async function priorReadingsFor(tireId) {
  const { records } = await listRecords("tread_readings", {
    where: { tire_id: tireId },
    sort: "reading_date",
    all: true,
  });
  return records;
}

export async function run(context) {
  const { shopId, tireId, reading, regulation, shopConfig = {}, actorId = "tread-agent" } = context;
  if (!shopId || !tireId || !reading || !regulation) {
    throw new Error("tread.run: shopId, tireId, reading, regulation are required");
  }

  return withAgentRun(
    { agentKey: "tread", shopId, inputSummary: `reading for tire ${tireId}` },
    async () => {
      const tire = await findOne("tires", { Id: tireId });
      if (!tire) throw new Error(`tread.run: tire ${tireId} not found`);

      const depth32nds = minDepth(reading);
      const classification = classify({ min_32nds: depth32nds }, regulation, shopConfig);
      const irregular = irregularWear(reading);

      const flagged = classification.status !== "ok" || irregular.flagged;
      const flagReason = [...classification.reasons, ...(irregular.flagged ? [irregular.note] : [])].join(" ");

      const readingRow = await createRecord("tread_readings", {
        tire_id: tireId,
        reading_date: reading.reading_date ?? new Date().toISOString().slice(0, 10),
        outer_32nds: reading.outer_32nds,
        centre_32nds: reading.centre_32nds,
        inner_32nds: reading.inner_32nds,
        min_32nds: depth32nds,
        measured_by: reading.measured_by ?? "",
        gauge_id: reading.gauge_id ?? "",
        odometer_km: reading.odometer_km ?? null,
        photo_ref: reading.photo_ref ?? "",
        flagged,
        flag_reason: flagReason,
      });

      await append({
        shopId,
        actorType: "staff",
        actorId,
        action: "tread_reading_recorded",
        entityType: "tread_readings",
        entityId: readingRow.Id,
        payload: { tire_id: tireId, min_32nds: depth32nds, status: classification.status, flagged },
      });

      // Wear-rate + projection, from full history including this reading.
      const history = await priorReadingsFor(tireId);
      const datedReadings = history
        .filter((r) => r.min_32nds !== null && r.min_32nds !== undefined)
        .map((r) => ({ date: r.reading_date, min_32nds: r.min_32nds }));
      const odometerReadings = history.map((r) => ({ date: r.reading_date, odometer_km: r.odometer_km }));

      const rate = wearRate(datedReadings, odometerReadings);
      const projected3mm = projectThreshold(datedReadings, to32ndsFromMm(PRACTICAL_MM_TARGET));
      const projectedLegalMin = projectThreshold(datedReadings, regulation.legal_min_32nds);

      let projectionRow = null;
      if (rate || projected3mm || projectedLegalMin) {
        projectionRow = await createRecord("wear_projections", {
          tire_id: tireId,
          computed_at: new Date().toISOString(),
          wear_per_1000km_32nds: rate?.value ?? null,
          seasons_remaining: null, // requires a per-shop seasonal mileage assumption — left to agent 3/8 context, not computed here
          projected_date_3mm: projected3mm?.alreadyBelow ? null : projected3mm?.projectedDate ?? null,
          projected_date_legal_min: projectedLegalMin?.alreadyBelow ? null : projectedLegalMin?.projectedDate ?? null,
          confidence: rate?.confidence ?? "low",
        });
      }

      return {
        summary: `Tire ${tireId}: ${depth32nds}/32" — ${classification.status}${flagged ? " (flagged)" : ""}`,
        recordsTouched: 1 + (projectionRow ? 1 : 0),
        reading: readingRow,
        classification,
        irregularWear: irregular,
        wearRate: rate,
        projection: projectionRow,
      };
    }
  );
}

export default { run };

if (import.meta.url === `file://${process.argv[1]}`) {
  const contextJson = process.argv[2];
  if (!contextJson) {
    console.error("Usage: node src/agents/02-tread.js '<json context>'");
    process.exitCode = 1;
  } else {
    run(JSON.parse(contextJson))
      .then((r) => console.log(JSON.stringify(r, null, 2)))
      .catch((err) => {
        console.error(err);
        process.exitCode = 1;
      });
  }
}
