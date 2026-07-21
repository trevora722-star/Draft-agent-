#!/usr/bin/env python3
"""Mint an Apple Distribution signing certificate via the App Store Connect API.

Runs on the CI Mac so no local Mac or Xcode is ever needed: generates a fresh
private key + CSR, asks Apple to issue a distribution certificate for it, and
bundles both into dist.p12 for the keychain. The p12 password is exported to
GITHUB_ENV as P12_PASSWORD_GEN.

Requires env: ASC_KEY_ID, ASC_ISSUER_ID, and the .p8 at
~/private_keys/AuthKey_<ASC_KEY_ID>.p8
"""
import base64
import os
import re
import secrets
import subprocess
import sys
import time

import jwt
import requests

API = "https://api.appstoreconnect.apple.com/v1"


def normalize_p8(raw: str) -> str:
    """Rebuild clean PEM framing regardless of how the key was pasted.

    Handles CRLF line endings, stray indentation, a single-line paste, or a
    paste missing the BEGIN/END banner lines entirely.
    """
    m = re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(.*?)-----END", raw, re.S)
    body = re.sub(r"\s+", "", m.group(1) if m else raw)
    body = re.sub(r"^.*?KEY-----", "", body)  # leftover banner fragments
    body = body.replace("-----ENDPRIVATEKEY-----", "")
    try:
        base64.b64decode(body, validate=True)
    except Exception:
        print("::error::The ASC_KEY_P8 secret does not contain a valid key. "
              "Re-paste the entire .p8 file contents (including the BEGIN/END "
              "lines) into the secret and re-run.")
        sys.exit(1)
    wrapped = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{wrapped}\n-----END PRIVATE KEY-----\n"


key_id = os.environ["ASC_KEY_ID"].strip()
issuer = os.environ["ASC_ISSUER_ID"].strip()

UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
KEYID_RE = re.compile(r"^[A-Z0-9]{10}$")
# Users sometimes paste these two into each other's secret — detect and swap.
if UUID_RE.match(key_id) and KEYID_RE.match(issuer):
    print("::warning::ASC_KEY_ID and ASC_ISSUER_ID look swapped — auto-swapping.")
    key_id, issuer = issuer, key_id
elif not KEYID_RE.match(key_id):
    print(f"::error::ASC_KEY_ID doesn't look like an App Store Connect Key ID "
          f"(expected 10 characters like 2X9R4HXF34). Check the secret value.")
    sys.exit(1)
elif not UUID_RE.match(issuer):
    print("::error::ASC_ISSUER_ID doesn't look like an Issuer ID (expected a "
          "36-character dashed UUID from the top of the API keys page).")
    sys.exit(1)
p8_path = os.path.expanduser(f"~/private_keys/AuthKey_{key_id}.p8")
with open(p8_path) as f:
    p8 = normalize_p8(f.read())
# Rewrite the file too: xcodebuild reads it later via -authenticationKeyPath.
with open(p8_path, "w") as f:
    f.write(p8)

# exp must stay comfortably under Apple's 20-minute ceiling; sitting exactly
# at the limit intermittently 401s when clocks skew.
now = int(time.time())
token = jwt.encode(
    {"iss": issuer, "iat": now - 30, "exp": now + 600, "aud": "appstoreconnect-v1"},
    p8,
    algorithm="ES256",
    headers={"kid": key_id},
)
headers = {"Authorization": f"Bearer {token}"}

subprocess.run(
    ["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
     "-keyout", "dist.key", "-out", "dist.csr",
     "-subj", "/CN=PiledUp CI Distribution/O=PiledUp CI"],
    check=True,
)
with open("dist.csr") as f:
    csr = f.read()

resp = requests.post(
    f"{API}/certificates",
    headers={**headers, "Content-Type": "application/json"},
    json={"data": {"type": "certificates",
                   "attributes": {"certificateType": "DISTRIBUTION",
                                  "csrContent": csr}}},
    timeout=60,
)
if resp.status_code == 401:
    print("::error::Apple rejected the API credentials (401). Check that: "
          "(1) ASC_KEY_ID matches the X's in your downloaded AuthKey_XXXXXXXXXX.p8 "
          "filename, (2) ASC_ISSUER_ID is the Issuer ID from the top of the same "
          "App Store Connect API page, (3) the key is a TEAM key (not an "
          "Individual key) and has not been revoked, and (4) ASC_KEY_P8 is the "
          ".p8 file matching that Key ID.")
    print(resp.text)
    sys.exit(1)
