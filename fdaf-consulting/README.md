# FDAF Consulting — website

Static single-page site for FDAF Consulting, an in-home personal training
business serving couples and small groups in West Kelowna and the Central
Okanagan.

## What's here

- `index.html` — the entire site (HTML, CSS, and JS embedded). No build step.

## Preview locally

```bash
# from this directory
python3 -m http.server 8000
# then open http://localhost:8000
```

Or just double-click `index.html` to open it in a browser.

## Deploy

Because it's one static file, it works on anything:

- **Netlify / Cloudflare Pages / GitHub Pages** — drop the `fdaf-consulting/`
  folder in and point the site at `index.html`.
- **Any web host** — upload `index.html` and you're done.

## Things to swap before going live

Search the file for these and replace with the real values:

| Placeholder                       | Where               | Replace with                    |
|-----------------------------------|---------------------|---------------------------------|
| `hello@fdafconsulting.ca`         | contact + form      | Real email address              |
| `(250) 000-0000`                  | contact + footnote  | Real phone number               |
| "Photo coming soon" portrait      | About section       | A real headshot (`<img>`)       |
| From $95 / From $40 / From $120   | Services pricing    | Real prices, or remove          |
| Testimonial block                 | Quote section       | A real client quote (with consent) |
| Certifications line               | About section       | Specific certification names    |

## Notes on the design

- Mobile-first, no JS framework, no external CSS — total weight is ~15 KB.
- Fonts are loaded from Google Fonts (Fraunces + Inter).
- The contact form uses `mailto:` so it works without a backend. For a
  production form, swap the `action` to a form service (Formspree, Basin,
  Netlify Forms) and remove the `mailto:` action.
