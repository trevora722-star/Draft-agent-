// A minimal in-memory stand-in for the NocoDB v2 REST API, used by unit
// tests so lib/*.js can be exercised through the real src/lib/nocodb.js
// client (retry logic, query builder, table resolution and all) without
// a live NocoDB instance. Not a full reimplementation — just enough of
// the meta/list/create/update/delete/count surface for our tables.

const KNOWN_TABLES = [
  "shops",
  "regulations",
  "customers",
  "vehicles",
  "tire_sets",
  "tires",
  "tread_readings",
  "wear_projections",
  "rack_locations",
  "movements",
  "season_campaigns",
  "outreach_messages",
  "inbound_messages",
  "appointments",
  "capacity_slots",
  "dormancy_cases",
  "quotes",
  "review_queue",
  "audit_ledger",
  "agent_runs",
];

function matchClause(row, field, op, rawValue) {
  const value = rawValue === "true" ? true : rawValue === "false" ? false : rawValue;
  const cell = row[field];
  const cellNum = Number(cell);
  const valueNum = Number(value);
  switch (op) {
    case "eq":
      // eslint-disable-next-line eqeqeq
      return cell == value;
    case "neq":
      // eslint-disable-next-line eqeqeq
      return cell != value;
    case "gt":
      return cellNum > valueNum;
    case "gte":
      return cellNum >= valueNum;
    case "lt":
      return cellNum < valueNum;
    case "lte":
      return cellNum <= valueNum;
    case "like":
      return String(cell ?? "").includes(String(value).replace(/%/g, ""));
    case "is":
      return value === "null" ? cell === null || cell === undefined : cell === value;
    case "isnot":
      return value === "null" ? !(cell === null || cell === undefined) : cell !== value;
    default:
      throw new Error(`mock-nocodb: unsupported operator ${op}`);
  }
}

function applyWhere(rows, whereStr) {
  if (!whereStr) return rows;
  const clauseRe = /\(([^,]+),([a-z]+),([^)]*)\)/g;
  const clauses = [...whereStr.matchAll(clauseRe)];
  return rows.filter((row) => clauses.every(([, field, op, value]) => matchClause(row, field, op, value)));
}

function applySort(rows, sortStr) {
  if (!sortStr) return rows;
  const fields = sortStr.split(",");
  const sorted = [...rows];
  sorted.sort((a, b) => {
    for (const f of fields) {
      const desc = f.startsWith("-");
      const key = desc ? f.slice(1) : f;
      const av = a[key];
      const bv = b[key];
      if (av === bv) continue;
      const cmp = av > bv ? 1 : -1;
      return desc ? -cmp : cmp;
    }
    return 0;
  });
  return sorted;
}

export class MockNocoDB {
  constructor() {
    this.tables = new Map();
    for (const name of KNOWN_TABLES) {
      this.tables.set(name, { id: `tbl_${name}`, rows: [], nextId: 1 });
    }
  }

  table(name) {
    if (!this.tables.has(name)) {
      this.tables.set(name, { id: `tbl_${name}`, rows: [], nextId: 1 });
    }
    return this.tables.get(name);
  }

  seed(name, rows) {
    const t = this.table(name);
    for (const row of rows) {
      const id = t.nextId++;
      t.rows.push({ Id: id, ...row });
    }
    return t.rows;
  }

  findTableById(tableId) {
    for (const [name, t] of this.tables.entries()) {
      if (t.id === tableId) return { name, table: t };
    }
    return null;
  }

  fetch = async (urlInput, opts = {}) => {
    const url = new URL(String(urlInput));
    const method = opts.method || "GET";
    const body = opts.body ? JSON.parse(opts.body) : undefined;

    const metaListMatch = url.pathname.match(/^\/api\/v2\/meta\/bases\/[^/]+\/tables$/);
    if (metaListMatch && method === "GET") {
      const list = [...this.tables.entries()].map(([name, t]) => ({ id: t.id, table_name: name, title: name }));
      return jsonResponse(200, { list });
    }

    const recordsMatch = url.pathname.match(/^\/api\/v2\/tables\/([^/]+)\/records$/);
    if (recordsMatch) {
      const found = this.findTableById(recordsMatch[1]);
      if (!found) return jsonResponse(404, { msg: "table not found" });
      const { table } = found;

      if (method === "GET") {
        const where = url.searchParams.get("where");
        const sort = url.searchParams.get("sort");
        const limit = Number(url.searchParams.get("limit") || 100);
        const offset = Number(url.searchParams.get("offset") || 0);
        let rows = applyWhere(table.rows, where);
        rows = applySort(rows, sort);
        const page = rows.slice(offset, offset + limit);
        return jsonResponse(200, {
          list: page,
          pageInfo: { isLastPage: offset + page.length >= rows.length, totalRows: rows.length },
        });
      }

      if (method === "POST") {
        const items = Array.isArray(body) ? body : [body];
        const created = items.map((item) => {
          const id = table.nextId++;
          const row = { Id: id, ...item };
          table.rows.push(row);
          return row;
        });
        return jsonResponse(200, Array.isArray(body) ? created : created[0]);
      }

      if (method === "PATCH") {
        const idx = table.rows.findIndex((r) => r.Id === body.Id);
        if (idx === -1) return jsonResponse(404, { msg: "not found" });
        table.rows[idx] = { ...table.rows[idx], ...body };
        return jsonResponse(200, table.rows[idx]);
      }

      if (method === "DELETE") {
        const idx = table.rows.findIndex((r) => r.Id === body.Id);
        if (idx === -1) return jsonResponse(404, { msg: "not found" });
        const [removed] = table.rows.splice(idx, 1);
        return jsonResponse(200, removed);
      }
    }

    const countMatch = url.pathname.match(/^\/api\/v2\/tables\/([^/]+)\/records\/count$/);
    if (countMatch && method === "GET") {
      const found = this.findTableById(countMatch[1]);
      if (!found) return jsonResponse(404, { msg: "table not found" });
      const where = url.searchParams.get("where");
      const rows = applyWhere(found.table.rows, where);
      return jsonResponse(200, { count: rows.length });
    }

    const oneMatch = url.pathname.match(/^\/api\/v2\/tables\/([^/]+)\/records\/(\d+)$/);
    if (oneMatch && method === "GET") {
      const found = this.findTableById(oneMatch[1]);
      if (!found) return jsonResponse(404, { msg: "table not found" });
      const row = found.table.rows.find((r) => String(r.Id) === oneMatch[2]);
      if (!row) return jsonResponse(404, { msg: "not found" });
      return jsonResponse(200, row);
    }

    return jsonResponse(404, { msg: `mock-nocodb: no handler for ${method} ${url.pathname}` });
  };
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    headers: { get: () => null },
    text: async () => JSON.stringify(body),
  };
}

export function setMockEnv() {
  process.env.NOCODB_BASE_URL = "https://noco.mock.local";
  process.env.NOCODB_API_TOKEN = "mock-token";
  process.env.NOCODB_BASE_ID = "mock-base";
}
