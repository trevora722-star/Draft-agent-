import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache } from "../src/lib/nocodb.js";
import { enqueue, approve, reject, assertApproved, listPending, REVIEW_STATUS } from "../src/lib/review.js";

let mock;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
});

function baseEnqueue(overrides = {}) {
  return enqueue({
    shopId: 1,
    entityType: "outreach_messages",
    entityId: 501,
    proposedAction: "send_sms",
    agentKey: "outreach",
    riskLevel: "medium",
    payload: { body: "Hi, time to swap your tires" },
    ...overrides,
  });
}

describe("enqueue", () => {
  test("creates a pending row and logs to the ledger", async () => {
    const row = await baseEnqueue();
    assert.equal(row.status, REVIEW_STATUS.PENDING);
    const ledgerTable = mock.table("audit_ledger");
    assert.equal(ledgerTable.rows.length, 1);
    assert.equal(ledgerTable.rows[0].action, "review_enqueued");
  });

  test("rejects an invalid risk level", async () => {
    await assert.rejects(() => baseEnqueue({ riskLevel: "extreme" }));
  });

  test("requires all core fields", async () => {
    await assert.rejects(() => enqueue({ shopId: 1 }));
  });
});

describe("assertApproved — the send-path gate", () => {
  test("throws when there is no review row at all", async () => {
    await assert.rejects(() => assertApproved("outreach_messages", 999), /no approved review/);
  });

  test("throws when the review is still pending", async () => {
    await baseEnqueue();
    await assert.rejects(() => assertApproved("outreach_messages", 501), /no approved review/);
  });

  test("throws when the review was rejected", async () => {
    const row = await baseEnqueue();
    await reject({ reviewId: row.Id, decidedBy: "staff1", notes: "wrong customer" });
    await assert.rejects(() => assertApproved("outreach_messages", 501), /no approved review/);
  });

  test("resolves with the approved row once approved", async () => {
    const row = await baseEnqueue();
    await approve({ reviewId: row.Id, decidedBy: "staff1" });
    const approved = await assertApproved("outreach_messages", 501);
    assert.equal(approved.status, REVIEW_STATUS.APPROVED);
    assert.equal(approved.decided_by, "staff1");
  });
});

describe("approve", () => {
  test("requires decidedBy", async () => {
    const row = await baseEnqueue();
    await assert.rejects(() => approve({ reviewId: row.Id }));
  });

  test("cannot approve a row twice", async () => {
    const row = await baseEnqueue();
    await approve({ reviewId: row.Id, decidedBy: "staff1" });
    await assert.rejects(() => approve({ reviewId: row.Id, decidedBy: "staff2" }));
  });

  test("logs review_approved to the ledger", async () => {
    const row = await baseEnqueue();
    await approve({ reviewId: row.Id, decidedBy: "staff1" });
    const actions = mock.table("audit_ledger").rows.map((r) => r.action);
    assert.deepEqual(actions, ["review_enqueued", "review_approved"]);
  });
});

describe("reject", () => {
  test("requires non-empty decision notes", async () => {
    const row = await baseEnqueue();
    await assert.rejects(() => reject({ reviewId: row.Id, decidedBy: "staff1", notes: "" }));
    await assert.rejects(() => reject({ reviewId: row.Id, decidedBy: "staff1" }));
  });

  test("rejects with notes succeeds and logs to the ledger", async () => {
    const row = await baseEnqueue();
    const updated = await reject({ reviewId: row.Id, decidedBy: "staff1", notes: "duplicate" });
    assert.equal(updated.status, REVIEW_STATUS.REJECTED);
    const last = mock.table("audit_ledger").rows.at(-1);
    assert.equal(last.action, "review_rejected");
  });

  test("cannot reject an already-approved row", async () => {
    const row = await baseEnqueue();
    await approve({ reviewId: row.Id, decidedBy: "staff1" });
    await assert.rejects(() => reject({ reviewId: row.Id, decidedBy: "staff1", notes: "changed mind" }));
  });
});

describe("listPending", () => {
  test("filters by shop and optional risk level", async () => {
    await baseEnqueue({ entityId: 1, riskLevel: "low" });
    await baseEnqueue({ entityId: 2, riskLevel: "high" });
    await baseEnqueue({ shopId: 2, entityId: 3, riskLevel: "high" });

    const shop1 = await listPending(1);
    assert.equal(shop1.length, 2);

    const shop1High = await listPending(1, { riskLevel: "high" });
    assert.equal(shop1High.length, 1);
    assert.equal(shop1High[0].entity_id, "2");
  });
});
