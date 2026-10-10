"""
Pinterest OAuth2 (PKCE) — one-time setup script.

Run this ONCE per app to get your access + refresh tokens.
It spins up a tiny local server to catch Pinterest's redirect,
exchanges the code for tokens, and saves them to pinterest_tokens.json.

Before running:
    1. Set PINTEREST_CLIENT_ID and PINTEREST_CLIENT_SECRET from your Pinterest app.
  2. In the Pinterest app settings, set the Redirect URI to exactly:
       http://localhost:8085/callback
  3. pip install requests

Run with (from the repo root):
  python3 pinterest/pinterest_setup.py
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from pinterest.runtime_config import CLIENT_ID, CLIENT_SECRET, PINTEREST_API_BASE, PINTEREST_ENV  # noqa: E402

REDIRECT_URI = "http://localhost:8085/callback"
SCOPES = "pins:read,pins:write,boards:read,boards:write,trends:read"
TOKEN_FILE = os.environ.get("PINTEREST_TOKEN_FILE", "pinterest_tokens.json")

auth_code_holder = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code_holder["code"] = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Got it, you can close this tab and go back to the terminal.")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No code in redirect - check the app's redirect URI setting.")

    def log_message(self, *args):
        pass  # keep the terminal quiet


def make_pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def main():
    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(16)

    auth_url = "https://www.pinterest.com/oauth/?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    server = http.server.HTTPServer(("localhost", 8085), CallbackHandler)
    thread = threading.Thread(target=server.handle_request)  # handles exactly one request
    thread.start()

    print("Opening Pinterest login in your browser...")
    webbrowser.open(auth_url)
    thread.join()  # waits until the redirect hits localhost:8085

    code = auth_code_holder.get("code")
    if not code:
        print("Never got a code back. Check the redirect URI matches exactly.")
        return

    basic_auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        f"{PINTEREST_API_BASE}/oauth/token",
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
    )

    if resp.status_code != 200:
        try:
            error_body = resp.json()
        except ValueError:
            error_body = resp.text.strip()
        print(f"Token exchange failed for {PINTEREST_ENV}: {resp.status_code}: {error_body}")
        return

    tokens = resp.json()
    tokens["fetched_at"] = time.time()
    try:
        with open(TOKEN_FILE) as f:
            stored_tokens = json.load(f)
    except FileNotFoundError:
        stored_tokens = {}
    environments = stored_tokens.get("environments", {})
    if not environments and "access_token" in stored_tokens:
        environments["production"] = stored_tokens
    environments[PINTEREST_ENV] = tokens
    token_directory = os.path.dirname(TOKEN_FILE)
    if token_directory:
        os.makedirs(token_directory, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump({"environments": environments}, f, indent=2)

    print(f"Saved tokens to {TOKEN_FILE}. Keep this file out of git.")


if __name__ == "__main__":
    main()
