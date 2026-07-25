import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache } from "../src/lib/nocodb.js";
import { enqueue, approve } from "../src/lib/review.js";
import {
  isUnsubscribeKeyword,
  assertConsentOk,
  buildConsentSnapshot,
  validateCemCompliance,
  recordUnsubscribe,
  sendOutbound,
  ConsentError,
} from "../src/lib/consent.js";

let mock;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
});

const SHOP = {
  legal_name: "Ace Tire & Auto Ltd.",
  address: "123 Main St, Kelowna, BC",
  sms_from: "+15551234567",
  email_from: "hello@acetire.example",
  timezone: "America/Vancouver",
  // No quiet hours by default so most tests aren't sensitive to the
  // real wall-clock time the suite happens to run at; the dedicated
  // quiet-hours test below configures a window and injects `now`.
  quiet_hours_start: null,
  quiet_hours_end: null,
};

function seedShopAndCustomer(overrides = {}, shopOverrides = {}) {
  const [shop] = mock.seed("shops", [{ ...SHOP, ...shopOverrides }]);
  const [customer] = mock.seed("customers", [
    {
      shop_id: shop.Id,
      first_name: "Pat",
      consent_type: "express",
      consent_source: "intake_form",
      consent_timestamp: "2025-01-01T00:00:00.000Z",
      preferred_channel: "sms",
      preferred_language: "en",
      unsubscribed_at: null,
      ...overrides,
    },
  ]);
  return { shop, customer };
}

describe("isUnsubscribeKeyword", () => {
  const positives = ["STOP", "stop", " Stop ", "ARRET", "arrêt", "UNSUBSCRIBE", "désabonnement", "DESABONNEMENT"];
  for (const kw of positives) {
    test(`matches "${kw}"`, () => assert.equal(isUnsubscribeKeyword(kw), true));
  }
  test("does not match ordinary text", () => {
    assert.equal(isUnsubscribeKeyword("please stop by tomorrow"), false);
    assert.equal(isUnsubscribeKeyword(""), false);
    assert.equal(isUnsubscribeKeyword(undefined), false);
  });
});

describe("assertConsentOk", () => {
  test("throws when consent_type is none", () => {
    assert.throws(() => assertConsentOk({ Id: 1, consent_type: "none" }), ConsentError);
  });
  test("throws when unsubscribed_at is set", () => {
    assert.throws(
      () => assertConsentOk({ Id: 1, consent_type: "express", unsubscribed_at: "2026-01-01T00:00:00Z" }),
      ConsentError
    );
  });
  test("passes for express consent, not unsubscribed", () => {
    assert.doesNotThrow(() => assertConsentOk({ Id: 1, consent_type: "express", unsubscribed_at: null }));
  });
});

describe("buildConsentSnapshot", () => {
  test("captures consent fields independent of later mutation", () => {
    const customer = { consent_type: "express", consent_source: "intake_form", unsubscribed_at: null };
    const snapshot = buildConsentSnapshot(customer);
    assert.equal(snapshot.consent_type, "express");
    assert.ok(snapshot.snapshot_at);
    customer.consent_type = "none"; // mutate after snapshot
    assert.equal(snapshot.consent_type, "express"); // snapshot unaffected
  });
});

describe("validateCemCompliance", () => {
  test("flags every missing required element", () => {
    const result = validateCemCompliance("Hey, your tires are ready!", SHOP);
    assert.equal(result.valid, false);
    assert.ok(result.missing.includes("legal_name"));
    assert.ok(result.missing.includes("unsubscribe_mechanism"));
  });

  test("passes with all required elements present", () => {
    const body = `Hi Pat, your winter tires are ready at Ace Tire & Auto Ltd., 123 Main St, Kelowna, BC. Reply STOP to unsubscribe or call ${SHOP.sms_from}.`;
    const result = validateCemCompliance(body, SHOP);
    assert.equal(result.valid, true);
    assert.deepEqual(result.missing, []);
  });
});

describe("recordUnsubscribe", () => {
  test("sets unsubscribed_at and logs to the ledger on first request", async () => {
    const { customer } = seedShopAndCustomer();
    const result = await recordUnsubscribe({ customerId: customer.Id, channel: "sms", rawText: "STOP" });
    assert.equal(result.alreadyUnsubscribed, false);
    assert.equal(result.shouldSendConfirmation, true);
    assert.ok(result.customer.unsubscribed_at);

    const ledgerRows = mock.table("audit_ledger").rows;
    assert.equal(ledgerRows.length, 1);
    assert.equal(ledgerRows[0].action, "unsubscribe_received");
  });

  test("is idempotent — a second STOP does not re-log or re-confirm", async () => {
    const { customer } = seedShopAndCustomer();
    await recordUnsubscribe({ customerId: customer.Id, channel: "sms", rawText: "STOP" });
    const second = await recordUnsubscribe({ customerId: customer.Id, channel: "sms", rawText: "STOP" });
    assert.equal(second.alreadyUnsubscribed, true);
    assert.equal(second.shouldSendConfirmation, false);
    assert.equal(mock.table("audit_ledger").rows.length, 1);
  });
});

