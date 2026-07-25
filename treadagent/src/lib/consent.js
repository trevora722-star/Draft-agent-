// CASL enforcement — CLAUDE.md constraint #7. This is structural, not a
// UI disclaimer: sendOutbound() is the single choke point every SMS/
// email send must go through, and it refuses to run rather than trust
// the caller to have checked consent, approval, or quiet hours first.

import { findOne, updateRecord } from "./nocodb.js";
import { append } from "./ledger.js";
import { assertApproved } from "./review.js";
import { isWithinQuietHours } from "./dates.js";

const UNSUBSCRIBE_KEYWORDS = ["stop", "arret", "unsubscribe", "desabonnement"];

function stripAccents(text) {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "");
}

/** SMS reply keyword match: STOP / ARRÊT / UNSUBSCRIBE / DÉSABONNEMENT (accent- and case-insensitive). */
export function isUnsubscribeKeyword(text) {
  if (!text) return false;
  const normalized = stripAccents(text.trim().toLowerCase());
  return UNSUBSCRIBE_KEYWORDS.includes(normalized);
}

export class ConsentError extends Error {
  constructor(message) {
    super(message);
    this.name = "ConsentError";
  }
}

/** Throws if this customer cannot legally be sent a CEM right now. Does not check quiet hours or review approval — see sendOutbound(). */
export function assertConsentOk(customer) {
  if (!customer) throw new ConsentError("assertConsentOk: no customer record");
  if (customer.consent_type === "none" || !customer.consent_type) {
    throw new ConsentError(`Customer ${customer.Id} has no consent on file (consent_type: none)`);
  }
  if (customer.unsubscribed_at) {
    throw new ConsentError(`Customer ${customer.Id} unsubscribed at ${customer.unsubscribed_at}`);
  }
}

/** Frozen consent state to store on the outreach row at send time, independent of later changes to the customer record. */
export function buildConsentSnapshot(customer) {
  return {
    consent_type: customer.consent_type,
    consent_source: customer.consent_source,
    consent_timestamp: customer.consent_timestamp,
    unsubscribed_at: customer.unsubscribed_at ?? null,
    preferred_channel: customer.preferred_channel,
    preferred_language: customer.preferred_language,
    snapshot_at: new Date().toISOString(),
  };
}

/**
 * Basic structural check that a rendered CEM carries the required CASL
 * disclosure elements. This is a linting aid for template authors, not
 * a substitute for legal review — see COMPLIANCE.md.
 */
export function validateCemCompliance(renderedText, shop) {
  const text = renderedText || "";
  const lower = text.toLowerCase();
  const missing = [];

  if (!shop?.legal_name || !lower.includes(shop.legal_name.toLowerCase())) {
    missing.push("legal_name");
  }
  if (!shop?.address || !lower.includes(shop.address.toLowerCase())) {
    missing.push("mailing_address");
  }
  const hasContact =
    (shop?.sms_from && text.includes(shop.sms_from)) ||
    (shop?.email_from && lower.includes(shop.email_from.toLowerCase()));
  if (!hasContact) missing.push("contact_phone_or_email");

  if (!/stop|unsubscribe|d[ée]sabonn|arr[êe]t/i.test(text)) {
    missing.push("unsubscribe_mechanism");
  }

  return { valid: missing.length === 0, missing };
}

/**
 * Process an unsubscribe request immediately. Logs to the ledger on
 * receipt (not once some later job "processes" it), and is idempotent —
 * repeated STOP messages from an already-unsubscribed customer do not
 * re-log or trigger a second confirmation.
 */
export async function recordUnsubscribe({ customerId, channel, rawText }) {
  const customer = await findOne("customers", { Id: customerId });
  if (!customer) throw new Error(`recordUnsubscribe: no customer ${customerId}`);

  if (customer.unsubscribed_at) {
    return { customer, alreadyUnsubscribed: true, shouldSendConfirmation: false };
  }

  const unsubscribedAt = new Date().toISOString();
  const updated = await updateRecord("customers", customerId, {
    unsubscribed_at: unsubscribedAt,
    unsubscribe_channel: channel,
  });

  await append({
    shopId: customer.shop_id,
    actorType: "customer",
    actorId: String(customerId),
    action: "unsubscribe_received",
    entityType: "customers",
    entityId: customerId,
    payload: { channel, raw_text: rawText ?? null, unsubscribed_at: unsubscribedAt },
  });

  return { customer: updated, alreadyUnsubscribed: false, shouldSendConfirmation: true };
}

/**
 * The send-path gate. Every outbound SMS/email goes through this
 * function — it throws rather than sends if:
 *   - the outreach_messages row isn't in an APPROVED review_queue entry
 *   - the customer's consent_type is "none" or they've unsubscribed
 *   - the shop's current local time is inside quiet hours
 *
 * `transport` is the injected adapter call (SMS or email) that actually
 * talks to Twilio/Resend/console — consent.js never imports those
 * directly, keeping this module's job purely the compliance gate.
 */
export async function sendOutbound({ outreachMessageId, actorId = "send-pipeline", transport, now = new Date() }) {
  if (typeof transport !== "function") {
    throw new Error("sendOutbound: transport function is required");
  }

  const message = await findOne("outreach_messages", { Id: outreachMessageId });
  if (!message) throw new Error(`sendOutbound: no outreach_messages row ${outreachMessageId}`);

  // Gate 1: Full Review approval (data-layer enforced, not just UI).
  await assertApproved("outreach_messages", outreachMessageId);

  const customer = await findOne("customers", { Id: message.customer_id });
  const shop = await findOne("shops", { Id: message.shop_id });

  // Gate 2: consent state.
  assertConsentOk(customer);

  // Gate 3: quiet hours, in the shop's own timezone — never server-local.
  if (
    isWithinQuietHours(
      {
        timeZone: shop.timezone,
        quietHoursStart: shop.quiet_hours_start,
        quietHoursEnd: shop.quiet_hours_end,
      },
      now
    )
  ) {
    throw new ConsentError(
      `sendOutbound: shop ${shop.Id} is inside quiet hours (${shop.quiet_hours_start}-${shop.quiet_hours_end} ${shop.timezone})`
    );
  }

  const snapshot = buildConsentSnapshot(customer);
  const providerResult = await transport({ message, customer, shop });

  const updated = await updateRecord("outreach_messages", outreachMessageId, {
    status: "sent",
    sent_at: new Date().toISOString(),
    provider_id: providerResult?.providerId ?? null,
    provider_status: providerResult?.providerStatus ?? null,
    consent_snapshot_json: JSON.stringify(snapshot),
  });

  await append({
    shopId: shop.Id,
    actorType: "system",
    actorId,
    action: "outbound_sent",
    entityType: "outreach_messages",
    entityId: outreachMessageId,
    payload: { channel: message.channel, provider_id: providerResult?.providerId ?? null },
  });

  return updated;
}
