// The ONLY module in TreadAgent that talks to NocoDB. Every other file
// reads/writes data through the functions exported here. This keeps a
// single choke point for retry/backoff behaviour, query construction,
// and (eventually) auditing of every data access.
//
// Targets the NocoDB v2 REST API (data: /api/v2/tables/{tableId}/records,
// meta: /api/v2/meta/...). Table names are resolved to NocoDB's internal
// table IDs and cached in-memory; call clearTableCache() in tests or
// after schema changes.

import { env } from "../config/env.js";

const DEFAULT_PAGE_SIZE = 100;
const MAX_RETRIES = 5;
const RETRY_BASE_MS = 300;

export class NocoDBError extends Error {
  constructor(message, { status, body, path } = {}) {
    super(message);
    this.name = "NocoDBError";
    this.status = status;
    this.body = body;
    this.path = path;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function backoffDelay(attempt, retryAfterHeader) {
  if (retryAfterHeader) {
    const seconds = Number(retryAfterHeader);
    if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  }
  const jitter = Math.random() * RETRY_BASE_MS;
  return RETRY_BASE_MS * 2 ** attempt + jitter;
}

function requireConfig() {
  const { baseUrl, apiToken, baseId } = env.nocodb;
  if (!baseUrl || !apiToken || !baseId) {
    throw new NocoDBError(
      "NocoDB is not configured: set NOCODB_BASE_URL, NOCODB_API_TOKEN, NOCODB_BASE_ID in .env"
    );
  }
  return { baseUrl: baseUrl.replace(/\/+$/, ""), apiToken, baseId };
}

// Injectable fetch so tests can mock the transport without a live server.
let fetchImpl = globalThis.fetch;
export function __setFetch(fn) {
  fetchImpl = fn;
}
export function __resetFetch() {
  fetchImpl = globalThis.fetch;
}

async function rawRequest(path, { method = "GET", query, body, retries = MAX_RETRIES } = {}) {
  const { baseUrl, apiToken } = requireConfig();
  const url = new URL(baseUrl + path);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      url.searchParams.set(key, String(value));
    }
  }

