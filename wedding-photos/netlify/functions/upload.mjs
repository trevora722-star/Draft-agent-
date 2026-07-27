import crypto from "node:crypto";
import { getStore } from "@netlify/blobs";
import { sessionRole, unauthorized } from "./lib/auth.mjs";

// The gallery page compresses each photo in the browser (full ≤ ~3.5 MB JPEG
// plus a small thumbnail) and uploads ONE photo per request, so each request
// stays comfortably inside Netlify's ~6 MB function body limit.
const MAX_BYTES = 5 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/gif", "image/webp"]);

export default async (request) => {
  if (!sessionRole(request)) return unauthorized();
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  let form;
  try {
    form = await request.formData();
  } catch {
    return Response.json({ error: "bad upload" }, { status: 400 });
  }
  const file = form.get("file");
  const thumb = form.get("thumb");
  const uploader = String(form.get("uploader") || "").replace(/\s+/g, " ").trim().slice(0, 60);
  const originalName = String(form.get("original_name") || file?.name || "photo").slice(0, 120);

  if (!file || typeof file.arrayBuffer !== "function" || file.size === 0) {
    return Response.json({ error: "empty file" }, { status: 400 });
  }
  if (file.size > MAX_BYTES) {
    return Response.json({ error: "larger than 5 MB" }, { status: 413 });
  }
  if (!ALLOWED_TYPES.has(file.type)) {
    return Response.json({ error: "not a supported photo type" }, { status: 415 });
  }

  const id = crypto.randomBytes(16).toString("hex");
  const mediaStore = getStore("wedding-media");
  await mediaStore.set(id, await file.arrayBuffer(), {
    metadata: { contentType: file.type, originalName },
  });
  if (thumb && typeof thumb.arrayBuffer === "function" && thumb.size > 0 && thumb.size < 512 * 1024) {
    const thumbStore = getStore("wedding-thumbs");
    await thumbStore.set(id, await thumb.arrayBuffer(), {
      metadata: { contentType: "image/jpeg" },
    });
  }

  const meta = {
    id,
    type: "photo",
    original_name: originalName,
    uploader,
    uploaded_at: Math.floor(Date.now() / 1000),
  };
  await getStore("wedding-meta").setJSON(id, meta);
  return Response.json(meta);
};

export const config = { path: "/api/upload" };
