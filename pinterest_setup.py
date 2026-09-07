"""
Pinterest OAuth2 (PKCE) — one-time setup script.

Run this ONCE per app to get your access + refresh tokens.
It spins up a tiny local server to catch Pinterest's redirect,
exchanges the code for tokens, and saves them to pinterest_tokens.json.

Before running:
  1. Fill in CLIENT_ID and CLIENT_SECRET below (from your Pinterest app).
  2. In the Pinterest app settings, set the Redirect URI to exactly:
       http://localhost:8085/callback
  3. pip install requests

Run with:
  python3 pinterest_setup.py
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser

import requests

from pinterest_config import CLIENT_ID, CLIENT_SECRET

REDIRECT_URI = "http://localhost:8085/callback"
SCOPES = "pins:read,pins:write,boards:read,boards:write"
TOKEN_FILE = "pinterest_tokens.json"

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
        "https://api.pinterest.com/v5/oauth/token",
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
        print("Token exchange failed:", resp.status_code, resp.text)
        return

    tokens = resp.json()
    tokens["fetched_at"] = time.time()
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

    print(f"Saved tokens to {TOKEN_FILE}. Keep this file out of git.")


if __name__ == "__main__":
    main()
