import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache } from "../src/lib/nocodb.js";
import { run as intakeRun } from "../src/agents/01-intake.js";
import { run as treadRun } from "../src/agents/02-tread.js";
import { run as rackRun, assignLocation, releaseLocation, suggestConsolidation } from "../src/agents/06-rack.js";
import { run as complianceRun } from "../src/agents/09-compliance.js";
import { enqueue, approve } from "../src/lib/review.js";
import { sendOutbound } from "../src/lib/consent.js";

let mock;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
});

const REGULATION = { legal_min_32nds: 2, winter_designation_min_mm: 3.5 };

function seedShop(overrides = {}) {
  const [shop] = mock.seed("shops", [
    {
      name: "Ace Tire",
      legal_name: "Ace Tire & Auto Ltd.",
      timezone: "America/Vancouver",
      province: "BC",
      ...overrides,
    },
  ]);
  return shop;
}

function seedRackLocation(shopId, overrides = {}) {
  const [loc] = mock.seed("rack_locations", [
    {
      shop_id: shopId,
      site: "on_site",
      site_name: "Main",
      zone: "A",
      aisle: "1",
      rack: "1",
      shelf: "1",
      slot: "1",
      capacity_sets: 1,
      occupied_by_set_id: null,
      active: true,
      ...overrides,
    },
  ]);
  return loc;
}

describe("agent 1: intake", () => {
  test("creates customer, vehicle, tire set, tires, and assigns a rack location", async () => {
    const shop = seedShop();
    const rack = seedRackLocation(shop.Id);

    const result = await intakeRun({
      shopId: shop.Id,
      customer: {
        first_name: "Pat",
        last_name: "Nguyen",
        phone_e164: "+16045551234",
        consent_type: "express",
        consent_source: "intake_form",
      },
      vehicle: { year: 2020, make: "Honda", model: "Civic", plate: "ABC123" },
      tireSet: { season: "winter", quantity: 4, brand: "Michelin", size: "205/55R16" },
    });

    assert.equal(result.tires.length, 4);
    assert.equal(result.rackLocation.Id, rack.Id);
    assert.equal(result.tireSet.status, "in_storage");

    const updatedRack = mock.table("rack_locations").rows.find((r) => r.Id === rack.Id);
    assert.equal(updatedRack.occupied_by_set_id, result.tireSet.Id);

    const ledgerActions = mock.table("audit_ledger").rows.map((r) => r.action);
    assert.ok(ledgerActions.includes("intake_created"));

    const agentRuns = mock.table("agent_runs").rows;
    assert.equal(agentRuns.length, 1);
    assert.equal(agentRuns[0].status, "success");
  });

  test("dedupes an existing customer by phone within the shop", async () => {
    const shop = seedShop();
    seedRackLocation(shop.Id);
    const [existingCustomer] = mock.seed("customers", [
      { shop_id: shop.Id, phone_e164: "+16045550000", consent_type: "express" },
    ]);

    const result = await intakeRun({
      shopId: shop.Id,
      customer: { phone_e164: "+16045550000", first_name: "Ignored" },
      vehicle: { year: 2019, make: "Toyota", model: "Corolla" },
      tireSet: { season: "summer", quantity: 4 },
    });

    assert.equal(result.customer.Id, existingCustomer.Id);
    assert.equal(mock.table("customers").rows.length, 1);
  });

  test("leaves rack_location_id unassigned when no free location exists", async () => {
    const shop = seedShop(); // no rack locations seeded
    const result = await intakeRun({
      shopId: shop.Id,
      customer: { phone_e164: "+16045559999", first_name: "Sam" },
      vehicle: { year: 2021, make: "Mazda", model: "3" },
      tireSet: { season: "winter", quantity: 4 },
    });
    assert.equal(result.rackLocation, null);
    assert.equal(result.tireSet.rack_location_id, null);
  });
});

