# Photos

Three image files power the site. Drop them here with these exact names and
they'll appear automatically on the next deploy. Anything missing falls back
to a styled placeholder (text mark for the logo, gradient panel for the
hero / equipment shot), so the layout never breaks.

| Filename | Where it appears | Notes |
|---|---|---|
| `Logo.jpg` | Header and footer brand mark | Hex badge |
| `adam.webp` | Full-bleed homepage hero | Mountain-top thumbs-up shot; the dark overlay keeps the headline readable |
| `ATV.webp` | Equipment / "Four Kawasakis" section | BruteForce 450 close-up |

Filenames are case-sensitive on Netlify (Linux), so if you replace one,
keep the same casing or update the references in `public/index.html`,
`booking.html`, `payment.html`. Aim for under ~300KB per image for fast loads.
