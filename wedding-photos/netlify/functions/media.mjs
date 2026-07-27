import { getStore } from "@netlify/blobs";
import { sessionRole, unauthorized } from "./lib/auth.mjs";

// Serves both full-size photos (/photos/:id) and thumbnails (/thumbs/:id).
export default async (request, context) => {
  if (!sessionRole(request)) return unauthorized();
  const id = context.params?.id || "";
  if (!/^[0-9a-f]{32}$/.test(id)) {
    return Response.json({ error: "not found" }, { status: 404 });
  }
  const url = new URL(request.url);
  const wantThumb = url.pathname.startsWith("/thumbs/");

  let result = null;
  if (wantThumb) {
    result = await getStore("wedding-thumbs").getWithMetadata(id, { type: "arrayBuffer" });
  }
  if (!result) {
    result = await getStore("wedding-media").getWithMetadata(id, { type: "arrayBuffer" });
  }
  if (!result) {
    return Response.json({ error: "not found" }, { status: 404 });
  }

  const headers = {
    "Content-Type": result.metadata?.contentType || "image/jpeg",
    "Cache-Control": "private, max-age=3600",
  };
  if (url.searchParams.get("download")) {
    const name = String(result.metadata?.originalName || `${id}.jpg`)
      .replace(/[^\w.\- ]/g, "_");
    headers["Content-Disposition"] = `attachment; filename="${name}"`;
  }
  return new Response(result.data, { headers });
};

export const config = { path: ["/photos/:id", "/thumbs/:id"] };
