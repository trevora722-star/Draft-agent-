# PiledUp — iOS App (TestFlight)

Native iOS wrapper around the PiledUp web game (`www/index.html`), built with
Capacitor. Two ways to get it onto TestFlight — pick whichever fits.

- **App name:** PiledUp
- **Bundle ID:** `com.piledup.game` — change it in `capacitor.config.json`
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

## Path B — No Mac: GitHub Actions builds it for you

The workflow at `.github/workflows/testflight.yml` builds and uploads on a
cloud Mac. One-time setup — add these **repository secrets**
(GitHub → Settings → Secrets and variables → Actions):

| Secret | Where to get it |
|---|---|
| `APPLE_TEAM_ID` | [developer.apple.com/account](https://developer.apple.com/account) → Membership details → Team ID (10 characters) |
| `ASC_KEY_ID` | App Store Connect → Users and Access → **Integrations** → App Store Connect API → generate a key with **App Manager** role. This is its Key ID. |
| `ASC_ISSUER_ID` | Same page — Issuer ID (shown above the key list) |
| `ASC_KEY_P8` | The `.p8` file you download when creating that key (download allowed once). Paste the full text contents. |
| `BUILD_CERT_P12_BASE64` | An **Apple Distribution** certificate exported as `.p12`, base64-encoded (`base64 -i cert.p12 | pbcopy` on a Mac). See note below if you have no Mac at all. |
| `P12_PASSWORD` | The password you set when exporting the `.p12` |

Then: **one-time in App Store Connect**, create the app record
(My Apps → **+** → New App → platform iOS, bundle ID `com.piledup.game` —
register the bundle ID first at developer.apple.com → Identifiers if it
isn't listed).

Finally: GitHub → **Actions** tab → *Build & Upload PiledUp to TestFlight* →
**Run workflow**. Each run uploads a new build to TestFlight automatically.

> **No Mac at all?** The distribution certificate is the one step Apple makes
> hard without one. Options: create the CSR + export with OpenSSL, borrow any
> Mac for 10 minutes, or use a cloud Mac (MacStadium, AWS EC2 Mac). Once the
> `.p12` secret is set you never need the Mac again.

## Updating the game

The iOS app serves `www/index.html`. When the web game
(`public/piledup.html`) changes, refresh the copy and re-upload:

```bash
cp ../public/piledup.html www/index.html
cp ../public/piledup-icon.png ../public/piledup.webmanifest www/
```

Then run the workflow (Path B) or re-archive in Xcode (Path A).
