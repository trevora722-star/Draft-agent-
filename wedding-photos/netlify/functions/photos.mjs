import { getStore } from "@netlify/blobs";
import { sessionRole, unauthorized } from "./lib/auth.mjs";

export default async (request) => {
  const role = sessionRole(request);
  if (!role) return unauthorized();
  if (request.method !== "GET") {
    return new Response("Method not allowed", { status: 405 });
  }

  const metaStore = getStore("wedding-meta");
  const { blobs } = await metaStore.list();
  const photos = [];
  // Fetch metadata in modest parallel batches to keep within function limits.
  const BATCH = 50;
  for (let i = 0; i < blobs.length; i += BATCH) {
    const chunk = blobs.slice(i, i + BATCH);
    const metas = await Promise.all(
      chunk.map((b) => metaStore.get(b.key, { type: "json" }).catch(() => null)),
    );
    photos.push(...metas.filter(Boolean));
  }
  photos.sort((a, b) => (b.uploaded_at || 0) - (a.uploaded_at || 0));
  return Response.json({ photos, is_admin: role === "admin" });
};

export const config = { path: "/api/photos" };
