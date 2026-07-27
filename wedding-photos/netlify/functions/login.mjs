import { checkLogin, makeToken, sessionCookie } from "./lib/auth.mjs";

// Best-effort rate limiting; each function instance keeps its own counter.
const attempts = new Map();
const LIMIT = 20;
const WINDOW_MS = 15 * 60 * 1000;

export default async (request, context) => {
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }
  const ip = context.ip || "unknown";
  const now = Date.now();
  const recent = (attempts.get(ip) || []).filter((t) => now - t < WINDOW_MS);
  attempts.set(ip, recent);
  if (recent.length >= LIMIT) {
    return Response.redirect(new URL("/login.html?error=rate", request.url), 303);
  }

  const form = await request.formData();
  const role = checkLogin(form.get("username"), form.get("password"));
  if (!role) {
    recent.push(now);
    return Response.redirect(new URL("/login.html?error=1", request.url), 303);
  }
  return new Response(null, {
    status: 303,
    headers: {
      Location: new URL("/", request.url).toString(),
      "Set-Cookie": sessionCookie(request, makeToken(role)),
    },
  });
};

export const config = { path: "/api/login" };
