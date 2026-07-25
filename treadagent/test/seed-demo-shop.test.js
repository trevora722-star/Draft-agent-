import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache, listRecords } from "../src/lib/nocodb.js";
import { main as seedDemoShop } from "../scripts/seed-demo-shop.js";
import { verifyChain } from "../src/lib/ledger.js";

let mock;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
  mock.seed("regulations", [{ province: "BC", legal_min_32nds: 2, winter_designation_min_mm: 3.5 }]);
});

describe("seed-demo-shop", () => {
  test("dry-run makes no writes", async () => {
    await seedDemoShop({ sets: 20, dryRun: true });
    assert.equal(mock.table("shops").rows.length, 0);
    assert.equal(mock.table("customers").rows.length, 0);
  });

  test("generates the requested number of tire sets with plausible structure", async () => {
    await seedDemoShop({ sets: 20 });

    const shops = mock.table("shops").rows;
    assert.equal(shops.length, 1);
    const shop = shops[0];
    assert.equal(shop.province, "BC");

    const tireSets = mock.table("tire_sets").rows;
    assert.equal(tireSets.length, 20);

    const tires = mock.table("tires").rows;
    assert.equal(tires.length, 20 * 4);

    // Every tire set should have a matching set of 4 tires.
    for (const set of tireSets) {
      const setTires = tires.filter((t) => t.tire_set_id === set.Id);
      assert.equal(setTires.length, 4, `tire_set ${set.Id} should have 4 tires`);
    }

    const readings = mock.table("tread_readings").rows;
    assert.ok(readings.length >= tires.length * 2, "each tire should have at least 2 seasons of history");

    // Dormancy is ~8% of sets.
    const dormancyCases = mock.table("dormancy_cases").rows;
    const ratio = dormancyCases.length / tireSets.length;
    assert.ok(ratio > 0 && ratio < 0.25, `dormancy ratio ${ratio} should be roughly 8%, allowing for small-N variance`);

    // Most rack locations that are occupied point at a real tire_set.
    const rackLocations = mock.table("rack_locations").rows;
    const occupied = rackLocations.filter((r) => r.occupied_by_set_id);
    assert.ok(occupied.length >= tireSets.length * 0.9);

    // Appointments exist and are staggered, not all on one day.
    const appointments = mock.table("appointments").rows;
    assert.ok(appointments.length > 0);
    const uniqueDays = new Set(appointments.map((a) => a.slot_start.slice(0, 10)));
    assert.ok(uniqueDays.size > 3, "appointments should be spread across multiple days, not stampeded into one");

    const chain = await verifyChain(shop.Id);
    assert.equal(chain.ok, true);
  });

  test("is safe to call twice — second call recognizes the existing shop rather than duplicating it", async () => {
    await seedDemoShop({ sets: 10 });
    await seedDemoShop({ sets: 10 });
    assert.equal(mock.table("shops").rows.length, 1);
  });

  test("throws a clear error when regulations haven't been provisioned", async () => {
    mock.table("regulations").rows = [];
    await assert.rejects(() => seedDemoShop({ sets: 10 }), /provision/);
  });
});
