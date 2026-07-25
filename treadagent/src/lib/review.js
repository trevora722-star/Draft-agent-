// Full Review human gate — CLAUDE.md constraint #5. No outbound action
// (SMS, email, notice, quote) may be sent without a shop staff member
// approving it here first, and that gate is enforced at the data layer:
// assertApproved() is what src/lib/consent.js's sendOutbound() calls
// before it will run, not just something the UI checks.

import { createRecord, findOne, updateRecord, listRecords } from "./nocodb.js";
import { append } from "./ledger.js";

export const REVIEW_STATUS = {
  PENDING: "pending",
  APPROVED: "approved",
  REJECTED: "rejected",
  EXPIRED: "expired",
};

export const RISK_LEVELS = ["low", "medium", "high"];

/**
 * Propose an outbound action for staff review. Agents call this — they
 * never call sendOutbound() directly.
 */
export async function enqueue({
  shopId,
  entityType,
  entityId,
  proposedAction,
  agentKey,
  riskLevel = "medium",
  payload = {},
}) {
  if (!shopId || !entityType || entityId === undefined || !proposedAction || !agentKey) {
    throw new Error(
      "review.enqueue: shopId, entityType, entityId, proposedAction, agentKey are required"
    );
  }
  if (!RISK_LEVELS.includes(riskLevel)) {
    throw new Error(`review.enqueue: invalid riskLevel "${riskLevel}"`);
  }

  const row = await createRecord("review_queue", {
    shop_id: shopId,
    entity_type: entityType,
    entity_id: String(entityId),
    proposed_action: proposedAction,
    agent_key: agentKey,
    risk_level: riskLevel,
    payload_json: JSON.stringify(payload),
    status: REVIEW_STATUS.PENDING,
  });

  await append({
    shopId,
    actorType: "agent",
    actorId: agentKey,
    action: "review_enqueued",
    entityType,
    entityId,
    payload: { review_id: row.Id, proposed_action: proposedAction, risk_level: riskLevel },
  });

  return row;
}

async function requirePendingReview(reviewId) {
  const row = await findOne("review_queue", { Id: reviewId });
  if (!row) throw new Error(`review.approve/reject: no review_queue row with Id ${reviewId}`);
  if (row.status !== REVIEW_STATUS.PENDING) {
    throw new Error(
      `review.approve/reject: review ${reviewId} is not pending (status: ${row.status})`
    );
  }
  return row;
}

/**
 * Approve a pending review row. This is the ONLY way a review_queue row
 * becomes approved — assertApproved() below is what the send path
 * checks for.
 */
export async function approve({ reviewId, decidedBy, notes = "" }) {
  if (!decidedBy) throw new Error("review.approve: decidedBy is required");
  const row = await requirePendingReview(reviewId);

  const updated = await updateRecord("review_queue", reviewId, {
    status: REVIEW_STATUS.APPROVED,
    decided_by: decidedBy,
    decided_at: new Date().toISOString(),
    decision_notes: notes,
  });

  await append({
    shopId: row.shop_id,
    actorType: "staff",
    actorId: decidedBy,
    action: "review_approved",
    entityType: row.entity_type,
    entityId: row.entity_id,
    payload: { review_id: reviewId },
  });

  return updated;
}

/**
 * Reject a pending review row. Decision notes are required — a
 * rejection with no explanation isn't auditable.
 */
export async function reject({ reviewId, decidedBy, notes }) {
  if (!decidedBy) throw new Error("review.reject: decidedBy is required");
  if (!notes || !notes.trim()) {
    throw new Error("review.reject: decision notes are required when rejecting");
  }
  const row = await requirePendingReview(reviewId);

  const updated = await updateRecord("review_queue", reviewId, {
    status: REVIEW_STATUS.REJECTED,
    decided_by: decidedBy,
    decided_at: new Date().toISOString(),
    decision_notes: notes,
  });

  await append({
    shopId: row.shop_id,
    actorType: "staff",
    actorId: decidedBy,
    action: "review_rejected",
    entityType: row.entity_type,
    entityId: row.entity_id,
    payload: { review_id: reviewId, notes },
  });

  return updated;
}

/**
 * Throws unless a matching APPROVED review_queue row exists for this
 * entity. This is the data-layer enforcement point: sendOutbound() in
 * lib/consent.js calls this before doing anything else. Returns the
 * approved row so callers can log which review authorized the send.
 */
export async function assertApproved(entityType, entityId) {
  const { records } = await listRecords("review_queue", {
    where: { entity_type: entityType, entity_id: String(entityId), status: REVIEW_STATUS.APPROVED },
    sort: "-decided_at",
    limit: 1,
  });
  if (records.length === 0) {
    throw new Error(
      `review.assertApproved: no approved review found for ${entityType}:${entityId}. ` +
        "Outbound actions require Full Review approval before they can send."
    );
  }
  return records[0];
}

export async function listPending(shopId, { riskLevel } = {}) {
  const where = { shop_id: shopId, status: REVIEW_STATUS.PENDING };
  if (riskLevel) where.risk_level = riskLevel;
  const { records } = await listRecords("review_queue", { where, sort: "-created_at", all: true });
  return records;
}
