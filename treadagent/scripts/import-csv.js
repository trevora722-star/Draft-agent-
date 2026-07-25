#!/usr/bin/env node
// CSV onboarding import: customers, vehicles, and existing storage
// inventory in one row per tire set. This is how every shop onboards —
// no POS integrations in v1 (see the build spec). Validates and
// reports before writing anything; --dry-run never touches the DB.
// Duplicate detection on phone (customers) and plate (vehicles), both
// within the file itself and against what's already in the shop.
//
// Reuses agents/01-intake.js for every valid row so an imported set
// goes through the exact same consent-capture / rack-assignment /
// ledger path as a live intake — this is not a bulk-insert shortcut.
//
// Expected CSV header (order doesn't matter, extra columns ignored):
//   first_name,last_name,phone_e164,email,preferred_channel,preferred_language,
//   consent_type,consent_source,
//   vehicle_year,vehicle_make,vehicle_model,plate,vin,drive_type,
//   season,quantity,on_wheels,wheel_type,brand,model,size,dot_week,dot_year,
//   storage_fee_cad,site
//
// Usage:
//   node scripts/import-csv.js --file=./customers.csv --shop=1 --dry-run
//   node scripts/import-csv.js --file=./customers.csv --shop=1

import { readFile } from "node:fs/promises";
import { findOne } from "../src/lib/nocodb.js";
import { run as intakeRun } from "../src/agents/01-intake.js";

const REQUIRED_COLUMNS = ["first_name", "phone_e164", "vehicle_year", "vehicle_make", "vehicle_model", "season", "quantity"];

function parseArgs(argv) {
  return Object.fromEntries(
    argv.map((a) => {
      const [k, v] = a.replace(/^--/, "").split("=");
      return [k, v ?? true];
    })
  );
}

/** Minimal RFC4180-ish CSV parser: handles quoted fields with embedded commas and escaped quotes ("" inside a quoted field). No embedded-newline support — one row per line. */
export function parseCsv(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length === 0) return { header: [], rows: [] };

  function parseLine(line) {
    const fields = [];
    let cur = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (inQuotes) {
        if (ch === '"' && line[i + 1] === '"') {
          cur += '"';
          i++;
        } else if (ch === '"') {
          inQuotes = false;
        } else {
          cur += ch;
        }
      } else if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        fields.push(cur);
        cur = "";
      } else {
        cur += ch;
      }
    }
    fields.push(cur);
    return fields.map((f) => f.trim());
  }

  const header = parseLine(lines[0]);
  const rows = lines.slice(1).map((line) => {
    const values = parseLine(line);
    return Object.fromEntries(header.map((col, i) => [col, values[i] ?? ""]));
  });
  return { header, rows };
}

export function validateRow(row, rowNumber) {
  const errors = [];
  for (const col of REQUIRED_COLUMNS) {
    if (!row[col] || String(row[col]).trim() === "") errors.push(`row ${rowNumber}: missing required column "${col}"`);
  }
  if (row.quantity && (Number.isNaN(Number(row.quantity)) || Number(row.quantity) < 1)) {
    errors.push(`row ${rowNumber}: quantity must be a positive number`);
  }
  if (row.vehicle_year && Number.isNaN(Number(row.vehicle_year))) {
    errors.push(`row ${rowNumber}: vehicle_year must be a number`);
  }
  if (row.season && !["summer", "winter", "all_season"].includes(row.season)) {
    errors.push(`row ${rowNumber}: season must be summer, winter, or all_season`);
  }
  return errors;
}

/** Duplicate detection within the file itself, on phone and plate. */
export function findInFileDuplicates(rows) {
  const seenPhones = new Map();
  const seenPlates = new Map();
  const duplicates = [];
  rows.forEach((row, i) => {
    const rowNumber = i + 2; // +1 for header, +1 for 1-indexing
    if (row.phone_e164) {
      if (seenPhones.has(row.phone_e164)) {
        duplicates.push(`row ${rowNumber}: phone ${row.phone_e164} duplicates row ${seenPhones.get(row.phone_e164)} in this file`);
      } else {
        seenPhones.set(row.phone_e164, rowNumber);
      }
    }
    if (row.plate) {
      if (seenPlates.has(row.plate)) {
        duplicates.push(`row ${rowNumber}: plate ${row.plate} duplicates row ${seenPlates.get(row.plate)} in this file`);
      } else {
        seenPlates.set(row.plate, rowNumber);
      }
    }
  });
  return duplicates;
}

