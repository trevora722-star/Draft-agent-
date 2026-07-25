import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache } from "../src/lib/nocodb.js";
import { main as verifyLedger } from "../scripts/verify-ledger.js";
import { append } from "../src/lib/ledger.js";

let mock;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
});

describe("verify-ledger", () => {
  test("reports ok:true with no shops", async () => {
    const result = await verifyLedger();
    assert.equal(result.ok, true);
    assert.deepEqual(result.results, []);
  });

  test("reports ok:true for a shop with an intact chain", async () => {
    mock.seed("shops", [{}]);
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "b", entityType: "x", entityId: 2 });

    const result = await verifyLedger();
    assert.equal(result.ok, true);
    assert.equal(result.results[0].ok, true);
  });

  test("reports ok:false and identifies the break for a tampered shop", async () => {
    mock.seed("shops", [{}]);
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "b", entityType: "x", entityId: 2 });
    mock.table("audit_ledger").rows.find((r) => r.seq === 2).hash = "f".repeat(64);

    const result = await verifyLedger();
    assert.equal(result.ok, false);
    assert.equal(result.results[0].ok, false);
    assert.equal(result.results[0].brokenAtSeq, 2);
  });

  test("checks all shops independently, one broken doesn't hide another's status", async () => {
    mock.seed("shops", [{}, {}]);
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    await append({ shopId: 2, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    mock.table("audit_ledger").rows.find((r) => r.shop_id === 1).hash = "f".repeat(64);

    const result = await verifyLedger();
    assert.equal(result.ok, false);
    const shop1 = result.results.find((r) => r.shopId === 1);
    const shop2 = result.results.find((r) => r.shopId === 2);
    assert.equal(shop1.ok, false);
    assert.equal(shop2.ok, true);
  });

  test("--shop filters to a single shop", async () => {
    mock.seed("shops", [{}, {}]);
    await append({ shopId: 2, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    process.argv = [...process.argv.slice(0, 2), "--shop=2"];
    const result = await verifyLedger();
    assert.equal(result.results.length, 1);
    assert.equal(result.results[0].shopId, "2"); // argv values are strings
  });
});