  let attempt = 0;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    let res;
    try {
      res = await fetchImpl(url.toString(), {
        method,
        headers: {
          "xc-token": apiToken,
          "Content-Type": "application/json",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (networkErr) {
      if (attempt >= retries) {
        throw new NocoDBError(`Network error calling NocoDB: ${networkErr.message}`, {
          path,
        });
      }
      await sleep(backoffDelay(attempt));
      attempt += 1;
      continue;
    }

    if (res.status === 429 || res.status >= 500) {
      if (attempt >= retries) {
        const text = await res.text().catch(() => "");
        throw new NocoDBError(`NocoDB request failed after ${retries} retries: ${res.status}`, {
          status: res.status,
          body: text,
          path,
        });
      }
      await sleep(backoffDelay(attempt, res.headers.get("retry-after")));
      attempt += 1;
      continue;
    }

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new NocoDBError(`NocoDB request failed: ${res.status} ${res.statusText}`, {
        status: res.status,
        body: text,
        path,
      });
    }

    if (res.status === 204) return null;
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }
}

// ---------------------------------------------------------------------
// Where-clause query builder
//
// Accepts either a raw NocoDB where-string, or a plain object of
// { field: value } (equality) / { field: { op: value } } pairs, and
// produces NocoDB's `(field,op,value)~and(...)` syntax.
// ---------------------------------------------------------------------

const OPS = new Set(["eq", "neq", "gt", "gte", "lt", "lte", "like", "nlike", "isnot", "is"]);

export function buildWhere(condition) {
  if (condition === undefined || condition === null) return undefined;
  if (typeof condition === "string") return condition;

  const clauses = Object.entries(condition).map(([field, spec]) => {
    if (spec !== null && typeof spec === "object" && !Array.isArray(spec)) {
      const [op, value] = Object.entries(spec)[0];
      if (!OPS.has(op)) {
        throw new Error(`Unsupported where operator "${op}" for field "${field}"`);
      }
      return `(${field},${op},${value})`;
    }
    return `(${field},eq,${spec})`;
  });
  return clauses.join("~and");
}

// ---------------------------------------------------------------------
// Table ID resolution (meta API), cached
// ---------------------------------------------------------------------

const tableCache = new Map(); // tableName -> tableId

export function clearTableCache() {
  tableCache.clear();
}

export async function listTablesMeta() {
  const { baseId } = requireConfig();
  const res = await rawRequest(`/api/v2/meta/bases/${baseId}/tables`);
  return res?.list || [];
}

export async function getTableId(tableName) {
  if (tableCache.has(tableName)) return tableCache.get(tableName);
  const tables = await listTablesMeta();
  for (const t of tables) {
    tableCache.set(t.table_name || t.title, t.id);
  }
  const id = tableCache.get(tableName);
  if (!id) {
    throw new NocoDBError(
      `NocoDB table "${tableName}" not found in base. Run scripts/provision-nocodb.js first.`
    );
  }
  return id;
}

export async function getTableMeta(tableName) {
  const tableId = await getTableId(tableName);
  return rawRequest(`/api/v2/meta/tables/${tableId}`);
}

export async function createTable(definition) {
  const { baseId } = requireConfig();
  const result = await rawRequest(`/api/v2/meta/bases/${baseId}/tables`, {
    method: "POST",
    body: definition,
  });
  tableCache.set(definition.table_name, result.id);
  return result;
}

export async function addColumn(tableName, columnDefinition) {
  const tableId = await getTableId(tableName);
  return rawRequest(`/api/v2/meta/tables/${tableId}/columns`, {
    method: "POST",
    body: columnDefinition,
  });
}

// ---------------------------------------------------------------------
// Record CRUD (data API)
// ---------------------------------------------------------------------

export async function listRecords(
  tableName,
  { where, sort, fields, limit = DEFAULT_PAGE_SIZE, offset = 0, all = false } = {}
) {
  const tableId = await getTableId(tableName);
  const query = {
    where: buildWhere(where),
    sort,
    fields: Array.isArray(fields) ? fields.join(",") : fields,
  };

  if (!all) {
    const res = await rawRequest(`/api/v2/tables/${tableId}/records`, {
      query: { ...query, limit, offset },
    });
    return { records: res?.list || [], pageInfo: res?.pageInfo };
  }

  const records = [];
  let currentOffset = offset;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const res = await rawRequest(`/api/v2/tables/${tableId}/records`, {
      query: { ...query, limit: DEFAULT_PAGE_SIZE, offset: currentOffset },
    });
    const page = res?.list || [];
    records.push(...page);
    if (!res?.pageInfo || res.pageInfo.isLastPage || page.length === 0) break;
    currentOffset += page.length;
  }
  return { records, pageInfo: { isLastPage: true, totalRows: records.length } };
}

export async function findOne(tableName, where) {
  const { records } = await listRecords(tableName, { where, limit: 1 });
  return records[0] || null;
}

export async function getRecord(tableName, id) {
  const tableId = await getTableId(tableName);
  return rawRequest(`/api/v2/tables/${tableId}/records/${id}`);
}

export async function createRecord(tableName, data) {
  const tableId = await getTableId(tableName);
  return rawRequest(`/api/v2/tables/${tableId}/records`, { method: "POST", body: data });
}

export async function createRecords(tableName, dataArray) {
  const tableId = await getTableId(tableName);
  return rawRequest(`/api/v2/tables/${tableId}/records`, { method: "POST", body: dataArray });
}

export async function updateRecord(tableName, id, data) {
  const tableId = await getTableId(tableName);
  return rawRequest(`/api/v2/tables/${tableId}/records`, {
    method: "PATCH",
    body: { Id: id, ...data },
  });
}

export async function deleteRecord(tableName, id) {
  const tableId = await getTableId(tableName);
  return rawRequest(`/api/v2/tables/${tableId}/records`, {
    method: "DELETE",
    body: { Id: id },
  });
}

export async function count(tableName, where) {
  const tableId = await getTableId(tableName);
  const res = await rawRequest(`/api/v2/tables/${tableId}/records/count`, {
    query: { where: buildWhere(where) },
  });
  return res?.count ?? 0;
}

export const _internal = { rawRequest, backoffDelay, requireConfig };
