import { config, features } from './config.js';

// Table schemas (NocoDB uidt types). JSON-ish fields are stored as LongText strings.
export const TABLE_SCHEMAS = {
  courses: [
    ['name', 'SingleLineText'],
    ['title', 'SingleLineText'],
    ['instructor', 'SingleLineText'],
    ['term', 'SingleLineText'],
    ['textbook_title', 'SingleLineText'],
    ['textbook_edition', 'SingleLineText'],
    ['grading_weights', 'LongText'],
    ['outline_file_url', 'SingleLineText'],
    ['lab_report_format', 'LongText'], // instructor's stated lab-report requirements (from outline)
    ['status', 'SingleLineText'], // generic | enriched
  ],
  topics: [
    ['course_id', 'Number'],
    ['name', 'SingleLineText'],
    ['week_number', 'Number'],
    ['textbook_chapter', 'SingleLineText'],
    ['status', 'SingleLineText'], // upcoming | current | covered
    ['difficulty_flag', 'SingleLineText'], // normal | known_weak_point
  ],
  key_dates: [
    ['course_id', 'Number'],
    ['type', 'SingleLineText'], // quiz | midterm | final | lab_report | assignment
    ['title', 'SingleLineText'],
    ['date', 'SingleLineText'], // ISO yyyy-mm-dd
    ['weight_pct', 'Number'],
  ],
  quiz_results: [
    ['topic_id', 'Number'],
    ['date', 'SingleLineText'],
    ['questions_asked', 'Number'],
    ['questions_correct', 'Number'],
    ['score_pct', 'Number'],
    ['weak_subtopics', 'LongText'],
  ],
  study_sessions: [
    ['date', 'SingleLineText'],
    ['course_id', 'Number'],
    ['topic_id', 'Number'],
    ['agent_used', 'SingleLineText'],
    ['duration_min', 'Number'],
    ['notes', 'LongText'],
  ],
  spaced_rep_queue: [
    ['topic_id', 'Number'],
    ['last_reviewed', 'SingleLineText'],
    ['next_due', 'SingleLineText'],
    ['interval_days', 'Number'],
    ['ease', 'SingleLineText'],
  ],
};

// ---------------------------------------------------------------------------
// NocoDB-backed store (REST API v2)
// ---------------------------------------------------------------------------

class NocoDBStore {
  constructor({ url, token, baseId }) {
    this.url = url;
    this.token = token;
    this.baseId = baseId;
    this.tableIds = {}; // table name -> NocoDB table id
  }

  async #req(method, path, body) {
    const res = await fetch(`${this.url}${path}`, {
      method,
      headers: {
        'xc-token': this.token,
        'content-type': 'application/json',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`NocoDB ${method} ${path} -> ${res.status}: ${text.slice(0, 300)}`);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  async init() {
    const listing = await this.#req('GET', `/api/v2/meta/bases/${this.baseId}/tables`);
    const existing = new Map((listing.list || []).map((t) => [t.table_name, t.id]));
    for (const [table, columns] of Object.entries(TABLE_SCHEMAS)) {
      if (existing.has(table)) {
        this.tableIds[table] = existing.get(table);
        continue;
      }
      const created = await this.#req('POST', `/api/v2/meta/bases/${this.baseId}/tables`, {
        table_name: table,
        title: table,
        columns: columns.map(([name, uidt]) => ({ column_name: name, title: name, uidt })),
      });
      this.tableIds[table] = created.id;
      console.log(`[nocodb] created table ${table} (${created.id})`);
    }
  }

  #tid(table) {
    const id = this.tableIds[table];
    if (!id) throw new Error(`Unknown table: ${table}`);
    return id;
  }

  #normalize(row) {
    if (!row) return row;
    const { Id, ...rest } = row;
    return { id: Id, ...rest };
  }

  async list(table, { where, sort, limit = 1000 } = {}) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (where) params.set('where', where);
    if (sort) params.set('sort', sort);
    const data = await this.#req('GET', `/api/v2/tables/${this.#tid(table)}/records?${params}`);
    return (data.list || []).map((r) => this.#normalize(r));
  }

  async create(table, data) {
    const created = await this.#req('POST', `/api/v2/tables/${this.#tid(table)}/records`, data);
    return this.#normalize({ ...data, ...created });
  }

  async update(table, id, data) {
    await this.#req('PATCH', `/api/v2/tables/${this.#tid(table)}/records`, [{ Id: id, ...data }]);
    return { id, ...data };
  }

  async remove(table, id) {
    await this.#req('DELETE', `/api/v2/tables/${this.#tid(table)}/records`, [{ Id: id }]);
  }
}

// ---------------------------------------------------------------------------
// In-memory fallback store (same interface; used when NocoDB is unconfigured)
// ---------------------------------------------------------------------------

class MemoryStore {
  constructor() {
    this.tables = {};
    this.counters = {};
    for (const t of Object.keys(TABLE_SCHEMAS)) {
      this.tables[t] = [];
      this.counters[t] = 0;
    }
  }

  async init() {}

  // Supports the small subset of NocoDB where-syntax this app uses:
  // "(field,eq,value)" — single condition only.
  #matches(row, where) {
    if (!where) return true;
    const m = /^\((\w+),eq,(.*)\)$/.exec(where);
    if (!m) return true;
    const [, field, raw] = m;
    const val = row[field];
    return String(val) === raw || Number(val) === Number(raw);
  }

  async list(table, { where, sort, limit = 1000 } = {}) {
    let rows = this.tables[table].filter((r) => this.#matches(r, where));
    if (sort) {
      const desc = sort.startsWith('-');
      const field = desc ? sort.slice(1) : sort;
      rows = [...rows].sort((a, b) => {
        const av = a[field] ?? '';
        const bv = b[field] ?? '';
        return (av < bv ? -1 : av > bv ? 1 : 0) * (desc ? -1 : 1);
      });
    }
    return rows.slice(0, limit).map((r) => ({ ...r }));
  }

  async create(table, data) {
    const row = { id: ++this.counters[table], ...data };
    this.tables[table].push(row);
    return { ...row };
  }

  async update(table, id, data) {
    const row = this.tables[table].find((r) => r.id === Number(id));
    if (row) Object.assign(row, data);
    return row ? { ...row } : null;
  }

  async remove(table, id) {
    this.tables[table] = this.tables[table].filter((r) => r.id !== Number(id));
  }
}

export function createStore() {
  if (features.nocodb) return new NocoDBStore(config.nocodb);
  return new MemoryStore();
}
