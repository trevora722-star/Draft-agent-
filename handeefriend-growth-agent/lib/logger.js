import { nocodb } from './nocodb.js';

export class AgentLogger {
  constructor(agentId, agentName, dryRun = false) {
    this.agentId = agentId;
    this.agentName = agentName;
    this.dryRun = dryRun;
    this.startTime = Date.now();
    this.records_processed = 0;
    this.records_created = 0;
    this.emails_sent = 0;
    this.errors = [];
  }

  increment(field, n = 1) {
    this[field] = (this[field] || 0) + n;
  }

  error(msg, data = {}) {
    console.error(`[Agent ${this.agentId}] ERROR:`, msg, data);
    this.errors.push({ msg, data, ts: new Date().toISOString() });
  }

  log(msg) {
    console.log(`[Agent ${this.agentId} ${this.agentName}] ${msg}`);
  }

  async finish(status = 'success') {
    const finalStatus = this.errors.length > 0 ? 'partial' : status;
    const entry = {
      agent_id: this.agentId,
      agent_name: this.agentName,
      run_date: new Date().toISOString(),
      status: finalStatus,
      records_processed: this.records_processed,
      records_created: this.records_created,
      emails_sent: this.emails_sent,
      errors: JSON.stringify(this.errors),
      dry_run: this.dryRun,
      duration_ms: Date.now() - this.startTime,
    };

    if (!this.dryRun) {
      try {
        await nocodb.create('agent_logs', entry);
      } catch (e) {
        console.error(`[Agent ${this.agentId}] Failed to write agent_log:`, e.message);
      }
    }

    console.log(
      `[Agent ${this.agentId}] DONE — status=${finalStatus} processed=${this.records_processed} created=${this.records_created} emails=${this.emails_sent} duration=${entry.duration_ms}ms${this.dryRun ? ' [DRY-RUN]' : ''}`,
    );
    return entry;
  }
}
