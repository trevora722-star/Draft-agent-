import { SITE_TITLE } from "./lib/auth.mjs";

// Public: just the site title for the static pages. No secrets here.
export default async () => Response.json({ title: SITE_TITLE });

export const config = { path: "/api/config" };
