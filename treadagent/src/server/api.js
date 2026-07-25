// Minimal JSON API backing the self-contained HTML tools in web/. Plain
// node:http routing — no Express, no framework — since this is backend
// code the "no bundler / no frontend deps" constraint doesn't apply to,
// but there's no need for a dependency here either.

import { findOne, listRecords } from "../lib/nocodb.js";
import { run as treadRun } from "../agents/02-tread.js";
import { approve, reject, listPending } from "../lib/review.js";

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Content-Length": Buffer.byteLength(payload) });
  res.end(payload);
}

async function readJsonBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

async function getTechCapture(res, qrToken) {
  const rackLocation = await findOne("rack_locations", { qr_token: qrToken });
  if (!rackLocation) return sendJson(res, 404, { error: "Unknown rack tag" });

  if (!rackLocation.occupied_by_set_id) {
    return sendJson(res, 200, { rackLocation, tireSet: null, message: "This slot is empty." });
  }

  const tireSet = await findOne("tire_sets", { Id: rackLocation.occupied_by_set_id });
  const { records: tires } = await listRecords("tires", { where: { tire_set_id: tireSet.Id }, all: true });
  const vehicle = await findOne("vehicles", { Id: tireSet.vehicle_id });
  const customer = vehicle ? await findOne("customers", { Id: vehicle.customer_id }) : null;

  sendJson(res, 200, {
    rackLocation,
    tireSet,
    vehicle,
    customer: customer ? { first_name: customer.first_name } : null, // 30-second screen needs a first name only, not a full PII payload
    tires: tires.map((t) => ({ id: t.Id, position: t.position })),
  });
}

async function postTechCaptureSubmit(req, res) {
  const body = await readJsonBody(req);
  const { shopId, tireId, reading } = body;
  if (!shopId || !tireId || !reading) return sendJson(res, 400, { error: "shopId, tireId, reading are required" });

  const shop = await findOne("shops", { Id: shopId });
  if (!shop) return sendJson(res, 404, { error: "unknown shop" });
  const regulation = await findOne("regulations", { province: shop.province });
  if (!regulation) return sendJson(res, 422, { error: `no regulations row for province ${shop.province}` });

  try {
    const result = await treadRun({
      shopId,
      tireId,
      reading,
      regulation,
      shopConfig: { season: reading.season },
    });
    sendJson(res, 200, { ok: true, status: result.classification.status, flagged: result.reading.flagged });
  } catch (err) {
    sendJson(res, 500, { error: err.message });
  }
}

async function getRackMap(res, shopId) {
  const { records: locations } = await listRecords("rack_locations", { where: { shop_id: shopId }, all: true });
  const setIds = locations.map((l) => l.occupied_by_set_id).filter(Boolean);
  const sets = {};
  for (const id of setIds) {
    const set = await findOne("tire_sets", { Id: id });
    if (set) sets[id] = set;
  }
  const dormancyCases = {};
  const { records: cases } = await listRecords("dormancy_cases", { where: { shop_id: shopId }, all: true });
  for (const c of cases) dormancyCases[c.tire_set_id] = c;

  sendJson(
    res,
    200,
    locations.map((loc) => ({
      ...loc,
      tireSet: loc.occupied_by_set_id ? sets[loc.occupied_by_set_id] ?? null : null,
      dormancyStage: loc.occupied_by_set_id ? dormancyCases[loc.occupied_by_set_id]?.stage ?? 0 : null,
    }))
  );
}

async function getReviewQueue(res, shopId) {
  const rows = await listPending(Number(shopId));
  sendJson(res, 200, rows);
}

async function postReviewDecision(req, res, id, action) {
  const body = await readJsonBody(req);
  try {
    const row =
      action === "approve"
        ? await approve({ reviewId: Number(id), decidedBy: body.decidedBy })
        : await reject({ reviewId: Number(id), decidedBy: body.decidedBy, notes: body.notes });
    sendJson(res, 200, row);
  } catch (err) {
    sendJson(res, 400, { error: err.message });
  }
}

export function createApiHandler() {
  return async function handle(req, res) {
    try {
      const url = new URL(req.url, "http://localhost");
      const segments = url.pathname.split("/").filter(Boolean);

      if (segments[0] !== "api") return sendJson(res, 404, { error: "not found" });

      if (segments[1] === "health" && req.method === "GET") {
        return sendJson(res, 200, { ok: true });
      }

      if (segments[1] === "tech-capture" && segments[2] && req.method === "GET") {
        return await getTechCapture(res, segments[2]);
      }
      if (segments[1] === "tech-capture" && segments[2] === "submit" && req.method === "POST") {
        return await postTechCaptureSubmit(req, res);
      }
      if (segments[1] === "rack-map" && req.method === "GET") {
        return await getRackMap(res, url.searchParams.get("shopId"));
      }
      if (segments[1] === "review-queue" && segments.length === 2 && req.method === "GET") {
        return await getReviewQueue(res, url.searchParams.get("shopId"));
      }
      if (segments[1] === "review-queue" && segments[3] === "approve" && req.method === "POST") {
        return await postReviewDecision(req, res, segments[2], "approve");
      }
      if (segments[1] === "review-queue" && segments[3] === "reject" && req.method === "POST") {
        return await postReviewDecision(req, res, segments[2], "reject");
      }

      sendJson(res, 404, { error: "not found" });
    } catch (err) {
      sendJson(res, 500, { error: err.message });
    }
  };
}
