// Shared wrapper every agent's run() uses to log itself to agent_runs.
// Not a table in the spec's repo tree, but keeps the "every agent logs
// a row to agent_runs" contract (CLAUDE.md) from being reimplemented
// 12 times slightly differently.

import { createRecord, updateRecord } from "./nocodb.js";

/**
 * @param {{agentKey:string, shopId:number|string, inputSummary?:string}} meta
 * @param {() => Promise<{summary?:string, recordsTouched?:number, tokensIn?:number, tokensOut?:number, costCad?:number}>} fn
 */
export async function withAgentRun({ agentKey, shopId, inputSummary = "" }, fn) {
  const row = await createRecord("agent_runs", {
    agent_key: agentKey,
    shop_id: shopId,
    started_at: new Date().toISOString(),
    status: "running",
    input_summary: inputSummary,
  });

  try {
    const result = (await fn()) ?? {};
    await updateRecord("agent_runs", row.Id, {
      ended_at: new Date().toISOString(),
      status: "success",
      output_summary: result.summary ?? "",
      records_touched: result.recordsTouched ?? 0,
      tokens_in: result.tokensIn ?? 0,
      tokens_out: result.tokensOut ?? 0,
      cost_cad: result.costCad ?? 0,
    });
    return result;
  } catch (err) {
    await updateRecord("agent_runs", row.Id, {
      ended_at: new Date().toISOString(),
      status: "error",
      error: err.message,
    });
    throw err;
  }
}
