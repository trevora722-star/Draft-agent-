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


key_id = os.environ["ASC_KEY_ID"]
issuer = os.environ["ASC_ISSUER_ID"]
p8_path = os.path.expanduser(f"~/private_keys/AuthKey_{key_id}.p8")
with open(p8_path) as f:
    p8 = normalize_p8(f.read())
# Rewrite the file too: xcodebuild reads it later via -authenticationKeyPath.
with open(p8_path, "w") as f:
    f.write(p8)

now = int(time.time())
token = jwt.encode(
    {"iss": issuer, "iat": now - 30, "exp": now + 1200, "aud": "appstoreconnect-v1"},
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
print(f"Issued Apple Distribution certificate {data['id']} "
      f"({data['attributes'].get('name', 'unnamed')}, "
      f"expires {data['attributes'].get('expirationDate', '?')})")
