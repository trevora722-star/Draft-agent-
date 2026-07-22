#!/usr/bin/env python3
"""Report and unblock the latest TestFlight build via the ASC API.

- Prints the newest builds and their processing state.
- If the latest build is VALID but missing the export-compliance answer,
  sets usesNonExemptEncryption=false (this app uses no custom encryption).
- Adds the latest VALID build to every beta group so testers receive it,
  and submits it for beta review when one is required.
"""
import base64
import json
import os
import re
import sys
import time

import jwt
import requests

API = "https://api.appstoreconnect.apple.com/v1"
BUNDLE_ID = "com.pileupgame.app"


def normalize_p8(raw):
    m = re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(.*?)-----END", raw, re.S)
    body = re.sub(r"\s+", "", m.group(1) if m else raw)
    wrapped = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{wrapped}\n-----END PRIVATE KEY-----\n"


key_id = os.environ["ASC_KEY_ID"].strip()
issuer = os.environ["ASC_ISSUER_ID"].strip()
with open(os.path.expanduser(f"~/private_keys/AuthKey_{key_id}.p8")) as f:
    p8 = normalize_p8(f.read())
now = int(time.time())
token = jwt.encode(
    {"iss": issuer, "iat": now - 30, "exp": now + 600, "aud": "appstoreconnect-v1"},
    p8, algorithm="ES256", headers={"kid": key_id})
H = {"Authorization": f"Bearer {token}"}
JH = {**H, "Content-Type": "application/json"}

r = requests.get(f"{API}/apps", headers=H,
                 params={"filter[bundleId]": BUNDLE_ID}, timeout=60)
r.raise_for_status()
apps = r.json()["data"]
if not apps:
    print(f"::error::No app with bundle id {BUNDLE_ID} found in App Store Connect.")
    sys.exit(1)
app_id = apps[0]["id"]
app_name = apps[0]["attributes"]["name"]
print(f"App: {app_name} ({BUNDLE_ID}, id {app_id})")

r = requests.get(f"{API}/builds", headers=H, timeout=60,
                 params={"filter[app]": app_id, "sort": "-uploadedDate",
                         "limit": 5,
                         "fields[builds]": "version,processingState,"
                         "uploadedDate,expired,usesNonExemptEncryption"})
r.raise_for_status()
builds = r.json()["data"]
if not builds:
    print("::error::No builds found — the upload may not have registered.")
    sys.exit(1)

print("\nRecent builds:")
for b in builds:
    a = b["attributes"]
    print(f"  build {a['version']}: {a['processingState']}"
          f" (uploaded {a['uploadedDate']},"
          f" compliance={'UNANSWERED' if a['usesNonExemptEncryption'] is None else a['usesNonExemptEncryption']},"
          f" expired={a['expired']})")

latest = builds[0]
lid = latest["id"]
la = latest["attributes"]
state = la["processingState"]

if state == "PROCESSING":
    print(f"\nRESULT: build {la['version']} is still PROCESSING on Apple's side.")
    sys.exit(0)
if state in ("FAILED", "INVALID"):
    print(f"\n::error::RESULT: build {la['version']} processing state is {state}. "
          "Check App Store Connect for details (often an asset/icon issue).")
    sys.exit(1)

print(f"\nBuild {la['version']} is {state}.")

if la["usesNonExemptEncryption"] is None:
    pr = requests.patch(f"{API}/builds/{lid}", headers=JH, timeout=60,
                        json={"data": {"type": "builds", "id": lid,
                              "attributes": {"usesNonExemptEncryption": False}}})
    print("Set export compliance (no non-exempt encryption): "
          f"HTTP {pr.status_code}")

r = requests.get(f"{API}/betaGroups", headers=H, timeout=60,
                 params={"filter[app]": app_id, "limit": 50})
r.raise_for_status()
groups = r.json()["data"]
if not groups:
    print("No beta groups found — internal App Store Connect users will "
          "still see the build in TestFlight.")
for g in groups:
    gname = g["attributes"]["name"]
    internal = g["attributes"].get("isInternalGroup")
    ar = requests.post(f"{API}/betaGroups/{g['id']}/relationships/builds",
                       headers=JH, timeout=60,
                       json={"data": [{"type": "builds", "id": lid}]})
    print(f"Added build to group '{gname}' (internal={internal}): "
          f"HTTP {ar.status_code} {'' if ar.status_code < 400 else ar.text[:300]}")

# External groups need a beta review submission for the first build of a version.
sr = requests.post(f"{API}/betaAppReviewSubmissions", headers=JH, timeout=60,
                   json={"data": {"type": "betaAppReviewSubmissions",
                         "relationships": {"build": {"data": {
                             "type": "builds", "id": lid}}}}})
if sr.status_code < 400:
    print("Submitted build for TestFlight beta review (needed for external "
          "testers; usually approved within a day).")
elif "already" in sr.text.lower() or sr.status_code == 409:
    print("Beta review: already submitted/approved or not required.")
else:
    print(f"Beta review submission: HTTP {sr.status_code} {sr.text[:300]}")

print("\nRESULT: build distributed. Internal testers get it immediately; "
      "external groups as soon as beta review clears.")
