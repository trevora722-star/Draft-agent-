import { test, describe } from "node:test";
import assert from "node:assert/strict";
import {
  buildWhere,
  listRecords,
  createRecord,
  clearTableCache,
  __setFetch,
  __resetFetch,
  _internal,
} from "../src/lib/nocodb.js";

async function withEnv(overrides, fn) {
  const prev = {
    NOCODB_BASE_URL: process.env.NOCODB_BASE_URL,
    NOCODB_API_TOKEN: process.env.NOCODB_API_TOKEN,
    NOCODB_BASE_ID: process.env.NOCODB_BASE_ID,
  };
  Object.assign(process.env, {
    NOCODB_BASE_URL: "https://noco.example.com",
    NOCODB_API_TOKEN: "test-token",
    NOCODB_BASE_ID: "base123",
    ...overrides,
  });
  try {
    return await fn();
  } finally {
    Object.assign(process.env, prev);
  }
}

describe("buildWhere", () => {
  test("equality shorthand", () => {
    assert.equal(buildWhere({ shop_id: 5 }), "(shop_id,eq,5)");
  });

  test("multiple fields joined with ~and", () => {
    assert.equal(
      buildWhere({ shop_id: 5, status: "pending" }),
      "(shop_id,eq,5)~and(status,eq,pending)"
    );
  });

  test("operator object form", () => {
    assert.equal(buildWhere({ min_32nds: { lt: 4 } }), "(min_32nds,lt,4)");
  });

  test("rejects unsupported operator", () => {
    assert.throws(() => buildWhere({ field: { bogus: 1 } }));
  });

  test("passes through raw string unchanged", () => {
    assert.equal(buildWhere("(a,eq,1)"), "(a,eq,1)");
  });

  test("undefined for no condition", () => {
    assert.equal(buildWhere(undefined), undefined);
  });
});

describe("record CRUD against mocked transport", () => {
  test("listRecords resolves table id then fetches records", () =>
    withEnv({}, async () => {
      clearTableCache();
      const calls = [];
      __setFetch(async (url, opts) => {
        calls.push({ url: url.toString(), opts });
        if (url.toString().includes("/meta/bases/")) {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({ list: [{ id: "tbl_customers", table_name: "customers" }] }),
          };
        }
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({ list: [{ Id: 1, first_name: "Pat" }], pageInfo: { isLastPage: true } }),
        };
      });

      const { records } = await listRecords("customers", { where: { shop_id: 1 } });
      assert.equal(records.length, 1);
      assert.equal(records[0].first_name, "Pat");
      assert.ok(calls[1].url.includes("where="));
      __resetFetch();
    }));

  test("createRecord posts to the resolved table", () =>
    withEnv({}, async () => {
      clearTableCache();
      __setFetch(async (url) => {
        if (url.toString().includes("/meta/bases/")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify({ list: [{ id: "tbl_x", table_name: "customers" }] }),
          };
        }
        return { ok: true, status: 200, text: async () => JSON.stringify({ Id: 42 }) };
      });
      const created = await createRecord("customers", { first_name: "Jo" });
      assert.equal(created.Id, 42);
      __resetFetch();
    }));

  test("retries on 429 then succeeds", () =>
    withEnv({}, async () => {
      clearTableCache();
      let attempts = 0;
      __setFetch(async (url) => {
        if (url.toString().includes("/meta/bases/")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify({ list: [{ id: "tbl_x", table_name: "customers" }] }),
          };
        }
        attempts += 1;
        if (attempts < 2) {
          return {
            ok: false,
            status: 429,
            headers: { get: () => "0" },
            text: async () => "rate limited",
          };
        }
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify({ list: [], pageInfo: { isLastPage: true } }),
        };
      });
      const { records } = await listRecords("customers", {});
      assert.deepEqual(records, []);
      assert.equal(attempts, 2);
      __resetFetch();
    }));

  test("throws NocoDBError when required env is missing", () =>
    withEnv({ NOCODB_BASE_URL: "", NOCODB_API_TOKEN: "", NOCODB_BASE_ID: "" }, async () => {
      await assert.rejects(() => listRecords("customers", {}));
    }));
});

describe("backoffDelay", () => {
  test("respects Retry-After header in seconds", () => {
    assert.equal(_internal.backoffDelay(0, "2"), 2000);
  });

  test("grows exponentially without a header", () => {
    const d0 = _internal.backoffDelay(0);
    const d1 = _internal.backoffDelay(1);
    assert.ok(d1 > d0);
  });
});