async function findExistingDuplicate(shopId, row) {
  if (row.phone_e164) {
    const existing = await findOne("customers", { shop_id: shopId, phone_e164: row.phone_e164 });
    if (existing) return `existing customer ${existing.Id} already has phone ${row.phone_e164} (will attach this vehicle/set to them)`;
  }
  return null;
}

function rowToIntakeContext(shopId, row) {
  return {
    shopId,
    actorId: "import-csv",
    customer: {
      first_name: row.first_name,
      last_name: row.last_name || "",
      phone_e164: row.phone_e164,
      email: row.email || "",
      preferred_channel: row.preferred_channel || "sms",
      preferred_language: row.preferred_language || "en",
      consent_type: row.consent_type || "none",
      consent_source: row.consent_source || "csv_import",
    },
    vehicle: {
      year: Number(row.vehicle_year),
      make: row.vehicle_make,
      model: row.vehicle_model,
      plate: row.plate || "",
      vin: row.vin || "",
      drive_type: row.drive_type || "",
    },
    tireSet: {
      season: row.season,
      quantity: Number(row.quantity),
      on_wheels: String(row.on_wheels).toLowerCase() === "true",
      wheel_type: row.wheel_type || "",
      brand: row.brand || "",
      model: row.model || "",
      size: row.size || "",
      dot_week: row.dot_week ? Number(row.dot_week) : null,
      dot_year: row.dot_year ? Number(row.dot_year) : null,
      storage_fee_cad: row.storage_fee_cad ? Number(row.storage_fee_cad) : 0,
      site: row.site || "on_site",
    },
  };
}

export async function main({ file, shopId, dryRun } = {}) {
  const args = parseArgs(process.argv.slice(2));
  file = file ?? args.file;
  shopId = shopId ?? Number(args.shop);
  dryRun = dryRun ?? Boolean(args["dry-run"]);

  if (!file || !shopId) {
    throw new Error("import-csv: --file and --shop are required");
  }

  const text = await readFile(file, "utf8");
  const { rows } = parseCsv(text);

  console.log(`Parsed ${rows.length} rows from ${file}`);

  const rowErrors = [];
  rows.forEach((row, i) => rowErrors.push(...validateRow(row, i + 2)));
  const fileDuplicates = findInFileDuplicates(rows);

  console.log("=".repeat(60));
  console.log("VALIDATION REPORT");
  console.log(`  Total rows: ${rows.length}`);
  console.log(`  Validation errors: ${rowErrors.length}`);
  rowErrors.forEach((e) => console.log(`    ✗ ${e}`));
  console.log(`  In-file duplicates: ${fileDuplicates.length}`);
  fileDuplicates.forEach((d) => console.log(`    ⚠ ${d}`));

  const invalidRowNumbers = new Set(
    rowErrors.map((e) => Number(e.match(/^row (\d+)/)?.[1])).filter(Boolean)
  );
  const duplicateRowNumbers = new Set(
    fileDuplicates.map((d) => Number(d.match(/^row (\d+)/)?.[1])).filter(Boolean)
  );

  const importable = rows
    .map((row, i) => ({ row, rowNumber: i + 2 }))
    .filter(({ rowNumber }) => !invalidRowNumbers.has(rowNumber) && !duplicateRowNumbers.has(rowNumber));

  console.log(`  Importable rows: ${importable.length}`);
  console.log("=".repeat(60));

  if (dryRun) {
    console.log("Dry run — no writes made. Re-run without --dry-run to import.");
    return { totalRows: rows.length, errors: rowErrors.length, duplicates: fileDuplicates.length, imported: 0 };
  }

  let imported = 0;
  const importErrors = [];
  for (const { row, rowNumber } of importable) {
    const existingNote = await findExistingDuplicate(shopId, row);
    if (existingNote) console.log(`  ℹ row ${rowNumber}: ${existingNote}`);
    try {
      await intakeRun(rowToIntakeContext(shopId, row));
      imported += 1;
    } catch (err) {
      importErrors.push(`row ${rowNumber}: ${err.message}`);
    }
  }

  console.log(`Imported ${imported} / ${importable.length} importable rows.`);
  if (importErrors.length > 0) {
    console.log(`${importErrors.length} rows failed during import:`);
    importErrors.forEach((e) => console.log(`    ✗ ${e}`));
  }

  return {
    totalRows: rows.length,
    errors: rowErrors.length,
    duplicates: fileDuplicates.length,
    imported,
    importErrors: importErrors.length,
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error("import-csv failed:", err.message);
    process.exitCode = 1;
  });
}
