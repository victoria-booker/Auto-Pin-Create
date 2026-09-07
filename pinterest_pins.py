"""
Pinterest pin creation — the part you actually call from your pipeline.

Reads pinterest_tokens.json (from pinterest_setup.py), refreshes the
access token if needed, and gives you list_boards() and create_pin().

pip install requests
"""
import base64
import json
import time

import requests

from pinterest_config import CLIENT_ID, CLIENT_SECRET

TOKEN_FILE = "pinterest_tokens.json"

API_BASE = "https://api.pinterest.com/v5"


def _load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)


def _save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def _refresh_access_token(tokens):
    basic_auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        f"{API_BASE}/oauth/token",
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        },
    )
    resp.raise_for_status()
    new_tokens = resp.json()
    # Pinterest doesn't always return a new refresh_token — keep the old one if so
    new_tokens.setdefault("refresh_token", tokens["refresh_token"])
    new_tokens["fetched_at"] = time.time()
    _save_tokens(new_tokens)
    return new_tokens["access_token"]


def get_access_token():
    tokens = _load_tokens()
    fetched_at = tokens.get("fetched_at", 0)
    expires_in = tokens.get("expires_in", 0)
    # refresh a bit early rather than right at the edge
    if time.time() > fetched_at + expires_in - 300:
        return _refresh_access_token(tokens)
    return tokens["access_token"]


def _headers():
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
    }


def list_boards():
    """Returns your boards so you can grab board_id values once and hardcode them."""
    resp = requests.get(f"{API_BASE}/boards", headers=_headers())
    resp.raise_for_status()
    return resp.json()["items"]


def list_sections(board_id):
    """Returns the sections within one board, so you can grab a section id
    once and hardcode it alongside the board_id."""
    resp = requests.get(f"{API_BASE}/boards/{board_id}/sections", headers=_headers())
    resp.raise_for_status()
    return resp.json()["items"]


def create_pin(board_id, title, description, link, image_url, alt_text=None, board_section_id=None):
    """
    board_id:          from list_boards()
    board_section_id:  optional, from list_sections(board_id) — omit to pin
                        straight to the board with no section
    image_url:         must be a public URL Pinterest can fetch — not a local file
    """
    payload = {
        "board_id": board_id,
        "title": title,
        "description": description,
        "link": link,
        "media_source": {
            "source_type": "image_url",
            "url": image_url,
        },
    }
    if alt_text:
        payload["alt_text"] = alt_text
    if board_section_id:
        payload["board_section_id"] = board_section_id

    resp = requests.post(f"{API_BASE}/pins", headers=_headers(), json=payload)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    # quick manual test
    boards = list_boards()
    for b in boards:
        print(b["id"], "-", b["name"])