if resp.status_code >= 400:
    print(f"::error::App Store Connect refused to issue a certificate "
          f"(HTTP {resp.status_code}): {resp.text}")
    if "maximum" in resp.text.lower() or "already" in resp.text.lower():
        print("::error::Your team likely hit Apple's distribution-certificate "
              "limit. Revoke an unused one at "
              "https://developer.apple.com/account/resources/certificates "
              "and re-run. (Revoking does NOT affect builds already on "
              "TestFlight or the App Store.)")
    sys.exit(1)

data = resp.json()["data"]
cert_id = data["id"]
with open("dist.cer", "wb") as f:
    f.write(base64.b64decode(data["attributes"]["certificateContent"]))
subprocess.run(
    ["openssl", "x509", "-inform", "DER", "-in", "dist.cer", "-out", "dist.pem"],
    check=True,
)

pw = secrets.token_hex(12)
subprocess.run(
    ["openssl", "pkcs12", "-export", "-inkey", "dist.key", "-in", "dist.pem",
     "-out", "dist.p12", "-passout", f"pass:{pw}"],
    check=True,
)
with open(os.environ["GITHUB_ENV"], "a") as f:
    f.write(f"P12_PASSWORD_GEN={pw}\n")
print(f"Issued Apple Distribution certificate {cert_id} "
      f"({data['attributes'].get('name', 'unnamed')}, "
      f"expires {data['attributes'].get('expirationDate', '?')})")

# ---- Provisioning profile ---------------------------------------------
# Cloud signing (xcodebuild -allowProvisioningUpdates creating profiles)
# needs an Admin key, so build the App Store profile ourselves and sign
# manually — App Manager keys are allowed to do this via the API.
import json

with open("capacitor.config.json") as f:
    BUNDLE_ID = json.load(f)["appId"]
PROFILE_NAME = "PiledUp CI AppStore"
JSON_H = {**headers, "Content-Type": "application/json"}

r = requests.get(f"{API}/bundleIds", headers=headers,
                 params={"filter[identifier]": BUNDLE_ID}, timeout=60)
r.raise_for_status()
matches = [b for b in r.json()["data"]
           if b["attributes"]["identifier"] == BUNDLE_ID]
if matches:
    bundle_res_id = matches[0]["id"]
else:
    r = requests.post(f"{API}/bundleIds", headers=JSON_H, timeout=60,
                      json={"data": {"type": "bundleIds",
                            "attributes": {"identifier": BUNDLE_ID,
                                           "name": "PiledUp",
                                           "platform": "IOS"}}})
    if r.status_code >= 400:
        print(f"::error::Could not register bundle id {BUNDLE_ID}: {r.text}")
        sys.exit(1)
    bundle_res_id = r.json()["data"]["id"]

# Remove stale CI profiles (they reference certificates from earlier runs).
r = requests.get(f"{API}/profiles", headers=headers,
                 params={"filter[name]": PROFILE_NAME}, timeout=60)
if r.ok:
    for prof in r.json().get("data", []):
        requests.delete(f"{API}/profiles/{prof['id']}", headers=headers,
                        timeout=60)

r = requests.post(f"{API}/profiles", headers=JSON_H, timeout=60,
                  json={"data": {
                      "type": "profiles",
                      "attributes": {"name": PROFILE_NAME,
                                     "profileType": "IOS_APP_STORE"},
                      "relationships": {
                          "bundleId": {"data": {"type": "bundleIds",
                                                "id": bundle_res_id}},
                          "certificates": {"data": [{"type": "certificates",
                                                     "id": cert_id}]}}}})
if r.status_code >= 400:
    print(f"::error::Could not create provisioning profile: {r.text}")
    sys.exit(1)
prof = r.json()["data"]["attributes"]
prof_dir = os.path.expanduser("~/Library/MobileDevice/Provisioning Profiles")
os.makedirs(prof_dir, exist_ok=True)
prof_path = os.path.join(prof_dir, f"{prof['uuid']}.mobileprovision")
with open(prof_path, "wb") as f:
    f.write(base64.b64decode(prof["profileContent"]))
print(f"Installed provisioning profile '{PROFILE_NAME}' ({prof['uuid']}) "
      f"for {BUNDLE_ID}")
