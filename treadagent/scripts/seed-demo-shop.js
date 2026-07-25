#!/usr/bin/env node
// Seeds a realistic demo shop: this is the sales asset a rep drives
// through DEMO-SCRIPT.md, so the numbers have to look like a real
// 18-month-old shop, not obviously synthetic data. ~600 tire sets,
// ~8% dormant across every escalation stage, 2-4 seasons of tread
// history with plausible wear curves, and a staggered (not stampeded)
// changeover-window appointment book.
//
// Bulk-inserts via createRecords() rather than routing every reading
// through agent 2 / the ledger — at this volume (600 sets × 4 tires ×
// up to 4 readings ≈ 7,000+ rows) that would mean thousands of
// sequential network round trips against a real NocoDB instance. Real
// shop activity always goes through the agents; this is seed data for
// a sales demo, not a simulation of production traffic — see
// scripts/import-csv.js and Task 2.13's end-to-end test for that.
//
// Usage: node scripts/seed-demo-shop.js [--sets=600] [--dry-run]

import { findOne, createRecord, createRecords, listRecords, updateRecord } from "../src/lib/nocodb.js";
import { append } from "../src/lib/ledger.js";

function parseCliArgs(argv) {
  return Object.fromEntries(
    argv.map((a) => {
      const [k, v] = a.replace(/^--/, "").split("=");
      return [k, v ?? true];
    })
  );
}
const CHUNK = 100;

const FIRST_NAMES = [
  "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Elizabeth",
  "Sarah", "Daniel", "Karen", "Matthew", "Nancy", "Anthony", "Lisa", "Mark", "Betty", "Steven",
  "Sandra", "Paul", "Ashley", "Andrew", "Kimberly", "Joshua", "Emily", "Kevin", "Donna", "Brian",
  "Wei", "Priya", "Mohammed", "Fatima", "Chen", "Aiko", "Raj", "Ling", "Amir", "Sofia",
  "Jean", "Marie", "Pierre", "Isabelle", "Olivier", "Chantal", "Luc", "Nicole", "Denis", "Sylvie",
];
const LAST_NAMES = [
  "Smith", "Nguyen", "Brown", "Wilson", "Martin", "Taylor", "Johnson", "Lee", "Anderson", "Thompson",
  "White", "Harris", "Clark", "Lewis", "Walker", "Young", "King", "Wright", "Chen", "Patel",
  "Kim", "Singh", "Kaur", "Wong", "Tremblay", "Gagnon", "Roy", "Cote", "Bouchard", "Gauthier",
  "Dubois", "Leblanc", "Morin", "Girard", "Fortin", "Pelletier",
];
const VEHICLES = [
  { make: "Honda", model: "Civic", drive: "FWD" },
  { make: "Toyota", model: "Corolla", drive: "FWD" },
  { make: "Toyota", model: "RAV4", drive: "AWD" },
  { make: "Subaru", model: "Outback", drive: "AWD" },
  { make: "Subaru", model: "Forester", drive: "AWD" },
  { make: "Ford", model: "F-150", drive: "4WD" },
  { make: "Honda", model: "CR-V", drive: "AWD" },
  { make: "Mazda", model: "3", drive: "FWD" },
  { make: "Hyundai", model: "Tucson", drive: "AWD" },
  { make: "Volkswagen", model: "Golf", drive: "FWD" },
  { make: "Jeep", model: "Grand Cherokee", drive: "4WD" },
  { make: "Chevrolet", model: "Silverado", drive: "4WD" },
  { make: "Kia", model: "Sportage", drive: "AWD" },
  { make: "Nissan", model: "Rogue", drive: "AWD" },
  { make: "BMW", model: "X3", drive: "AWD" },
];
const TIRE_BRANDS = ["Michelin", "Bridgestone", "Continental", "Goodyear", "Pirelli", "Nokian", "Toyo", "Yokohama"];
const SIZES = ["205/55R16", "215/60R16", "225/65R17", "235/55R18", "195/65R15", "245/50R18"];

function rand(n) { return Math.floor(Math.random() * n); }
function pick(arr) { return arr[rand(arr.length)]; }
function phone(i) { return "+1250555" + String(1000 + i).padStart(4, "0"); }
function isoDaysAgo(days) { return new Date(Date.now() - days * 86_400_000).toISOString(); }
function dateDaysAgo(days) { return isoDaysAgo(days).slice(0, 10); }

