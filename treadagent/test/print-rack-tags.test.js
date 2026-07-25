import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache } from "../src/lib/nocodb.js";
import { buildRackTagsHtml, main as printRackTags } from "../scripts/print-rack-tags.js";

let mock;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
});

describe("buildRackTagsHtml", () => {
  test("generates one tag per active rack location with an embedded QR SVG", async () => {
    const [shop] = mock.seed("shops", [{ name: "Ace Tire" }]);
    mock.seed("rack_locations", [
      { shop_id: shop.Id, site: "on_site", zone: "A", rack: "1", shelf: "1", slot: "1", qr_token: "TAG-A11", active: true },
      { shop_id: shop.Id, site: "on_site", zone: "A", rack: "1", shelf: "2", slot: "1", qr_token: "TAG-A12", active: true },
      { shop_id: shop.Id, site: "on_site", zone: "B", rack: "1", shelf: "1", slot: "1", qr_token: "TAG-INACTIVE", active: false },
    ]);

    const html = await buildRackTagsHtml({ shopId: shop.Id, baseUrl: "http://localhost:3000/web" });
    assert.match(html, /Ace Tire/);
    assert.match(html, /TAG-A11/);
    assert.match(html, /TAG-A12/);
    assert.doesNotMatch(html, /TAG-INACTIVE/, "inactive locations must not be printed");
    assert.match(html, /<svg/, "each tag should embed an SVG QR code");
  });

  test("filters by site when requested", async () => {
    const [shop] = mock.seed("shops", [{}]);
    mock.seed("rack_locations", [
      { shop_id: shop.Id, site: "on_site", qr_token: "ONS-1", active: true },
      { shop_id: shop.Id, site: "off_site", qr_token: "OFS-1", active: true },
    ]);
    const html = await buildRackTagsHtml({ shopId: shop.Id, baseUrl: "http://x", site: "off_site" });
    assert.match(html, /OFS-1/);
    assert.doesNotMatch(html, /ONS-1/);
  });

  test("throws for an unknown shop", async () => {
    await assert.rejects(() => buildRackTagsHtml({ shopId: 999, baseUrl: "http://x" }));
  });
});

describe("main()", () => {
  test("dry-run does not write a file", async () => {
    const [shop] = mock.seed("shops", [{}]);
    mock.seed("rack_locations", [{ shop_id: shop.Id, qr_token: "T1", active: true }]);
    const result = await printRackTags({ shopId: shop.Id, dryRun: true, out: "/tmp/should-not-exist-treadagent.html" });
    assert.equal(result.written, false);
  });
});
