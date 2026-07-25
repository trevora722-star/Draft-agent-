import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { Readable } from "node:stream";
import { MockNocoDB, setMockEnv } from "./helpers/mock-nocodb.js";
import { __setFetch, clearTableCache } from "../src/lib/nocodb.js";
import { createApiHandler } from "../src/server/api.js";
import { enqueue } from "../src/lib/review.js";

let mock;
let handle;

beforeEach(() => {
  setMockEnv();
  mock = new MockNocoDB();
  clearTableCache();
  __setFetch(mock.fetch);
  handle = createApiHandler();
});

function fakeReq(method, url, body) {
  const req = body ? Readable.from([Buffer.from(JSON.stringify(body))]) : Readable.from([]);
  req.method = method;
  req.url = url;
  return req;
}

function fakeRes() {
  const res = {
    statusCode: null,
    headers: null,
    body: null,
    writeHead(status, headers) {
      this.statusCode = status;
      this.headers = headers;
    },
    end(payload) {
      this.body = payload ? JSON.parse(payload) : null;
    },
  };
  return res;
}

describe("GET /api/health", () => {
  test("responds ok", async () => {
    const res = fakeRes();
    await handle(fakeReq("GET", "/api/health"), res);
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.body, { ok: true });
  });
});

describe("GET /api/tech-capture/:qrToken", () => {
  test("404s for an unknown tag", async () => {
    const res = fakeRes();
    await handle(fakeReq("GET", "/api/tech-capture/nope"), res);
    assert.equal(res.statusCode, 404);
  });

  test("returns an empty-slot message when unoccupied", async () => {
    mock.seed("rack_locations", [{ qr_token: "TAG1", occupied_by_set_id: null }]);
    const res = fakeRes();
    await handle(fakeReq("GET", "/api/tech-capture/TAG1"), res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.tireSet, null);
  });

  test("returns tire set, vehicle, customer first name, and tires for an occupied slot", async () => {
    const [customer] = mock.seed("customers", [{ first_name: "Pat", last_name: "Nguyen", phone_e164: "+16045551234" }]);
    const [vehicle] = mock.seed("vehicles", [{ customer_id: customer.Id, make: "Honda", model: "Civic" }]);
    const [tireSet] = mock.seed("tire_sets", [{ vehicle_id: vehicle.Id, brand: "Michelin" }]);
    mock.seed("tires", [
      { tire_set_id: tireSet.Id, position: "LF" },
      { tire_set_id: tireSet.Id, position: "RF" },
    ]);
    mock.seed("rack_locations", [{ qr_token: "TAG2", occupied_by_set_id: tireSet.Id }]);

    const res = fakeRes();
    await handle(fakeReq("GET", "/api/tech-capture/TAG2"), res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.customer.first_name, "Pat");
    assert.equal(res.body.customer.last_name, undefined, "must not leak last name/phone to the capture screen");
    assert.equal(res.body.tires.length, 2);
  });
});

describe("POST /api/tech-capture/submit", () => {
  test("records a reading through agent 2 and returns its classification", async () => {
    const [shop] = mock.seed("shops", [{ province: "BC" }]);
    mock.seed("regulations", [{ province: "BC", legal_min_32nds: 2, winter_designation_min_mm: 3.5 }]);
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id }]);
    const [tire] = mock.seed("tires", [{ tire_set_id: tireSet.Id, position: "LF" }]);

    const res = fakeRes();
    await handle(
      fakeReq("POST", "/api/tech-capture/submit", {
        shopId: shop.Id,
        tireId: tire.Id,
        reading: { outer_32nds: 8, centre_32nds: 8, inner_32nds: 8 },
      }),
      res
    );
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.ok, true);
    assert.equal(mock.table("tread_readings").rows.length, 1);
  });

  test("400s when required fields are missing", async () => {
    const res = fakeRes();
    await handle(fakeReq("POST", "/api/tech-capture/submit", { shopId: 1 }), res);
    assert.equal(res.statusCode, 400);
  });
});

describe("GET /api/rack-map", () => {
  test("returns each location with its occupant summary and dormancy stage", async () => {
    const [shop] = mock.seed("shops", [{}]);
    const [tireSet] = mock.seed("tire_sets", [{ shop_id: shop.Id, brand: "Bridgestone" }]);
    mock.seed("dormancy_cases", [{ shop_id: shop.Id, tire_set_id: tireSet.Id, stage: 2 }]);
    mock.seed("rack_locations", [{ shop_id: shop.Id, occupied_by_set_id: tireSet.Id }]);
    mock.seed("rack_locations", [{ shop_id: shop.Id, occupied_by_set_id: null }]);

    const res = fakeRes();
    await handle(fakeReq("GET", `/api/rack-map?shopId=${shop.Id}`), res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.length, 2);
    const occupied = res.body.find((r) => r.occupied_by_set_id === tireSet.Id);
    assert.equal(occupied.dormancyStage, 2);
    assert.equal(occupied.tireSet.brand, "Bridgestone");
  });
});

describe("review-queue endpoints", () => {
  test("GET lists pending reviews for a shop; POST approve/reject transition them", async () => {
    const [shop] = mock.seed("shops", [{}]);
    const review = await enqueue({
      shopId: shop.Id,
      entityType: "outreach_messages",
      entityId: 1,
      proposedAction: "send_sms",
      agentKey: "outreach",
      riskLevel: "low",
    });

    const listRes = fakeRes();
    await handle(fakeReq("GET", `/api/review-queue?shopId=${shop.Id}`), listRes);
    assert.equal(listRes.body.length, 1);

    const approveRes = fakeRes();
    await handle(fakeReq("POST", `/api/review-queue/${review.Id}/approve`, { decidedBy: "staff1" }), approveRes);
    assert.equal(approveRes.statusCode, 200);
    assert.equal(approveRes.body.status, "approved");
  });

  test("POST reject requires notes and returns 400 without them", async () => {
    const [shop] = mock.seed("shops", [{}]);
    const review = await enqueue({
      shopId: shop.Id,
      entityType: "outreach_messages",
      entityId: 2,
      proposedAction: "send_sms",
      agentKey: "outreach",
      riskLevel: "low",
    });
    const res = fakeRes();
    await handle(fakeReq("POST", `/api/review-queue/${review.Id}/reject`, { decidedBy: "staff1" }), res);
    assert.equal(res.statusCode, 400);
  });
});
