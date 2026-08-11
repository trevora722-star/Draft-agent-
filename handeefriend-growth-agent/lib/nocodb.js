const BASE = process.env.NOCODB_BASE_URL;
const KEY = process.env.NOCODB_API_KEY;
const BASE_ID = process.env.NOCODB_BASE_ID;

async function req(path, method = 'GET', body = null) {
  const res = await fetch(`${BASE}/api/v2${path}`, {
    method,
    headers: {
      'xc-token': KEY,
      'Content-Type': 'application/json',
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`NocoDB ${method} ${path}: ${res.status} ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const nocodb = {
  list: (table, { where = '', limit = 100, offset = 0, sort = '' } = {}) => {
    const params = new URLSearchParams();
    if (where) params.set('where', where);
    if (sort) params.set('sort', sort);
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    return req(`/db/data/noco/${BASE_ID}/${table}?${params.toString()}`);
  },

  get: (table, id) => req(`/db/data/noco/${BASE_ID}/${table}/${id}`),

  create: (table, data) =>
    req(`/db/data/noco/${BASE_ID}/${table}`, 'POST', data),

  update: (table, id, data) =>
    req(`/db/data/noco/${BASE_ID}/${table}/${id}`, 'PATCH', data),

  delete: (table, id) =>
    req(`/db/data/noco/${BASE_ID}/${table}/${id}`, 'DELETE'),

  bulkCreate: (table, rows) =>
    req(`/db/data/noco/${BASE_ID}/${table}/bulk`, 'POST', rows),

  // Convenience: list all rows handling pagination
  listAll: async (table, { where = '', sort = '', pageSize = 100 } = {}) => {
    const all = [];
    let offset = 0;
    while (true) {
      const page = await req(
        `/db/data/noco/${BASE_ID}/${table}?${new URLSearchParams({
          where,
          sort,
          limit: String(pageSize),
          offset: String(offset),
        }).toString()}`,
      );
      const rows = page.list || page.records || [];
      all.push(...rows);
      if (rows.length < pageSize) break;
      offset += pageSize;
    }
    return all;
  },

  // Find first row matching a where clause
  findOne: async (table, where) => {
    const res = await req(
      `/db/data/noco/${BASE_ID}/${table}?${new URLSearchParams({
        where,
        limit: '1',
      }).toString()}`,
    );
    const rows = res.list || res.records || [];
    return rows[0] || null;
  },
};
