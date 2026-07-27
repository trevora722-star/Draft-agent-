// Shared auth helpers for the Netlify edition of the wedding album.
//
// Same scheme as the Render/FastAPI version: one shared guest login plus an
// optional admin password (same username) that grants delete rights. The
// session is a signed "exp.role.sig" cookie.

import crypto from "node:crypto";

export const SESSION_COOKIE = "wedding_session";
export const SESSION_TTL_SECONDS = 60 * 60 * 24 * 30; // 30 days

const USERNAME = process.env.WEDDING_USERNAME || "guest";
const PASSWORD = process.env.WEDDING_PASSWORD || "wedding";
const ADMIN_PASSWORD = process.env.WEDDING_ADMIN_PASSWORD || "";

// With no disk to persist a generated key, derive one from the credentials
// when WEDDING_SECRET_KEY isn't set (changing the password logs everyone out).
const SECRET = process.env.WEDDING_SECRET_KEY
  ? Buffer.from(process.env.WEDDING_SECRET_KEY)
  : crypto
      .createHash("sha256")
      .update(`wedding-secret:${USERNAME}:${PASSWORD}:${ADMIN_PASSWORD}`)
      .digest();

export const SITE_TITLE = process.env.WEDDING_TITLE || "Our Wedding Album";

function safeEqual(a, b) {
  // Hash first so lengths always match; comparison stays constant-time.
  const ha = crypto.createHash("sha256").update(String(a)).digest();
  const hb = crypto.createHash("sha256").update(String(b)).digest();
  return crypto.timingSafeEqual(ha, hb);
}

function sign(payload) {
  return crypto.createHmac("sha256", SECRET).update(payload).digest("hex");
}

// Returns "admin" | "guest" | null for a login attempt.
export function checkLogin(username, password) {
  const userOk = safeEqual((username || "").trim(), USERNAME);
  const adminOk = ADMIN_PASSWORD !== "" && safeEqual(password || "", ADMIN_PASSWORD);
  const guestOk = safeEqual(password || "", PASSWORD);
  if (!userOk) return null;
  if (adminOk) return "admin";
  if (guestOk) return "guest";
  return null;
}

export function makeToken(role) {
  const exp = String(Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS);
  const payload = `${exp}.${role}`;
  return `${payload}.${sign(payload)}`;
}

// Returns "admin" | "guest" | null for a request's session cookie.
export function sessionRole(request) {
  const header = request.headers.get("cookie") || "";
  const match = header.match(new RegExp(`(?:^|;\\s*)${SESSION_COOKIE}=([^;]+)`));
  if (!match) return null;
  const parts = match[1].split(".");
  if (parts.length !== 3) return null;
  const [exp, role, sig] = parts;
  if (!safeEqual(sig, sign(`${exp}.${role}`))) return null;
  if (!["guest", "admin"].includes(role)) return null;
  if (!/^\d+$/.test(exp) || Number(exp) <= Date.now() / 1000) return null;
  return role;
}

export function sessionCookie(request, token, maxAge = SESSION_TTL_SECONDS) {
  const secure = new URL(request.url).protocol === "https:" ? "; Secure" : "";
  return `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${secure}`;
}

export function unauthorized() {
  return Response.json({ error: "not logged in" }, { status: 401 });
}
