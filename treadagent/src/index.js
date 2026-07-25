#!/usr/bin/env node
// Service entry point: fail-fast env check, HTTP API for the web/
// tools, webhook routes (wired up in Phase 2), and cron registration
// (Phase 2 — node-cron in process for v1, see repo structure notes).

import { createServer } from "node:http";
import { assertRequiredEnv, env } from "./config/env.js";
import { createApiHandler } from "./server/api.js";

assertRequiredEnv();

const apiHandler = createApiHandler();

const server = createServer((req, res) => {
  if (req.url.startsWith("/api/")) return apiHandler(req, res);
  res.writeHead(404, { "Content-Type": "text/plain" });
  res.end("Not found. TreadAgent's API is under /api/*; static web/ tools are served separately.");
});

server.listen(env.service.port, () => {
  console.log(`TreadAgent listening on :${env.service.port} (${env.service.nodeEnv})`);
});