describe("agent 2: tread", () => {
  test("records a reading, classifies it, and flags below-legal depth", async () => {
    const shop = seedShop();
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id, season: "winter" }]);
    const [tire] = mock.seed("tires", [{ tire_set_id: tireSet.Id, position: "LF" }]);

    const result = await treadRun({
      shopId: shop.Id,
      tireId: tire.Id,
      reading: {
        outer_32nds: 2,
        centre_32nds: 2,
        inner_32nds: 2,
        reading_date: "2026-01-01",
        odometer_km: 50000,
        measured_by: "tech1",
      },
      regulation: REGULATION,
      shopConfig: { practicalReplacement32nds: 4, season: "winter" },
    });

    assert.equal(result.reading.min_32nds, 2);
    assert.equal(result.classification.status, "below_legal");
    assert.equal(result.reading.flagged, true);
  });

  test("computes wear rate and projection from a second dated reading with odometer history", async () => {
    const shop = seedShop();
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id, season: "winter" }]);
    const [tire] = mock.seed("tires", [{ tire_set_id: tireSet.Id, position: "LF" }]);

    await treadRun({
      shopId: shop.Id,
      tireId: tire.Id,
      reading: { outer_32nds: 10, centre_32nds: 10, inner_32nds: 10, reading_date: "2025-04-01", odometer_km: 0 },
      regulation: REGULATION,
      shopConfig: { season: "summer" },
    });

    const second = await treadRun({
      shopId: shop.Id,
      tireId: tire.Id,
      reading: { outer_32nds: 8, centre_32nds: 8, inner_32nds: 8, reading_date: "2025-10-01", odometer_km: 4000 },
      regulation: REGULATION,
      shopConfig: { season: "winter" },
    });

    assert.ok(second.wearRate);
    assert.equal(second.wearRate.value, 0.5);
    assert.ok(second.projection);
  });

  test("flags irregular wear pattern without diagnosing", async () => {
    const shop = seedShop();
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id, season: "summer" }]);
    const [tire] = mock.seed("tires", [{ tire_set_id: tireSet.Id, position: "RF" }]);

    const result = await treadRun({
      shopId: shop.Id,
      tireId: tire.Id,
      reading: { outer_32nds: 9, centre_32nds: 9, inner_32nds: 3, reading_date: "2026-01-01" },
      regulation: REGULATION,
      shopConfig: { season: "summer" },
    });
    assert.equal(result.irregularWear.flagged, true);
    assert.equal(result.reading.flagged, true);
  });
});

describe("agent 6: rack", () => {
  test("assignLocation occupies a free location and logs a movement", async () => {
    const shop = seedShop();
    const rack = seedRackLocation(shop.Id);
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id, season: "winter" }]);

    const result = await assignLocation({ shopId: shop.Id, tireSetId: tireSet.Id, site: "on_site" });
    assert.equal(result.assigned, true);
    assert.equal(result.location.Id, rack.Id);
    assert.equal(mock.table("movements").rows.length, 1);
  });

  test("assignLocation reports failure when no location is free", async () => {
    const shop = seedShop();
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id, season: "winter" }]);
    const result = await assignLocation({ shopId: shop.Id, tireSetId: tireSet.Id, site: "on_site" });
    assert.equal(result.assigned, false);
  });

  test("releaseLocation frees the rack and clears tire_set.rack_location_id", async () => {
    const shop = seedShop();
    const rack = seedRackLocation(shop.Id);
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id, rack_location_id: rack.Id }]);
    mock.table("rack_locations").rows.find((r) => r.Id === rack.Id).occupied_by_set_id = tireSet.Id;

    const result = await releaseLocation({ shopId: shop.Id, tireSetId: tireSet.Id });
    assert.equal(result.released, true);
    const updatedRack = mock.table("rack_locations").rows.find((r) => r.Id === rack.Id);
    assert.equal(updatedRack.occupied_by_set_id, null);
  });

  test("suggestConsolidation flags underused multi-capacity locations without writing anything", async () => {
    const shop = seedShop();
    seedRackLocation(shop.Id, { capacity_sets: 4, occupied_by_set_id: 1 });
    seedRackLocation(shop.Id, { capacity_sets: 1, occupied_by_set_id: 2 });

    const before = JSON.stringify(mock.table("rack_locations").rows);
    const result = await suggestConsolidation({ shopId: shop.Id });
    const after = JSON.stringify(mock.table("rack_locations").rows);

    assert.equal(before, after, "suggestConsolidation must not mutate data");
    assert.equal(result.underusedLocations.length, 1);
  });

  test("run() dispatches by action and logs an agent_runs row", async () => {
    const shop = seedShop();
    seedRackLocation(shop.Id);
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id }]);
    await rackRun({ shopId: shop.Id, action: "assign", tireSetId: tireSet.Id, site: "on_site" });
    assert.equal(mock.table("agent_runs").rows.length, 1);
  });
});

