#!/usr/bin/env node
// Agent 1: intake — set registration, wheels-vs-rubber-only, size/DOT
// capture, rack assignment, consent capture. Deterministic — no LLM.

import { createRecord, findOne, updateRecord, listRecords } from "../lib/nocodb.js";
import { append } from "../lib/ledger.js";
import { withAgentRun } from "../lib/agent-run.js";

const POSITIONS = ["LF", "RF", "LR", "RR", "spare"];

async function findOrCreateCustomer(shopId, customerInput) {
  if (customerInput.id) {
    const existing = await findOne("customers", { Id: customerInput.id });
    if (!existing) throw new Error(`intake: customer ${customerInput.id} not found`);
    return existing;
  }
  if (customerInput.phone_e164) {
    const existing = await findOne("customers", {
      shop_id: shopId,
      phone_e164: customerInput.phone_e164,
    });
    if (existing) return existing;
  }
  return createRecord("customers", {
    shop_id: shopId,
    first_name: customerInput.first_name,
    last_name: customerInput.last_name,
    phone_e164: customerInput.phone_e164,
    email: customerInput.email,
    preferred_channel: customerInput.preferred_channel ?? "sms",
    preferred_language: customerInput.preferred_language ?? "en",
    consent_type: customerInput.consent_type ?? "none",
    consent_source: customerInput.consent_source ?? "",
    consent_timestamp: customerInput.consent_type && customerInput.consent_type !== "none" ? new Date().toISOString() : null,
    consent_evidence_ref: customerInput.consent_evidence_ref ?? "",
    unsubscribed_at: null,
    notes: customerInput.notes ?? "",
  });
}

async function findOrCreateVehicle(customerId, vehicleInput) {
  if (vehicleInput.id) {
    const existing = await findOne("vehicles", { Id: vehicleInput.id });
    if (!existing) throw new Error(`intake: vehicle ${vehicleInput.id} not found`);
    return existing;
  }
  if (vehicleInput.plate) {
    const existing = await findOne("vehicles", { customer_id: customerId, plate: vehicleInput.plate });
    if (existing) return existing;
  }
  return createRecord("vehicles", {
    customer_id: customerId,
    year: vehicleInput.year,
    make: vehicleInput.make,
    model: vehicleInput.model,
    plate: vehicleInput.plate ?? "",
    vin: vehicleInput.vin ?? "",
    summer_size: vehicleInput.summer_size ?? "",
    winter_size: vehicleInput.winter_size ?? "",
    drive_type: vehicleInput.drive_type ?? "",
    tpms: Boolean(vehicleInput.tpms),
  });
}

/** Picks the first active, unoccupied rack location for the shop/site — a simple deterministic first-fit, not a capacity optimizer (that's agent 10). */
export async function assignRackLocation({ shopId, site }) {
  const { records } = await listRecords("rack_locations", {
    where: { shop_id: shopId, site, active: true, occupied_by_set_id: { is: "null" } },
    limit: 1,
  });
  return records[0] ?? null;
}

export async function run(context) {
  const { shopId, actorId = "intake-agent" } = context;
  if (!shopId) throw new Error("intake.run: shopId is required");

  return withAgentRun(
    { agentKey: "intake", shopId, inputSummary: `intake for ${context.customer?.phone_e164 ?? "new customer"}` },
    async () => {
      const customer = await findOrCreateCustomer(shopId, context.customer);
      const vehicle = await findOrCreateVehicle(customer.Id, context.vehicle);

      const quantity = context.tireSet.quantity ?? 4;
      const tireSet = await createRecord("tire_sets", {
        vehicle_id: vehicle.Id,
        shop_id: shopId,
        season: context.tireSet.season,
        on_wheels: Boolean(context.tireSet.on_wheels),
        wheel_type: context.tireSet.wheel_type ?? "",
        quantity,
        brand: context.tireSet.brand ?? "",
        model: context.tireSet.model ?? "",
        size: context.tireSet.size ?? "",
        dot_week: context.tireSet.dot_week ?? null,
        dot_year: context.tireSet.dot_year ?? null,
        status: "in_storage",
        rack_location_id: null,
        intake_date: context.intakeDate ?? new Date().toISOString().slice(0, 10),
        last_touched_date: context.intakeDate ?? new Date().toISOString().slice(0, 10),
        storage_agreement_ref: context.tireSet.storage_agreement_ref ?? "",
        storage_fee_cad: context.tireSet.storage_fee_cad ?? 0,
      });

      const tires = [];
      for (let i = 0; i < quantity; i++) {
        const position = context.tireSet.positions?.[i] ?? POSITIONS[i] ?? `extra_${i}`;
        tires.push(
          await createRecord("tires", {
            tire_set_id: tireSet.Id,
            position,
            dot_serial: context.tireSet.dot_serials?.[i] ?? "",
            notes: "",
            retired_at: null,
          })
        );
      }

      let rackLocation = null;
      const requestedSite = context.tireSet.site ?? "on_site";
      if (context.rackLocationId) {
        rackLocation = await findOne("rack_locations", { Id: context.rackLocationId });
      } else {
        rackLocation = await assignRackLocation({ shopId, site: requestedSite });
      }

      if (rackLocation) {
        await updateRecord("rack_locations", rackLocation.Id, { occupied_by_set_id: tireSet.Id });
        await updateRecord("tire_sets", tireSet.Id, { rack_location_id: rackLocation.Id });
        await createRecord("movements", {
          tire_set_id: tireSet.Id,
          from_location_id: null,
          to_location_id: rackLocation.Id,
          moved_by: actorId,
          moved_at: new Date().toISOString(),
          reason: "intake",
        });
      }

      await append({
        shopId,
        actorType: "staff",
        actorId,
        action: "intake_created",
        entityType: "tire_sets",
        entityId: tireSet.Id,
        payload: {
          customer_id: customer.Id,
          vehicle_id: vehicle.Id,
          quantity,
          rack_location_id: rackLocation?.Id ?? null,
          consent_type: customer.consent_type,
        },
      });

      return {
        summary: `Intake complete: tire_set ${tireSet.Id}, ${quantity} tires, rack ${
          rackLocation ? rackLocation.Id : "UNASSIGNED — no free location found"
        }`,
        recordsTouched: 2 + tires.length + (rackLocation ? 2 : 0),
        customer,
        vehicle,
        tireSet,
        tires,
        rackLocation,
      };
    }
  );
}

export default { run };

if (import.meta.url === `file://${process.argv[1]}`) {
  const contextJson = process.argv[2];
  if (!contextJson) {
    console.error("Usage: node src/agents/01-intake.js '<json context>'");
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
