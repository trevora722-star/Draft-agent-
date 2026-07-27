import { getStore } from "@netlify/blobs";
import { sessionRole, unauthorized } from "./lib/auth.mjs";

// Admin-only soft delete: copy each blob into a trash store, then remove the
// live copy, so a mistaken delete can be restored.
export default async (request, context) => {
  const role = sessionRole(request);
  if (!role) return unauthorized();
  if (request.method !== "DELETE") {
    return new Response("Method not allowed", { status: 405 });
  }
  if (role !== "admin") {
    return Response.json({ error: "admin only" }, { status: 403 });
  }
  const id = context.params?.id || "";
  if (!/^[0-9a-f]{32}$/.test(id)) {
    return Response.json({ error: "not found" }, { status: 404 });
  }

  const pairs = [
    ["wedding-media", "wedding-trash-media"],
    ["wedding-thumbs", "wedding-trash-thumbs"],
    ["wedding-meta", "wedding-trash-meta"],
  ];
  let found = false;
  for (const [liveName, trashName] of pairs) {
    const live = getStore(liveName);
    const blob = await live.getWithMetadata(id, { type: "arrayBuffer" });
    if (!blob) continue;
    found = true;
    await getStore(trashName).set(id, blob.data, { metadata: blob.metadata || {} });
    await live.delete(id);
  }
  if (!found) {
    return Response.json({ error: "not found" }, { status: 404 });
  }
  return Response.json({ deleted: id });
};

export const config = { path: "/api/photos/:id" };