describe("agent 9: compliance", () => {
  test("reports a clean sweep when nothing is wrong", async () => {
    const shop = seedShop();
    mock.seed("customers", [{ shop_id: shop.Id, consent_type: "express", consent_timestamp: "2025-01-01T00:00:00Z" }]);

    const result = await complianceRun({ shopId: shop.Id });
    assert.equal(result.report.ledger.ok, true);
    assert.equal(result.report.unauthorizedSends.violations.length, 0);
    assert.equal(result.report.dormancyEvidence.gaps.length, 0);
  });

  test("catches a sent message whose consent snapshot shows no consent", async () => {
    const shop = seedShop();
    mock.seed("outreach_messages", [
      {
        shop_id: shop.Id,
        status: "sent",
        consent_snapshot_json: JSON.stringify({ consent_type: "none" }),
      },
    ]);

    const result = await complianceRun({ shopId: shop.Id });
    assert.equal(result.report.unauthorizedSends.violations.length, 1);
    assert.match(result.report.unauthorizedSends.violations[0].reason, /consent_type none/);
  });

  test("flags a stage-5 dormancy case missing its evidence bundle hash", async () => {
    const shop = seedShop();
    mock.seed("dormancy_cases", [{ shop_id: shop.Id, stage: 5, notices_json: "[{}]", evidence_bundle_hash: null }]);
    const result = await complianceRun({ shopId: shop.Id });
    assert.equal(result.report.dormancyEvidence.gaps.length, 1);
  });

  test("verifies the ledger genuinely reflects activity from other agents in this test run", async () => {
    const shop = seedShop();
    seedRackLocation(shop.Id);
    await intakeRun({
      shopId: shop.Id,
      customer: { phone_e164: "+16045551111", first_name: "Jo", consent_type: "express" },
      vehicle: { year: 2022, make: "Kia", model: "Soul" },
      tireSet: { season: "winter", quantity: 4 },
    });
    const result = await complianceRun({ shopId: shop.Id });
    assert.equal(result.report.ledger.ok, true);
    // verifyChain() runs before this sweep's own compliance_sweep_completed
    // row is appended, so it should see exactly the prior intake_created row.
    assert.equal(result.report.ledger.length, 1);
    const actionsAfter = mock.table("audit_ledger").rows.map((r) => r.action);
    assert.deepEqual(actionsAfter, ["intake_created", "compliance_sweep_completed"]);
  });
});

describe("cross-agent: send path still requires review approval even for a compliant, consented customer", () => {
  test("outreach cannot bypass Full Review just because consent+quiet-hours are fine", async () => {
    const shop = seedShop({ quiet_hours_start: null, quiet_hours_end: null });
    const [customer] = mock.seed("customers", [{ shop_id: shop.Id, consent_type: "express" }]);
    const [message] = mock.seed("outreach_messages", [
      { shop_id: shop.Id, customer_id: customer.Id, channel: "sms", status: "pending_review" },
    ]);
    await assert.rejects(() =>
      sendOutbound({ outreachMessageId: message.Id, transport: async () => ({ providerId: "x" }) })
    );

    const review = await enqueue({
      shopId: shop.Id,
      entityType: "outreach_messages",
      entityId: message.Id,
      proposedAction: "send_sms",
      agentKey: "outreach",
      riskLevel: "low",
    });
    await approve({ reviewId: review.Id, decidedBy: "staff1" });

    const sent = await sendOutbound({ outreachMessageId: message.Id, transport: async () => ({ providerId: "ok" }) });
    assert.equal(sent.status, "sent");
  });
});
