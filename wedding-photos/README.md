# Wedding Photo Album

A private photo- and video-sharing site for wedding guests. One shared
username and password protects everything: only people you give the login to
can see the gallery or upload.

## What guests get

- A simple sign-in page (works great on phones)
- A gallery of every photo and video shared so far, newest first, with a
  full-screen viewer (videos play right in it) and full-resolution download
- An **Upload photos & videos** button that accepts multiple files at once —
  including iPhone HEIC photos, which are converted to JPEG automatically.
  Photos up to 30 MB, videos (MP4/MOV/M4V/WebM) up to 200 MB
- An optional "Your name" field so each photo shows who shared it

## Admin: deleting photos

Set `WEDDING_ADMIN_PASSWORD` to a *different* password that only you know.
Sign in with the **same username** but the admin password and you'll see an
"Admin" badge plus a delete button on every photo (and in the full-screen
viewer). Deleting removes the item from the gallery for everyone but moves
the files to a `trash/` folder inside the data directory rather than
destroying them — a mistaken delete can be undone by moving the files back.
If `WEDDING_ADMIN_PASSWORD` is unset, nobody can delete anything.

## Deploy on Render (recommended)

The `render.yaml` at the root of this branch is a one-click Render Blueprint:

1. Push this branch to GitHub (already done if you're reading it there).
2. In [Render](https://dashboard.render.com): **New + → Blueprint**, pick this
   repo, and choose the branch `claude/wedding-photo-platform-mxs50e`.
3. When prompted, set:
   - `WEDDING_USERNAME` — the username you'll give guests (e.g. `guests`)
   - `WEDDING_PASSWORD` — the password you'll give guests
   - `WEDDING_TITLE` — optional, e.g. `Anna & James — June 2026`
4. Deploy. Render gives you a URL like `wedding-photos.onrender.com` —
   share it (plus the username and password) with the other guests.

**Important — persistent storage.** The blueprint attaches a 10 GB disk so
uploaded photos survive restarts and deploys. Render disks require a paid
plan (Starter, ~$7/month, plus ~$0.25/GB/month for the disk). If you use the
free plan instead, remove the `disk:` section — but be aware the free plan's
filesystem is wiped on every restart, so **uploaded photos would be lost**.
For real wedding photos, use the paid plan or download backups regularly.

## Settings (environment variables)

| Variable | Required | What it does |
|---|---|---|
| `WEDDING_USERNAME` | yes | Shared username guests type to sign in (default `guest`) |
| `WEDDING_PASSWORD` | yes | Shared password (a dev default of `wedding` is used locally if unset) |
| `WEDDING_ADMIN_PASSWORD` | no | Separate password that grants delete rights (same username). Unset = no one can delete |
| `WEDDING_TITLE` | no | Title shown on the site (default "Our Wedding Album") |
| `WEDDING_DATA_DIR` | no | Where photos are stored (default `./data`; the Render blueprint sets `/var/data`) |
| `WEDDING_SECRET_KEY` | no | Cookie-signing secret; auto-generated and saved in the data dir if unset |

## Run locally

```bash
cd wedding-photos
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://127.0.0.1:8000 and sign in with `guest` / `wedding`.

## Tests

```bash
cd wedding-photos
python -m pytest test_app.py
```

## Notes on how it works

- Login sets a signed, HTTP-only session cookie valid for 30 days; every
  page, API call, photo, and thumbnail checks it.
- Login attempts are rate-limited (20 per 15 minutes per IP).
- Photo uploads are capped at 30 MB, validated as real images, and EXIF
  rotation is corrected. Thumbnails are generated for a fast gallery.
  Videos are capped at 200 MB and streamed to disk.
- Photos, videos, thumbnails, and metadata are plain files under the data
  directory — backing up is just copying that folder. Deleted items sit in
  `trash/` inside it.
