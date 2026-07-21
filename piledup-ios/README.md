# PiledUp — iOS App (TestFlight)

Native iOS wrapper around the PiledUp web game (`www/index.html`), built with
Capacitor. Two ways to get it onto TestFlight — pick whichever fits.

- **App name:** PiledUp
- **Bundle ID:** `com.pileupgame.app` — change it in `capacitor.config.json`
  AND in Xcode (App target → Signing & Capabilities) if this ID is taken.
- **Content note:** the PRO mode contains explicit language behind an in-app
  18+ gate. In App Store Connect, set the age rating questionnaire honestly
  (Profanity/Crude Humor: Frequent → 17+) or the build will be rejected.

---

## Path A — You have a Mac with Xcode (easiest, ~15 minutes)

1. Clone this repo on the Mac and run:
   ```bash
   cd piledup-ios
   npm install
   npx cap sync ios
   npx cap open ios     # opens the project in Xcode
   ```
2. In Xcode: select the **App** target → **Signing & Capabilities** →
   check **Automatically manage signing** → pick your **Team** (your Apple
   Developer account).
3. At the top, set the destination to **Any iOS Device (arm64)**.
4. Menu **Product → Archive**. When it finishes, click
   **Distribute App → TestFlight & App Store → Upload**.
5. Go to [App Store Connect](https://appstoreconnect.apple.com) → your app →
   **TestFlight** tab. The build appears after ~10–30 min of processing.
   Fill in the export-compliance question (this app uses no custom
   encryption), then add testers.

## Path B — No Mac needed at all: GitHub Actions does everything

The workflow at `.github/workflows/testflight.yml` builds and uploads on a
cloud Mac, and **mints its own Apple signing certificate** through the App
Store Connect API (`scripts/asc_make_cert.py`) — so no Mac, no Xcode, and no
certificate export are ever required.

One-time setup, doable entirely from a phone browser — add these four
**repository secrets** at
GitHub → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Where to get it |
|---|---|
| `APPLE_TEAM_ID` | [developer.apple.com/account](https://developer.apple.com/account) → Membership details → Team ID (10 characters) |
| `ASC_KEY_ID` | [App Store Connect → Users and Access → Integrations](https://appstoreconnect.apple.com/access/integrations/api) → App Store Connect API → generate a key with **App Manager** role. This is its Key ID. |
| `ASC_ISSUER_ID` | Same page — Issuer ID (shown above the key list) |
| `ASC_KEY_P8` | The `.p8` file you download when creating that key (download allowed once). Open it in a text editor / Files app and paste the full contents, including the BEGIN/END PRIVATE KEY lines. |

> ⚠️ The `.p8` key is a credential for your whole Apple developer team.
> Only ever paste it into the GitHub *secrets* page — never into a chat,
> a commit, or an issue. This repository is public.

The app record must already exist in App Store Connect (it does, if testers
already use the app). For a brand-new app: My Apps → **+** → New App →
bundle ID `com.pileupgame.app`.

Trigger a release by pushing to the `testflight-release` branch, or from
GitHub → **Actions** tab → *Build & Upload PiledUp to TestFlight* →
**Run workflow**. Each run uploads a new build to TestFlight automatically.

> **If Apple refuses to issue a certificate** ("maximum number of
> certificates"), revoke an unused Apple Distribution certificate at
> [developer.apple.com/account/resources/certificates](https://developer.apple.com/account/resources/certificates)
> and re-run. Revoking never affects builds already on TestFlight or the
> App Store — it only stops future signing with that particular certificate.

## Updating the game

The iOS app serves `www/index.html`. When the web game
(`public/piledup.html`) changes, refresh the copy and re-upload:

```bash
cp ../public/piledup.html www/index.html
cp ../public/piledup-icon.png ../public/piledup.webmanifest www/
```

Then run the workflow (Path B) or re-archive in Xcode (Path A).
