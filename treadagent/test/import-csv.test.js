import { test, describe, beforeEach, after } from "node:test";
import assert from "node:assert/strict";
import { writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache } from "../src/lib/nocodb.js";
import { main as importCsv, parseCsv, validateRow, findInFileDuplicates } from "../scripts/import-csv.js";

let mock;
let dir;

beforeEach(async () => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
  mock.seed("shops", [{}]);
  dir = await mkdtemp(path.join(tmpdir(), "treadagent-csv-"));
});

const HEADER =
  "first_name,last_name,phone_e164,email,consent_type,vehicle_year,vehicle_make,vehicle_model,plate,season,quantity,brand,size\n";

async function writeCsv(rows) {
  const file = path.join(dir, "import.csv");
  await writeFile(file, HEADER + rows.join("\n"));
  return file;
}

describe("parseCsv", () => {
  test("parses a simple CSV into row objects keyed by header", () => {
    const { header, rows } = parseCsv("a,b\n1,2\n3,4");
    assert.deepEqual(header, ["a", "b"]);
    assert.deepEqual(rows, [{ a: "1", b: "2" }, { a: "3", b: "4" }]);
  });

  test("handles quoted fields with embedded commas and escaped quotes", () => {
    const { rows } = parseCsv('name,note\n"Smith, John","she said ""hi"""');
    assert.equal(rows[0].name, "Smith, John");
    assert.equal(rows[0].note, 'she said "hi"');
  });
});

describe("validateRow", () => {
  test("flags missing required columns", () => {
    const errors = validateRow({ first_name: "Pat" }, 2);
    assert.ok(errors.some((e) => e.includes("phone_e164")));
    assert.ok(errors.some((e) => e.includes("vehicle_year")));
  });

  test("flags an invalid season", () => {
    const errors = validateRow(
      { first_name: "Pat", phone_e164: "+1", vehicle_year: "2020", vehicle_make: "Honda", vehicle_model: "Civic", season: "spring", quantity: "4" },
      2
    );
    assert.ok(errors.some((e) => e.includes("season")));
  });

  test("passes a complete valid row", () => {
    const errors = validateRow(
      { first_name: "Pat", phone_e164: "+1", vehicle_year: "2020", vehicle_make: "Honda", vehicle_model: "Civic", season: "winter", quantity: "4" },
      2
    );
    assert.deepEqual(errors, []);
  });
});

describe("findInFileDuplicates", () => {
  test("flags a repeated phone number within the file", () => {
    const dups = findInFileDuplicates([{ phone_e164: "+1555" }, { phone_e164: "+1555" }]);
    assert.equal(dups.length, 1);
    assert.match(dups[0], /phone \+1555/);
  });

  test("flags a repeated plate within the file", () => {
    const dups = findInFileDuplicates([{ plate: "ABC123" }, { plate: "ABC123" }]);
    assert.equal(dups.length, 1);
  });
});

describe("main() end-to-end", () => {
  test("dry-run reports the plan but writes nothing", async () => {
    const file = await writeCsv([
      "Pat,Nguyen,+16045550001,pat@example.com,express,2020,Honda,Civic,ABC111,winter,4,Michelin,205/55R16",
    ]);
    const result = await importCsv({ file, shopId: 1, dryRun: true });
    assert.equal(result.imported, 0);
    assert.equal(mock.table("customers").rows.length, 0);
  });

  test("imports valid rows through the real intake agent", async () => {
    const file = await writeCsv([
      "Pat,Nguyen,+16045550001,pat@example.com,express,2020,Honda,Civic,ABC111,winter,4,Michelin,205/55R16",
      "Sam,Lee,+16045550002,sam@example.com,implied,2019,Toyota,Corolla,ABC222,summer,4,Bridgestone,215/60R16",
    ]);
    const result = await importCsv({ file, shopId: 1, dryRun: false });
    assert.equal(result.imported, 2);
    assert.equal(mock.table("customers").rows.length, 2);
    assert.equal(mock.table("tire_sets").rows.length, 2);
    assert.equal(mock.table("tires").rows.length, 8);
    // Consent capture flows through from the CSV.
    const pat = mock.table("customers").rows.find((c) => c.phone_e164 === "+16045550001");
    assert.equal(pat.consent_type, "express");
  });

  test("skips rows with validation errors and reports them", async () => {
    const file = await writeCsv([
      "Pat,Nguyen,+16045550001,pat@example.com,express,2020,Honda,Civic,ABC111,winter,4,Michelin,205/55R16",
      ",,,,,,,,,,,,", // entirely blank row — every required column missing
    ]);
    const result = await importCsv({ file, shopId: 1, dryRun: false });
    assert.equal(result.imported, 1);
    assert.ok(result.errors > 0);
  });

  test("skips in-file duplicate phone numbers", async () => {
    const file = await writeCsv([
      "Pat,Nguyen,+16045550001,pat@example.com,express,2020,Honda,Civic,ABC111,winter,4,Michelin,205/55R16",
      "PatAgain,Nguyen,+16045550001,pat@example.com,express,2021,Honda,Civic,ABC999,summer,4,Michelin,205/55R16",
    ]);
    const result = await importCsv({ file, shopId: 1, dryRun: false });
    assert.equal(result.duplicates, 1);
    assert.equal(result.imported, 1);
  });

  test("attaches a new vehicle/set to an existing customer found by phone, rather than duplicating them", async () => {
    const first = await writeCsv([
      "Pat,Nguyen,+16045550001,pat@example.com,express,2020,Honda,Civic,ABC111,winter,4,Michelin,205/55R16",
    ]);
    await importCsv({ file: first, shopId: 1, dryRun: false });

    const second = await writeCsv([
      "Pat,Nguyen,+16045550001,pat@example.com,express,2020,Honda,Civic,ABC112,summer,4,Bridgestone,205/55R16",
    ]);
    await importCsv({ file: second, shopId: 1, dryRun: false });

    assert.equal(mock.table("customers").rows.length, 1);
    assert.equal(mock.table("tire_sets").rows.length, 2);
  });
});

after(async () => {
  await rm(dir, { recursive: true, force: true }).catch(() => {});
});
