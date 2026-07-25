import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache, listRecords } from "../src/lib/nocodb.js";
import { main as provision } from "../scripts/provision-nocodb.js";
import { TABLES } from "../src/config/schema.js";

let mock;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  mock.tables.clear(); // start from a genuinely empty base
  clearTableCache();
  __setFetch(mock.fetch);
});

describe("provision-nocodb", () => {
  test("creates every table from the schema with all its columns", async () => {
    await provision();
    for (const t of TABLES) {
      const table = mock.table(t.name);
      const columnNames = new Set(table.columns.map((c) => c.column_name));
      for (const f of t.fields) {
        assert.ok(columnNames.has(f.name), `${t.name} missing column ${f.name}`);
      }
      assert.ok(columnNames.has("created_at"));
      assert.ok(columnNames.has("updated_at"));
    }
  });

  test("seeds all four regulation rows", async () => {
    await provision();
    const { records } = await listRecords("regulations", {});
    const provinces = records.map((r) => r.province).sort();
    assert.deepEqual(provinces, ["AB", "BC", "ON", "QC"]);
  });

  test("is idempotent — a second run adds no duplicate columns or regulation rows", async () => {
    await provision();
    const shopsAfterFirst = mock.table("shops").columns.length;
    const regsAfterFirst = (await listRecords("regulations", {})).records.length;

    clearTableCache();
    await provision();

    assert.equal(mock.table("shops").columns.length, shopsAfterFirst);
    const regsAfterSecond = (await listRecords("regulations", {})).records.length;
    assert.equal(regsAfterSecond, regsAfterFirst);
  });

  test("adds missing columns to a table that already exists but is incomplete", async () => {
    // Simulate a table that was created some other way, missing a couple of columns.
    const t = mock.table("customers");
    t.columns = [{ column_name: "first_name", title: "first_name", uidt: "SingleLineText" }];

    await provision();

    const columnNames = new Set(mock.table("customers").columns.map((c) => c.column_name));
    assert.ok(columnNames.has("phone_e164"));
    assert.ok(columnNames.has("consent_type"));
    // Original column should still be there exactly once.
    const firstNameCount = mock.table("customers").columns.filter((c) => c.column_name === "first_name").length;
    assert.equal(firstNameCount, 1);
  });
});
