#!/usr/bin/env node
// Idempotent NocoDB schema provisioning for TreadAgent. Safe to re-run:
// existing tables are left alone except for adding any columns that are
// in src/config/schema.js but missing from the live table. Also seeds
// the `regulations` table's baseline provincial values (see the loud
// comment below — those values MUST be verified before a shop goes
// live; this script does not and cannot confirm they're current).
//
// Usage:
//   node scripts/provision-nocodb.js --dry-run   # print the plan only, no network calls, no credentials needed
//   node scripts/provision-nocodb.js             # create/verify tables and seed regulations against the real NocoDB base

import { TABLES } from "../src/config/schema.js";
import {
  listTablesMeta,
  getTableMeta,
  createTable,
  addColumn,
  listRecords,
  createRecord,
} from "../src/lib/nocodb.js";

const DRY_RUN = process.argv.includes("--dry-run");

const UIDT_MAP = {
  text: "SingleLineText",
  longtext: "LongText",
  int: "Number",
  decimal: "Decimal",
  bool: "Checkbox",
  date: "Date",
  datetime: "DateTime",
  select: "SingleSelect",
};

function toColumnDef(field) {
  const uidt = UIDT_MAP[field.type];
  if (!uidt) throw new Error(`provision-nocodb: unknown field type "${field.type}" for ${field.name}`);
  const def = { column_name: field.name, title: field.name, uidt };
  if (field.type === "select") {
    def.colOptions = { options: field.options.map((o) => ({ title: o })) };
  }
  return def;
}

const SYSTEM_COLUMNS = [
  { column_name: "created_at", title: "created_at", uidt: "CreatedTime" },
  { column_name: "updated_at", title: "updated_at", uidt: "LastModifiedTime" },
];

function planForTable(table) {
  return {
    table_name: table.name,
    columns: [...table.fields.map(toColumnDef), ...SYSTEM_COLUMNS],
  };
}

// -----------------------------------------------------------------
// Regulations seed — values taken from the spec. THE OPERATOR MUST
// VERIFY THESE AGAINST THE CURRENT PROVINCIAL AUTHORITY BEFORE ANY
// SHOP GOES LIVE. Tread-safety thresholds and legal minimums are
// legislated and do change; this seed is a starting point, not a
// source of truth. See CLAUDE.md and BLOCKERS.md.
// -----------------------------------------------------------------
const REGULATIONS_SEED = [
  {
    province: "BC",
    winter_window_start: "10-01",
    winter_window_end: "04-30",
    route_note: "Some designated routes end Mar 31 rather than Apr 30 — verify per route with DriveBC.",
    legal_min_32nds: 2, // 1.6mm
    winter_designation_min_mm: 3.5,
    marking_required: "M+S or 3PMSF",
    source_url: "https://www2.gov.bc.ca/gov/content/transportation/driving-and-cycling/traveller-information/seasonal/winter-driving",
    verified_on: null, // NOT verified by this script — operator must confirm and set this date
  },
  {
    province: "AB",
    winter_window_start: "10-01",
    winter_window_end: "04-30",
    route_note: "No provincewide mandatory winter-tire law as of spec authoring — verify current status.",
    legal_min_32nds: 2,
    winter_designation_min_mm: null,
    marking_required: null,
    source_url: "https://www.alberta.ca/",
    verified_on: null,
  },
  {
    province: "ON",
    winter_window_start: "10-01",
    winter_window_end: "04-30",
    route_note: "No provincewide mandatory winter-tire law; insurers may offer discounts — verify current status.",
    legal_min_32nds: 2,
    winter_designation_min_mm: null,
    marking_required: null,
    source_url: "https://www.ontario.ca/",
    verified_on: null,
  },
  {
    province: "QC",
    winter_window_start: "12-01",
    winter_window_end: "03-15",
    route_note: "Quebec mandates winter tires for this window under the Highway Safety Code — verify exact dates annually.",
    legal_min_32nds: 2,
    winter_designation_min_mm: null,
    marking_required: "mountain/snowflake pictogram or M+S per SAAQ",
    source_url: "https://saaq.gouv.qc.ca/",
    verified_on: null,
  },
];

async function provisionTables() {
  const existing = DRY_RUN ? [] : await listTablesMeta();
  const existingNames = new Set(existing.map((t) => t.table_name || t.title));

  for (const table of TABLES) {
    const plan = planForTable(table);

    if (!existingNames.has(table.name)) {
      console.log(`[create] ${table.name} (${plan.columns.length} columns)`);
      if (!DRY_RUN) {
        await createTable(plan);
      }
      continue;
    }

    console.log(`[exists] ${table.name} — checking for missing columns`);
    if (DRY_RUN) continue;

    const meta = await getTableMeta(table.name);
    const existingCols = new Set((meta.columns || []).map((c) => c.column_name || c.title));
    const missing = plan.columns.filter((c) => !existingCols.has(c.column_name));
    for (const col of missing) {
      console.log(`  [add column] ${table.name}.${col.column_name}`);
      await addColumn(table.name, col);
    }
    if (missing.length === 0) console.log(`  (up to date)`);
  }
}

async function seedRegulations() {
  console.log("\nSeeding regulations table (idempotent by province)...");
  if (DRY_RUN) {
    for (const r of REGULATIONS_SEED) console.log(`[would seed] regulations: ${r.province}`);
    return;
  }
  for (const reg of REGULATIONS_SEED) {
    const { records } = await listRecords("regulations", { where: { province: reg.province }, limit: 1 });
    if (records.length > 0) {
      console.log(`[skip] regulations.${reg.province} already present`);
      continue;
    }
    await createRecord("regulations", reg);
    console.log(`[seeded] regulations.${reg.province}`);
  }
}

export async function main() {
  console.log(`TreadAgent NocoDB provisioning ${DRY_RUN ? "(dry run — no network calls, no writes)" : ""}`);
  console.log("=".repeat(70));
  await provisionTables();
  await seedRegulations();
  console.log("\nDone." + (DRY_RUN ? " Re-run without --dry-run to apply." : ""));
  if (!DRY_RUN) {
    console.log(
      "\nREMINDER: verify every row in the `regulations` table against the current\n" +
        "provincial authority before this shop goes live. See BLOCKERS.md."
    );
  }
}

// Only auto-run when invoked directly (`node scripts/provision-nocodb.js`),
// not when imported by tests.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error("provision-nocodb failed:", err.message);
    process.exitCode = 1;
  });
}
