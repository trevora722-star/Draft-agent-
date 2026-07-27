import { sessionCookie } from "./lib/auth.mjs";

export default async (request) => {
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }
  return new Response(null, {
    status: 303,
    headers: {
      Location: new URL("/login.html", request.url).toString(),
      "Set-Cookie": sessionCookie(request, "", 0),
    },
  });
};

export const config = { path: "/api/logout" };