async function ensureShop(dryRun) {
  const existing = await findOne("shops", { name: "Okanagan Tire & Auto" });
  if (existing) {
    console.log(`Demo shop already exists (Id ${existing.Id}). Re-run against a fresh base to reseed, or use it as-is.`);
    return existing;
  }
  if (dryRun) {
    console.log("[dry-run] would create shop 'Okanagan Tire & Auto'");
    return { Id: "DRY-RUN" };
  }
  return createRecord("shops", {
    name: "Okanagan Tire & Auto",
    legal_name: "Okanagan Tire & Auto Ltd.",
    address: "1420 Harvey Ave, Kelowna, BC V1Y 6G4",
    city: "Kelowna",
    province: "BC",
    timezone: "America/Vancouver",
    storage_model: "both",
    bay_count: 6,
    capacity_sets: 700,
    sms_from: "+12505551234",
    email_from: "service@okanagantire.example",
    tier: "shop",
    quiet_hours_start: "21:00",
    quiet_hours_end: "08:00",
    tone_profile: "friendly, direct, no upsell pressure",
    active: true,
  });
}

async function ensureRackLocations(shopId, dryRun) {
  const { records: existing } = await listRecords("rack_locations", { where: { shop_id: shopId }, all: true });
  if (existing.length > 0) {
    console.log(`${existing.length} rack locations already exist — skipping rack generation.`);
    return existing;
  }

  const locations = [];
  // On-site: 4 zones x 5 aisles x 4 shelves = 80 slots
  for (const zone of ["A", "B", "C", "D"]) {
    for (let aisle = 1; aisle <= 5; aisle++) {
      for (let shelf = 1; shelf <= 4; shelf++) {
        locations.push({
          shop_id: shopId,
          site: "on_site",
          site_name: "Main Shop",
          zone,
          aisle: String(aisle),
          rack: String(aisle),
          shelf: String(shelf),
          slot: "1",
          capacity_sets: 1,
          occupied_by_set_id: null,
          qr_token: `ONS-${zone}${aisle}${shelf}`,
          active: true,
        });
      }
    }
  }
  // Off-site overflow warehouse: 500 slots (E1-E500)
  for (let i = 1; i <= 500; i++) {
    locations.push({
      shop_id: shopId,
      site: "off_site",
      site_name: "Overflow Warehouse",
      zone: "E",
      aisle: String(Math.ceil(i / 20)),
      rack: String(Math.ceil(i / 5)),
      shelf: String(((i - 1) % 5) + 1),
      slot: "1",
      capacity_sets: 1,
      occupied_by_set_id: null,
      qr_token: `OFS-E${i}`,
      active: true,
    });
  }

  if (dryRun) {
    console.log(`[dry-run] would create ${locations.length} rack_locations`);
    return locations;
  }
  console.log(`Creating ${locations.length} rack locations...`);
  return insertChunked("rack_locations", locations);
}

async function insertChunked(table, rows) {
  const created = [];
  for (let i = 0; i < rows.length; i += CHUNK) {
    const chunk = rows.slice(i, i + CHUNK);
    const result = await createRecords(table, chunk);
    created.push(...(Array.isArray(result) ? result : [result]));
    process.stdout.write(`  ${table}: ${Math.min(i + CHUNK, rows.length)}/${rows.length}\r`);
  }
  console.log();
  return created;
}

/** A plausible wear curve: starts near-new, loses 1.5-3.5/32 per 6-month season depending on drive type, floors at 1. */
function wearCurve(startDepth, seasons, aggressiveness) {
  const depths = [startDepth];
  for (let i = 1; i < seasons; i++) {
    const loss = 1.5 + Math.random() * 2 * aggressiveness;
    depths.push(Math.max(1, Math.round(depths[i - 1] - loss)));
  }
  return depths;
}