describe("sendOutbound — the compliance gate", () => {
  async function seedApprovedMessage({ customerOverrides = {}, shopOverrides = {} } = {}) {
    const { shop: shopRow, customer } = seedShopAndCustomer(customerOverrides, shopOverrides);
    const [message] = mock.seed("outreach_messages", [
      {
        shop_id: shopRow.Id,
        customer_id: customer.Id,
        channel: "sms",
        rendered_body: "Your tires are ready to swap.",
        status: "pending_review",
      },
    ]);
    const review = await enqueue({
      shopId: shopRow.Id,
      entityType: "outreach_messages",
      entityId: message.Id,
      proposedAction: "send_sms",
      agentKey: "outreach",
      riskLevel: "low",
    });
    await approve({ reviewId: review.Id, decidedBy: "staff1" });
    return { shop: shopRow, customer, message };
  }

  test("throws when there is no approved review", async () => {
    const { shop, customer } = seedShopAndCustomer();
    const [message] = mock.seed("outreach_messages", [
      { shop_id: shop.Id, customer_id: customer.Id, channel: "sms", status: "draft" },
    ]);
    await assert.rejects(
      () => sendOutbound({ outreachMessageId: message.Id, transport: async () => ({ providerId: "x" }) }),
      /no approved review/
    );
  });

  test("throws when consent_type is none, even if approved", async () => {
    const { message } = await seedApprovedMessage({ customerOverrides: { consent_type: "none" } });
    await assert.rejects(
      () => sendOutbound({ outreachMessageId: message.Id, transport: async () => ({ providerId: "x" }) }),
      ConsentError
    );
  });

  test("throws when the customer has unsubscribed, even if approved", async () => {
    const { message } = await seedApprovedMessage({
      customerOverrides: { unsubscribed_at: "2026-01-01T00:00:00.000Z" },
    });
    await assert.rejects(
      () => sendOutbound({ outreachMessageId: message.Id, transport: async () => ({ providerId: "x" }) }),
      ConsentError
    );
  });

  test("throws when shop-local time is inside quiet hours, even if approved and consented", async () => {
    const { message } = await seedApprovedMessage({
      shopOverrides: { quiet_hours_start: "21:00", quiet_hours_end: "08:00" },
    });
    // 2026-01-15T06:00Z = 22:00 PST in America/Vancouver — inside 21:00-08:00.
    const nightTime = new Date("2026-01-15T06:00:00.000Z");
    let sent = false;
    await assert.rejects(
      () =>
        sendOutbound({
          outreachMessageId: message.Id,
          now: nightTime,
          transport: async () => {
            sent = true;
            return { providerId: "x" };
          },
        }),
      ConsentError
    );
    assert.equal(sent, false, "transport must never be invoked during quiet hours");
  });

  test("sends successfully when approved, consented, and outside quiet hours", async () => {
    const { message } = await seedApprovedMessage();
    let transportCalled = false;
    const updated = await sendOutbound({
      outreachMessageId: message.Id,
      transport: async ({ message: m, customer, shop }) => {
        transportCalled = true;
        assert.equal(m.Id, message.Id);
        assert.equal(customer.consent_type, "express");
        assert.equal(shop.legal_name, SHOP.legal_name);
        return { providerId: "provider-123", providerStatus: "queued" };
      },
    });
    assert.equal(transportCalled, true);
    assert.equal(updated.status, "sent");
    assert.equal(updated.provider_id, "provider-123");
    assert.ok(updated.consent_snapshot_json);
    const snapshot = JSON.parse(updated.consent_snapshot_json);
    assert.equal(snapshot.consent_type, "express");

    const actions = mock.table("audit_ledger").rows.map((r) => r.action);
    assert.ok(actions.includes("outbound_sent"));
  });

  test("a draft mentioning a replacement recommendation cannot send without an approved review row", async () => {
    const { shop, customer } = seedShopAndCustomer();
    const [message] = mock.seed("outreach_messages", [
      {
        shop_id: shop.Id,
        customer_id: customer.Id,
        channel: "sms",
        rendered_body: "Based on your tread readings, we recommend replacing your winter tires — quote attached.",
        status: "pending_review",
      },
    ]);
    // Note: no review enqueued/approved at all.
    let sent = false;
    await assert.rejects(
      () =>
        sendOutbound({
          outreachMessageId: message.Id,
          transport: async () => {
            sent = true;
            return { providerId: "x" };
          },
        }),
      /no approved review/
    );
    assert.equal(sent, false, "transport must never be invoked without approval");
  });
});
