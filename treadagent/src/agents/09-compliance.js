#!/usr/bin/env node
// Agent 9: compliance — ledger integrity verification, consent audit,
// notice proof, retention policy. Deterministic — no LLM. This agent
// never sends anything; it only reports. It re-checks what
// sendOutbound() should already have enforced, because an audit that
// only trusts the enforcement point it's auditing isn't an audit.

import { listRecords } from "../lib/nocodb.js";
import { verifyChain, append } from "../lib/ledger.js";
import { withAgentRun } from "../lib/agent-run.js";

const RETENTION_MIN_YEARS = 3;

export async function auditConsent(shopId) {
  const { records: customers } = await listRecords("customers", { where: { shop_id: shopId }, all: true });
  const summary = { express: 0, implied: 0, none: 0, unsubscribed: 0, total: customers.length };
  for (const c of customers) {
    if (c.unsubscribed_at) summary.unsubscribed += 1;
    if (c.consent_type === "express") summary.express += 1;
    else if (c.consent_type === "implied") summary.implied += 1;
    else summary.none += 1;
  }
  return summary;
}

/**
 * Re-derive, from each SENT message's frozen consent_snapshot_json,
 * whether it should ever have been allowed to send. sendOutbound()
 * enforces this at send time; this function catches anything that
 * slipped through some other path (a manual DB edit, a bug, a bypass).
 */
export async function auditUnauthorizedSends(shopId) {
  const { records: sent } = await listRecords("outreach_messages", {
    where: { shop_id: shopId, status: "sent" },
    all: true,
  });

  const violations = [];
  for (const message of sent) {
    if (!message.consent_snapshot_json) {
      violations.push({ messageId: message.Id, reason: "no consent_snapshot_json recorded at send time" });
      continue;
    }
    let snapshot;
    try {
      snapshot = JSON.parse(message.consent_snapshot_json);
    } catch {
      violations.push({ messageId: message.Id, reason: "consent_snapshot_json is not valid JSON" });
      continue;
    }
    if (!snapshot.consent_type || snapshot.consent_type === "none") {
      violations.push({ messageId: message.Id, reason: "sent with consent_type none" });
    }
    if (snapshot.unsubscribed_at) {
      violations.push({ messageId: message.Id, reason: `sent after unsubscribe at ${snapshot.unsubscribed_at}` });
    }
  }
  return { checked: sent.length, violations };
}

export async function auditDormancyEvidence(shopId) {
  const { records: cases } = await listRecords("dormancy_cases", { where: { shop_id: shopId }, all: true });
  const gaps = [];
  for (const c of cases) {
    if (c.stage >= 3 && (!c.notices_json || c.notices_json === "[]" || c.notices_json === "")) {
      gaps.push({ caseId: c.Id, stage: c.stage, reason: "stage >= 3 but no notices recorded" });
    }
    if (c.stage >= 5 && !c.evidence_bundle_hash) {
      gaps.push({ caseId: c.Id, stage: c.stage, reason: "stage 5 (sealed) but no evidence_bundle_hash" });
    }
  }
  return { casesChecked: cases.length, gaps };
}

/** Reports the oldest consent-bearing record's age as evidence the 3-year retention minimum is being met (TreadAgent never deletes ledger/consent rows — see CLAUDE.md). */
export async function checkRetention(shopId, now = new Date()) {
  const { records: customers } = await listRecords("customers", { where: { shop_id: shopId }, all: true });
  const timestamps = customers.map((c) => c.consent_timestamp).filter(Boolean).map((t) => new Date(t).getTime());
  if (timestamps.length === 0) {
    return { ok: true, oldestConsentAgeYears: 0, minRequiredYears: RETENTION_MIN_YEARS };
  }
  const oldest = Math.min(...timestamps);
  const ageYears = (now.getTime() - oldest) / (365.25 * 86_400_000);
  return { ok: true, oldestConsentAgeYears: Number(ageYears.toFixed(2)), minRequiredYears: RETENTION_MIN_YEARS };
}

export async function run(context) {
  const { shopId, actorId = "compliance-agent" } = context;
  if (!shopId) throw new Error("compliance.run: shopId is required");

  return withAgentRun({ agentKey: "compliance", shopId, inputSummary: "full compliance sweep" }, async () => {
    const [chain, consent, sends, dormancy, retention] = await Promise.all([
      verifyChain(shopId),
      auditConsent(shopId),
      auditUnauthorizedSends(shopId),
      auditDormancyEvidence(shopId),
      checkRetention(shopId),
    ]);

    const report = {
      generatedAt: new Date().toISOString(),
      ledger: chain,
      consent,
      unauthorizedSends: sends,
      dormancyEvidence: dormancy,
      retention,
    };

    const clean =
      chain.ok && sends.violations.length === 0 && dormancy.gaps.length === 0;

    await append({
      shopId,
      actorType: "agent",
      actorId,
      action: "compliance_sweep_completed",
      entityType: "shops",
      entityId: shopId,
      payload: {
        ledger_ok: chain.ok,
        unauthorized_send_count: sends.violations.length,
        dormancy_gap_count: dormancy.gaps.length,
        clean,
      },
    });

    return {
      summary: clean
        ? "Compliance sweep clean: ledger intact, no unauthorized sends, no dormancy evidence gaps."
        : `Compliance sweep found issues — ledger ok: ${chain.ok}, unauthorized sends: ${sends.violations.length}, dormancy gaps: ${dormancy.gaps.length}`,
      recordsTouched: 0,
      report,
    };
  });
}

export default { run };

if (import.meta.url === `file://${process.argv[1]}`) {
  const contextJson = process.argv[2];
  if (!contextJson) {
    console.error("Usage: node src/agents/09-compliance.js '<json context>'");
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