async function main(opts = {}) {
  const cliArgs = parseCliArgs(process.argv.slice(2));
  const TARGET_SETS = Number(opts.sets ?? cliArgs.sets ?? 600);
  const DRY_RUN = Boolean(opts.dryRun ?? cliArgs["dry-run"]);

  console.log(`Seeding demo shop (${TARGET_SETS} sets)${DRY_RUN ? " [dry-run]" : ""}...`);
  console.log("=".repeat(70));

  const shop = await ensureShop(DRY_RUN);
  const regulation = await findOne("regulations", { province: "BC" });
  if (!regulation && !DRY_RUN) {
    throw new Error("No BC regulation row found. Run `npm run provision` first.");
  }

  await ensureRackLocations(shop.Id, DRY_RUN);

  const customerCount = Math.round(TARGET_SETS / 2); // one vehicle each, summer + winter set

  console.log(`Generating ${customerCount} customers, vehicles, and ${TARGET_SETS} tire sets...`);
  const customerRows = [];
  for (let i = 0; i < customerCount; i++) {
    const consentRoll = Math.random();
    customerRows.push({
      shop_id: shop.Id,
      first_name: pick(FIRST_NAMES),
      last_name: pick(LAST_NAMES),
      phone_e164: phone(i),
      email: `demo.customer${i}@example.com`,
      preferred_channel: Math.random() < 0.7 ? "sms" : "email",
      preferred_language: Math.random() < 0.12 ? "fr" : "en",
      consent_type: consentRoll < 0.85 ? "express" : consentRoll < 0.95 ? "implied" : "none",
      consent_source: "intake_form",
      consent_timestamp: isoDaysAgo(400 + rand(400)),
      unsubscribed_at: Math.random() < 0.03 ? isoDaysAgo(rand(200)) : null,
      notes: "",
    });
  }
  if (DRY_RUN) {
    console.log(`[dry-run] would create ${customerRows.length} customers, ~${customerRows.length} vehicles, ${TARGET_SETS} tire sets, tires, and tread history.`);
    console.log("\nDry run complete — no writes made.");
    return;
  }
  const customers = await insertChunked("customers", customerRows);

  const vehicleRows = customers.map((c, i) => {
    const v = pick(VEHICLES);
    return {
      customer_id: c.Id,
      year: 2015 + rand(11),
      make: v.make,
      model: v.model,
      plate: `BC${String(100000 + i)}`,
      vin: "",
      summer_size: pick(SIZES),
      winter_size: pick(SIZES),
      drive_type: v.drive,
      tpms: Math.random() < 0.6,
    };
  });
  const vehicles = await insertChunked("vehicles", vehicleRows);

  const rackLocations = (await listRecords("rack_locations", { where: { shop_id: shop.Id }, all: true })).records;
  let rackCursor = 0;
  const dormantTargetCount = Math.round(TARGET_SETS * 0.08);
  let dormantAssigned = 0;

  const tireSetRows = [];
  const tireSetMeta = []; // parallel array: { drive, brand }
  vehicles.forEach((vehicle, i) => {
    for (const season of ["summer", "winter"]) {
      const brand = pick(TIRE_BRANDS);
      const willBeDormant = dormantAssigned < dormantTargetCount && Math.random() < 0.08;
      if (willBeDormant) dormantAssigned++;
      const seasonsOfHistory = 2 + rand(3); // 2-4
      const lastTouchedDaysAgo = willBeDormant ? 400 + rand(400) : rand(200);

      tireSetRows.push({
        vehicle_id: vehicle.Id,
        shop_id: shop.Id,
        season,
        on_wheels: Math.random() < 0.4,
        wheel_type: Math.random() < 0.4 ? "steel" : "",
        quantity: 4,
        brand,
        model: brand + " " + (season === "winter" ? "WinterGrip" : "Touring"),
        size: season === "summer" ? vehicleRows[i].summer_size : vehicleRows[i].winter_size,
        dot_week: 1 + rand(52),
        dot_year: 2021 + rand(4),
        status: willBeDormant ? "dormant" : "in_storage",
        rack_location_id: null, // filled in after rack assignment below
        intake_date: dateDaysAgo(lastTouchedDaysAgo + 30),
        last_touched_date: dateDaysAgo(lastTouchedDaysAgo),
        storage_agreement_ref: `AGMT-${vehicle.Id}-${season}`,
        storage_fee_cad: 89.99,
      });
      tireSetMeta.push({ drive: VEHICLES.find((v) => v.make === vehicleRows[i].make)?.drive || "FWD", brand, dormant: willBeDormant, seasonsOfHistory, season });
    }
  });

  // Assign racks up-front (in memory) so tire_sets can be created with rack_location_id set in one pass.
  tireSetRows.forEach((row) => {
    if (rackCursor < rackLocations.length) {
      row.rack_location_id = rackLocations[rackCursor].Id;
      rackCursor++;
    }
  });

  const tireSets = await insertChunked("tire_sets", tireSetRows);

  // Mark racks occupied (bulk, chunked update via createRecords isn't for updates — do individual updates but chunk by awaiting in batches for reasonable concurrency)
  console.log("Marking rack locations occupied...");
  for (let i = 0; i < tireSets.length; i++) {
    if (tireSetRows[i].rack_location_id) {
      await updateRecord("rack_locations", tireSetRows[i].rack_location_id, { occupied_by_set_id: tireSets[i].Id });
    }
    if (i % 100 === 0) process.stdout.write(`  racks: ${i}/${tireSets.length}\r`);
  }
  console.log();

  console.log("Generating tires and tread history (this is the bulk of the data)...");
  const tireRows = [];
  const tireMeta = [];
  const positions = ["LF", "RF", "LR", "RR"];
  tireSets.forEach((set, i) => {
    for (const position of positions) {
      tireRows.push({ tire_set_id: set.Id, position, dot_serial: "", notes: "", retired_at: null });
      tireMeta.push(tireSetMeta[i]);
    }
  });
  const tires = await insertChunked("tires", tireRows);

  const readingRows = [];
  tires.forEach((tire, i) => {
    const meta = tireMeta[i];
    const aggressiveness = meta.drive === "4WD" || meta.drive === "AWD" ? 1.2 : 1.0;
    const depths = wearCurve(10 + rand(2), meta.seasonsOfHistory, aggressiveness);
    let odometer = 20000 + rand(30000);
    depths.forEach((depth, seasonIdx) => {
      const monthsAgo = (meta.seasonsOfHistory - seasonIdx) * 6;
      const jitter = () => Math.max(0, depth + rand(2) - 1);
      const outer = jitter();
      const inner = jitter();
      const min32 = Math.min(outer, depth, inner);
      odometer += 6000 + rand(4000);
      readingRows.push({
        tire_id: tire.Id,
        reading_date: dateDaysAgo(monthsAgo * 30),
        outer_32nds: outer,
        centre_32nds: depth,
        inner_32nds: inner,
        min_32nds: min32,
        measured_by: "demo-seed",
        gauge_id: "GAUGE-1",
        odometer_km: odometer,
        photo_ref: "",
        flagged: min32 <= 4,
        flag_reason: min32 <= 4 ? "Below practical replacement threshold." : "",
      });
    });
  });
  await insertChunked("tread_readings", readingRows);

  console.log("Generating dormancy cases for the dormant ~8%...");
  const dormantSets = tireSets.filter((_, i) => tireSetMeta[i].dormant);
  const stageWeights = [
    [1, 0.4], [2, 0.25], [3, 0.2], [4, 0.1], [5, 0.05],
  ];
  function weightedStage() {
    const r = Math.random();
    let cum = 0;
    for (const [stage, weight] of stageWeights) {
      cum += weight;
      if (r <= cum) return stage;
    }
    return 1;
  }
  const dormancyRows = dormantSets.map((set) => {
    const stage = weightedStage();
    return {
      tire_set_id: set.Id,
      shop_id: shop.Id,
      first_flagged_at: isoDaysAgo(200 + rand(300)),
      stage,
      stage_entered_at: isoDaysAgo(rand(180)),
      notices_json: stage >= 2 ? JSON.stringify([{ stage: 1, sent_at: isoDaysAgo(200) }]) : "[]",
      statutory_deadline: stage >= 3 ? dateDaysAgo(-30) : null,
      resolution: "pending",
      resolution_at: null,
      resolved_by: "",
      evidence_bundle_ref: "",
      evidence_bundle_hash: "",
    };
  });
  await insertChunked("dormancy_cases", dormancyRows);

  console.log("Building a staggered changeover-window appointment book...");
  const campaign = await createRecord("season_campaigns", {
    shop_id: shop.Id,
    direction: "to_winter",
    year: new Date().getFullYear(),
    window_open: dateDaysAgo(20),
    window_close: dateDaysAgo(-25),
    cohort_rule_json: JSON.stringify({ rule: "all non-dormant sets" }),
    target_count: tireSets.length - dormantSets.length,
    status: "active",
    created_by: "demo-seed",
  });

  const bookableCustomers = customers.slice(0, Math.round(customers.length * 0.6));
  const appointmentRows = bookableCustomers.map((c, i) => {
    // Staggered across a 6-week window rather than all in week 1 — this is the capacity-smoothing story.
    const weekOffset = rand(6);
    const dayOffset = -20 + weekOffset * 7 + rand(5);
    const past = dayOffset < 0;
    return {
      shop_id: shop.Id,
      customer_id: c.Id,
      tire_set_id: null,
      slot_start: isoDaysAgo(-dayOffset),
      slot_end: isoDaysAgo(-dayOffset - 0.02),
      service_type: Math.random() < 0.15 ? "swap_and_replace" : "swap",
      status: past ? (Math.random() < 0.05 ? "no_show" : "completed") : "booked",
      source: "campaign",
      reminder_sent_at: past ? isoDaysAgo(-dayOffset + 1) : null,
      no_show: past && Math.random() < 0.05,
    };
  });
  await insertChunked("appointments", appointmentRows);

  await append({
    shopId: shop.Id,
    actorType: "system",
    actorId: "seed-demo-shop",
    action: "demo_shop_seeded",
    entityType: "shops",
    entityId: shop.Id,
    payload: {
      tireSets: tireSets.length,
      customers: customers.length,
      dormantCases: dormancyRows.length,
      appointments: appointmentRows.length,
    },
  });

  console.log("\nDone.");
  console.log(`  Shop: ${shop.Id} (Okanagan Tire & Auto)`);
  console.log(`  Customers: ${customers.length}`);
  console.log(`  Tire sets: ${tireSets.length}`);
  console.log(`  Dormant cases: ${dormancyRows.length} (~${((dormancyRows.length / tireSets.length) * 100).toFixed(1)}%)`);
  console.log(`  Appointments: ${appointmentRows.length}`);
  console.log(`  Season campaign: ${campaign.Id}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error("seed-demo-shop failed:", err.message);
    process.exitCode = 1;
  });
}

export { main };
