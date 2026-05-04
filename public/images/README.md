# Photos

Drop real photos here using the filenames below and they'll auto-appear on
the site (no rebuild required — just redeploy on Netlify or push to the branch
and Netlify rebuilds for you).

If a file is missing or fails to load, the site keeps its CSS gradient
placeholder, so the layout never breaks.

## Expected files

| Filename | Where it appears | Suggested dimensions |
|---|---|---|
| `hero.jpg` | Full-bleed homepage hero (top of page) | 2400 × 1200, JPG, ~70% quality |
| `adam.jpg` | Portrait next to "Hey, I'm Adam" | 800 × 1000, portrait orientation |
| `atv-1.jpg` … `atv-4.jpg` | Equipment section, four-up grid of the fleet | 1200 × 900 each, landscape |
| `atv-guided-half.jpg` | Services card: Guided Half-Day | 800 × 600 |
| `atv-guided-full.jpg` | Services card: Guided Full-Day | 800 × 600 |
| `atv-self-half.jpg` | Services card: Self-Guided Half-Day | 800 × 600 |
| `atv-self-full.jpg` | Services card: Self-Guided Full-Day | 800 × 600 |

JPG or WebP. Keep each under ~300KB if you can — the site loads quickly that
way and looks crisp on phones.

## Adding a new image slot

The image path lives in two places:

1. **For a card image** — set `image: "images/your-file.jpg"` on the SKU in
   `public/catalog.js`.
2. **For a section image** — add `data-bg="images/your-file.jpg"` to any HTML
   element. The loader in `public/site.js` picks it up automatically and only
   shows the photo if it loads successfully.
