#!/usr/bin/env node
// Generates a printable HTML sheet of rack tags — one per active rack
// location, each carrying a QR code that opens
// web/tech-capture.html?qr=<token> on a tech's phone. This is what a
// shop prints, laminates, and zip-ties to the rack: the whole point of
// the 30-second capture budget depends on the tag being scannable
// without typing anything.
//
// Usage:
//   node scripts/print-rack-tags.js --shop=1 --out=rack-tags.html
//   node scripts/print-rack-tags.js --shop=1 --base-url=https://shop.treadagent.app --site=on_site

import { writeFile } from "node:fs/promises";
import QRCode from "qrcode";
import { listRecords, findOne } from "../src/lib/nocodb.js";

function parseArgs(argv) {
  return Object.fromEntries(
    argv.map((a) => {
      const [k, v] = a.replace(/^--/, "").split("=");
      return [k, v ?? true];
    })
  );
}

function tagLabel(loc) {
  return [loc.site_name, loc.zone, loc.rack, loc.shelf, loc.slot].filter(Boolean).join(" · ");
}

export async function buildRackTagsHtml({ shopId, baseUrl, site }) {
  const shop = await findOne("shops", { Id: shopId });
  if (!shop) throw new Error(`print-rack-tags: shop ${shopId} not found`);

  const where = { shop_id: shopId, active: true };
  if (site) where.site = site;
  const { records: locations } = await listRecords("rack_locations", { where, all: true });

  const cards = await Promise.all(
    locations.map(async (loc) => {
      const url = `${baseUrl}/tech-capture.html?qr=${encodeURIComponent(loc.qr_token)}`;
      const qrSvg = await QRCode.toString(url, { type: "svg", margin: 1, width: 160 });
      return `
        <div class="tag">
          <div class="qr">${qrSvg}</div>
          <div class="label">${escapeHtml(tagLabel(loc))}</div>
          <div class="token">${escapeHtml(loc.qr_token)}</div>
          <div class="site">${escapeHtml(loc.site || "")}</div>
        </div>`;
    })
  );

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Rack tags — ${escapeHtml(shop.name || "Shop " + shopId)}</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 12px; }
  h1 { font-size: 14px; }
  .sheet { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .tag {
    border: 1px dashed #999; border-radius: 6px; padding: 8px; text-align: center;
    break-inside: avoid; page-break-inside: avoid;
  }
  .qr svg { width: 100%; height: auto; }
  .label { font-weight: 700; font-size: 11px; margin-top: 4px; }
  .token { font-size: 10px; color: #555; font-family: monospace; }
  .site { font-size: 9px; color: #888; text-transform: uppercase; }
  @media print { .sheet { grid-template-columns: repeat(4, 1fr); } }
</style>
</head>
<body>
<h1>${escapeHtml(shop.name || "Shop " + shopId)} — rack tags (${locations.length})</h1>
<div class="sheet">
${cards.join("\n")}
</div>
</body>
</html>`;
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export async function main(opts = {}) {
  const args = parseArgs(process.argv.slice(2));
  const shopId = opts.shopId ?? Number(args.shop);
  const baseUrl = (opts.baseUrl ?? args["base-url"] ?? "http://localhost:3000/web").replace(/\/+$/, "");
  const site = opts.site ?? (args.site === true ? undefined : args.site);
  const out = opts.out ?? args.out ?? `rack-tags-shop-${shopId}.html`;

  if (!shopId) throw new Error("print-rack-tags: --shop=<id> is required");

  const html = await buildRackTagsHtml({ shopId, baseUrl, site });
  if (opts.dryRun ?? args["dry-run"]) {
    console.log(`[dry-run] would write ${out}`);
    return { out, written: false, html };
  }
  await writeFile(out, html);
  console.log(`Wrote ${out}`);
  return { out, written: true };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error("print-rack-tags failed:", err.message);
    process.exitCode = 1;
  });
}
