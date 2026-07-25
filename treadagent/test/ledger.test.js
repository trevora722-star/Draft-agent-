import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache } from "../src/lib/nocodb.js";
import { append, verifyChain, canonicalJSON, GENESIS_HASH, sha256Hex } from "../src/lib/ledger.js";

let mock;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
});

describe("canonicalJSON", () => {
  test("sorts keys regardless of insertion order", () => {
    const a = canonicalJSON({ b: 1, a: 2, c: { z: 1, y: 2 } });
    const b = canonicalJSON({ c: { y: 2, z: 1 }, a: 2, b: 1 });
    assert.equal(a, b);
  });

  test("preserves array order", () => {
    assert.equal(canonicalJSON({ a: [3, 1, 2] }), '{"a":[3,1,2]}');
  });
});

describe("append", () => {
  test("first row for a shop chains to the genesis hash", async () => {
    const row = await append({
      shopId: 1,
      actorType: "staff",
      actorId: "u1",
      action: "intake_created",
      entityType: "tire_sets",
      entityId: 42,
      payload: { note: "hello" },
    });
    assert.equal(row.seq, 1);
    assert.equal(row.prev_hash, GENESIS_HASH);
    assert.equal(row.hash.length, 64);
  });

  test("subsequent rows chain to the previous row's hash and increment seq", async () => {
    const r1 = await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    const r2 = await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "b", entityType: "x", entityId: 2 });
    assert.equal(r2.seq, 2);
    assert.equal(r2.prev_hash, r1.hash);
  });

  test("different shops have independent chains", async () => {
    const a1 = await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    const b1 = await append({ shopId: 2, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    assert.equal(a1.seq, 1);
    assert.equal(b1.seq, 1);
    assert.equal(a1.prev_hash, GENESIS_HASH);
    assert.equal(b1.prev_hash, GENESIS_HASH);
  });

  test("requires shopId and other fields", async () => {
    await assert.rejects(() => append({ actorType: "staff", action: "a", entityType: "x", entityId: 1 }));
    await assert.rejects(() => append({ shopId: 1, action: "a", entityType: "x", entityId: 1 }));
  });
});

describe("verifyChain", () => {
  test("passes for an untouched chain", async () => {
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1, payload: { n: 1 } });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "b", entityType: "x", entityId: 2, payload: { n: 2 } });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "c", entityType: "x", entityId: 3, payload: { n: 3 } });
    const result = await verifyChain(1);
    assert.equal(result.ok, true);
    assert.equal(result.length, 3);
  });

  test("passes for an empty chain", async () => {
    const result = await verifyChain(999);
    assert.equal(result.ok, true);
    assert.equal(result.length, 0);
  });

  test("detects a tampered payload in a middle row", async () => {
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1, payload: { n: 1 } });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "b", entityType: "x", entityId: 2, payload: { n: 2 } });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "c", entityType: "x", entityId: 3, payload: { n: 3 } });

    // Tamper directly with the in-memory row store, simulating a DB-level edit.
    const table = mock.table("audit_ledger");
    const middle = table.rows.find((r) => r.seq === 2);
    middle.payload_json = JSON.stringify({ n: 999 });

    const result = await verifyChain(1);
    assert.equal(result.ok, false);
    assert.equal(result.brokenAtSeq, 2);
    assert.match(result.reason, /hash mismatch/);
  });

  test("detects a tampered hash even if payload matches", async () => {
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "b", entityType: "x", entityId: 2 });

    const table = mock.table("audit_ledger");
    const last = table.rows.find((r) => r.seq === 2);
    last.hash = "f".repeat(64);

    const result = await verifyChain(1);
    assert.equal(result.ok, false);
    assert.equal(result.brokenAtSeq, 2);
  });

  test("detects a deleted middle row (sequence gap)", async () => {
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "a", entityType: "x", entityId: 1 });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "b", entityType: "x", entityId: 2 });
    await append({ shopId: 1, actorType: "staff", actorId: "u1", action: "c", entityType: "x", entityId: 3 });

    const table = mock.table("audit_ledger");
    table.rows = table.rows.filter((r) => r.seq !== 2);

    const result = await verifyChain(1);
    assert.equal(result.ok, false);
    assert.equal(result.brokenAtSeq, 3);
    assert.match(result.reason, /sequence gap|prev_hash mismatch/);
  });
});

describe("sha256Hex", () => {
  test("matches a known SHA-256 vector", () => {
    assert.equal(
      sha256Hex(""),
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
  });
});
