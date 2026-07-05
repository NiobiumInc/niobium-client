#!/usr/bin/env python3
# Copyright 2024-present Niobium Microsystems, Inc.
# Licensed under the Apache License, Version 2.0.
#
# fog_token.py — mint (reset) your Fog API token from username + password.
#
# Prompts for credentials, logs in via the OAuth2 password flow, rotates the
# API token, and prints the new token to stdout (prompts/hints go to stderr,
# so `export FOG_API_TOKEN=$(fog_token.py)` works). Rotating invalidates any
# previous token. Standalone — only dependency is httpx (bundles its own CAs).
#
#   fog_token.py                    # prompt for both
#   fog_token.py -u me@example.com  # prompt only for password
#
# Env: FOG_API_URL [default https://api.beta.niobium.co].
import argparse
import getpass
import os
import sys

import httpx


def die(msg):
    sys.exit(f"[fog_token] {msg}")


def _detail(resp):
    try:
        body = resp.json()
        return body.get("detail") if isinstance(body, dict) else body
    except ValueError:
        return resp.text


def main(argv):
    p = argparse.ArgumentParser(description="Mint a fresh Fog API token from your login.")
    p.add_argument("-u", "--username", help="account email (else prompt)")
    args = p.parse_args(argv)

    api = os.environ.get("FOG_API_URL", "https://api.beta.niobium.co").rstrip("/")
    email = args.username
    if not email:
        print("Username (email): ", end="", file=sys.stderr, flush=True)
        email = sys.stdin.readline().strip()
    password = getpass.getpass("Password: ")  # writes prompt to stderr/tty
    if not (email and password):
        die("username and password are required")

    try:
        # 1. OAuth2 password flow -> access token (form-encoded, no trailing slash).
        r = httpx.post(f"{api}/auth/token",
                       data={"username": email, "password": password}, timeout=60)
        if r.status_code != 200:
            die(f"login failed (HTTP {r.status_code}): {_detail(r)}")
        access = r.json().get("access_token") or die("login: no access_token in response")

        # 2. rotate the API token (Bearer-authed).
        r = httpx.post(f"{api}/users/me/token/reset",
                       headers={"Authorization": f"Bearer {access}"}, timeout=60)
    except httpx.RequestError as e:
        die(f"cannot reach {api}: {e}")
    if r.status_code != 200:
        die(f"token reset failed (HTTP {r.status_code}): {_detail(r)}")
    token = r.json().get("api_token") or die("reset: no api_token in response")

    print(token)  # stdout only -> capturable
    print(f"\nexport FOG_API_TOKEN={token}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
