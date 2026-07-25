#!/usr/bin/env node
// Agent 6: rack — location assignment, consolidation suggestions,
// on-site/off-site transfers. Deterministic — no LLM.

import { createRecord, findOne, updateRecord, listRecords } from "../lib/nocodb.js";
import { append } from "../lib/ledger.js";
import { withAgentRun } from "../lib/agent-run.js";

export async function assignLocation({ shopId, tireSetId, site = "on_site", actorId = "rack-agent" }) {
  const tireSet = await findOne("tire_sets", { Id: tireSetId });
  if (!tireSet) throw new Error(`rack.assignLocation: tire_set ${tireSetId} not found`);

  const { records } = await listRecords("rack_locations", {
    where: { shop_id: shopId, site, active: true, occupied_by_set_id: { is: "null" } },
    limit: 1,
  });
  const location = records[0];
  if (!location) {
    return { assigned: false, reason: `No free ${site} rack location for shop ${shopId}` };
  }

  await updateRecord("rack_locations", location.Id, { occupied_by_set_id: tireSetId });
  await updateRecord("tire_sets", tireSetId, { rack_location_id: location.Id });
  await createRecord("movements", {
    tire_set_id: tireSetId,
    from_location_id: tireSet.rack_location_id ?? null,
    to_location_id: location.Id,
    moved_by: actorId,
    moved_at: new Date().toISOString(),
    reason: "assignment",
  });
  await append({
    shopId,
    actorType: "agent",
    actorId,
    action: "rack_assigned",
    entityType: "tire_sets",
    entityId: tireSetId,
    payload: { rack_location_id: location.Id, site },
  });

  return { assigned: true, location };
}

export async function releaseLocation({ shopId, tireSetId, actorId = "rack-agent", reason = "release" }) {
  const tireSet = await findOne("tire_sets", { Id: tireSetId });
  if (!tireSet?.rack_location_id) return { released: false, reason: "tire_set has no rack_location_id" };

  await updateRecord("rack_locations", tireSet.rack_location_id, { occupied_by_set_id: null });
  await updateRecord("tire_sets", tireSetId, { rack_location_id: null });
  await createRecord("movements", {
    tire_set_id: tireSetId,
    from_location_id: tireSet.rack_location_id,
    to_location_id: null,
    moved_by: actorId,
    moved_at: new Date().toISOString(),
    reason,
  });
  await append({
    shopId,
    actorType: "agent",
    actorId,
    action: "rack_released",
    entityType: "tire_sets",
    entityId: tireSetId,
    payload: { from_location_id: tireSet.rack_location_id, reason },
  });

  return { released: true };
}

export async function transferSite({ shopId, tireSetId, toSite, actorId = "rack-agent" }) {
  await releaseLocation({ shopId, tireSetId, actorId, reason: "site_transfer" });
  const result = await assignLocation({ shopId, tireSetId, site: toSite, actorId });
  return { ...result, transferredTo: toSite };
}

/**
 * Pure read-side analysis: which rack locations look underused enough
 * that their occupants could be consolidated into fewer locations,
 * freeing paid rack space. No writes — this just surfaces candidates
 * for rack-map.html; a human decides whether to act.
 */
export async function suggestConsolidation({ shopId, site }) {
  const where = { shop_id: shopId, active: true };
  if (site) where.site = site;
  const { records: locations } = await listRecords("rack_locations", { where, all: true });

  const underused = locations.filter((loc) => {
    const capacity = loc.capacity_sets ?? 1;
    const occupied = loc.occupied_by_set_id ? 1 : 0;
    return capacity > 1 && occupied < capacity;
  });

  const empty = locations.filter((loc) => !loc.occupied_by_set_id);

  return {
    totalLocations: locations.length,
    emptyLocations: empty.length,
    underusedLocations: underused.map((l) => ({
      id: l.Id,
      site: l.site,
      zone: l.zone,
      aisle: l.aisle,
      capacity_sets: l.capacity_sets,
      occupied: l.occupied_by_set_id ? 1 : 0,
    })),
  };
}

export async function run(context) {
  const { shopId, action, actorId = "rack-agent" } = context;
  if (!shopId || !action) throw new Error("rack.run: shopId and action are required");

  return withAgentRun({ agentKey: "rack", shopId, inputSummary: `${action}` }, async () => {
    let result;
    switch (action) {
      case "assign":
        result = await assignLocation({ shopId, tireSetId: context.tireSetId, site: context.site, actorId });
        break;
      case "release":
        result = await releaseLocation({ shopId, tireSetId: context.tireSetId, actorId });
        break;
      case "transfer":
        result = await transferSite({ shopId, tireSetId: context.tireSetId, toSite: context.toSite, actorId });
        break;
      case "suggest_consolidation":
        result = await suggestConsolidation({ shopId, site: context.site });
        break;
      default:
        throw new Error(`rack.run: unknown action "${action}"`);
    }
    return { summary: `rack:${action} → ${JSON.stringify(result).slice(0, 200)}`, recordsTouched: 1, ...result };
  });
}

export default { run };

if (import.meta.url === `file://${process.argv[1]}`) {
  const contextJson = process.argv[2];
  if (!contextJson) {
    console.error("Usage: node src/agents/06-rack.js '<json context>'");
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
