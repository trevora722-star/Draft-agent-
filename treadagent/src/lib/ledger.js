// SHA-256 append-only audit ledger. Every state change and every
// outbound action in TreadAgent writes a row here, chained to the
// previous row's hash. Rows are never updated or deleted — only
// appended and verified. See CLAUDE.md constraint #4.

import { createHash } from "node:crypto";
import { createRecord, listRecords } from "./nocodb.js";

export const GENESIS_HASH = "0".repeat(64);

// Recursively sorts object keys so the same logical payload always
// serializes to the same string, regardless of key insertion order.
// Arrays keep their original order (order is semantically meaningful).
export function canonicalJSON(value) {
  return JSON.stringify(canonicalize(value));
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value !== null && typeof value === "object") {
    const sorted = {};
    for (const key of Object.keys(value).sort()) {
      sorted[key] = canonicalize(value[key]);
    }
    return sorted;
  }
  return value;
}

export function sha256Hex(input) {
  return createHash("sha256").update(input, "utf8").digest("hex");
}

function computeHash({ seq, timestamp, canonicalPayload, prevHash }) {
  return sha256Hex(`${seq}${timestamp}${canonicalPayload}${prevHash}`);
}

async function lastRow(shopId) {
  const { records } = await listRecords("audit_ledger", {
    where: { shop_id: shopId },
    sort: "-seq",
    limit: 1,
  });
  return records[0] || null;
}

/**
 * Append a row to the audit ledger.
 * @param {object} entry
 * @param {number|string} entry.shopId
 * @param {"agent"|"staff"|"customer"|"system"} entry.actorType
 * @param {string} entry.actorId
 * @param {string} entry.action
 * @param {string} entry.entityType
 * @param {string|number} entry.entityId
 * @param {object} [entry.payload]
 */
export async function append({ shopId, actorType, actorId, action, entityType, entityId, payload = {} }) {
  if (!shopId) throw new Error("ledger.append: shopId is required");
  if (!actorType || !action || !entityType || entityId === undefined) {
    throw new Error("ledger.append: actorType, action, entityType, entityId are required");
  }

  const previous = await lastRow(shopId);
  const seq = previous ? previous.seq + 1 : 1;
  const prevHash = previous ? previous.hash : GENESIS_HASH;
  const timestamp = new Date().toISOString();
  const canonicalPayload = canonicalJSON(payload);
  const hash = computeHash({ seq, timestamp, canonicalPayload, prevHash });

  return createRecord("audit_ledger", {
    seq,
    shop_id: shopId,
    timestamp_utc: timestamp,
    actor_type: actorType,
    actor_id: actorId ?? "",
    action,
    entity_type: entityType,
    entity_id: String(entityId),
    payload_json: canonicalPayload,
    prev_hash: prevHash,
    hash,
  });
}

/**
 * Walk the entire chain for a shop and confirm every row's hash matches
 * its recomputed value and every prev_hash matches the prior row's hash,
 * with no gaps in seq. Returns the first break found, if any.
 */
export async function verifyChain(shopId) {
  const { records } = await listRecords("audit_ledger", {
    where: { shop_id: shopId },
    sort: "seq",
    all: true,
  });

  let expectedPrevHash = GENESIS_HASH;
  let expectedSeq = 1;

  for (const row of records) {
    if (row.seq !== expectedSeq) {
      return {
        ok: false,
        brokenAtSeq: row.seq,
        reason: `sequence gap: expected seq ${expectedSeq}, found ${row.seq}`,
      };
    }
    if (row.prev_hash !== expectedPrevHash) {
      return {
        ok: false,
        brokenAtSeq: row.seq,
        reason: `prev_hash mismatch at seq ${row.seq}: expected ${expectedPrevHash}, found ${row.prev_hash}`,
      };
    }
    const recomputed = computeHash({
      seq: row.seq,
      timestamp: row.timestamp_utc,
      canonicalPayload: row.payload_json,
      prevHash: row.prev_hash,
    });
    if (recomputed !== row.hash) {
      return {
        ok: false,
        brokenAtSeq: row.seq,
        reason: `hash mismatch at seq ${row.seq}: stored row does not match recomputed hash (tampered?)`,
      };
    }
    expectedPrevHash = row.hash;
    expectedSeq += 1;
  }

  return { ok: true, length: records.length, headHash: expectedPrevHash === GENESIS_HASH ? null : expectedPrevHash };
}

/**
 * Hash an evidence bundle (e.g. a dormancy-case PDF buffer) and write a
 * ledger row recording the seal event. Returns the hash so callers can
 * store it alongside the bundle reference (e.g. dormancy_cases.evidence_bundle_hash).
 */
export async function sealBundle({ shopId, actorId, entityType, entityId, buffer }) {
  const hash = sha256Hex(buffer);
  await append({
    shopId,
    actorType: "system",
    actorId: actorId ?? "system",
    action: "seal_evidence_bundle",
    entityType,
    entityId,
    payload: { bundle_sha256: hash, sealed_at: new Date().toISOString() },
  });
  return hash;
}
