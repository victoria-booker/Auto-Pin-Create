"""
Pinterest pin creation — the part you actually call from your pipeline.

Reads pinterest_tokens.json (from pinterest_setup.py), refreshes the
access token if needed, and gives you list_boards() and create_pin().

pip install requests
"""
import base64
import json
import os
import time

import requests

from pinterest.runtime_config import CLIENT_ID, CLIENT_SECRET, PINTEREST_API_BASE, PINTEREST_ENV
from pinterest.pinterest_boards import resolve_board_name

TOKEN_FILE = os.environ.get("PINTEREST_TOKEN_FILE", "pinterest_tokens.json")
TOKEN_BOOTSTRAP_ENV = "PINTEREST_TOKENS_JSON_B64"

API_BASE = PINTEREST_API_BASE


def _load_tokens():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        encoded_tokens = os.environ.get(TOKEN_BOOTSTRAP_ENV)
        if not encoded_tokens:
            raise RuntimeError(
                f"Pinterest token file {TOKEN_FILE} is missing. Set {TOKEN_BOOTSTRAP_ENV} "
                "to bootstrap it, or run pinterest_setup.py."
            ) from exc

    try:
        tokens = json.loads(base64.b64decode(encoded_tokens, validate=True))
        if not isinstance(tokens, dict):
            raise ValueError("The token payload must be a JSON object.")
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{TOKEN_BOOTSTRAP_ENV} must contain base64-encoded Pinterest token JSON.") from exc

    _save_tokens(tokens)
    return tokens


def _save_tokens(tokens):
    token_directory = os.path.dirname(TOKEN_FILE)
    if token_directory:
        os.makedirs(token_directory, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)


def _tokens_for_environment(all_tokens):
    environments = all_tokens.get("environments")
    if environments is not None:
        tokens = environments.get(PINTEREST_ENV)
        if tokens is None:
            raise RuntimeError(
                f"No Pinterest {PINTEREST_ENV} token is stored in {TOKEN_FILE}. "
                "Run pinterest_setup.py with the matching PINTEREST_ENV."
            )
        return tokens

    if PINTEREST_ENV == "production" and "access_token" in all_tokens:
        return all_tokens

    raise RuntimeError(
        f"{TOKEN_FILE} does not contain a Pinterest {PINTEREST_ENV} token. "
        "Run pinterest_setup.py with the matching PINTEREST_ENV."
    )


def _save_tokens_for_environment(tokens):
    try:
        all_tokens = _load_tokens()
    except FileNotFoundError:
        all_tokens = {}

    environments = all_tokens.get("environments", {})
    if not environments and "access_token" in all_tokens:
        environments["production"] = all_tokens
    environments[PINTEREST_ENV] = tokens
    _save_tokens({"environments": environments})


def _refresh_access_token(tokens):
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("Set PINTEREST_CLIENT_ID and PINTEREST_CLIENT_SECRET before refreshing Pinterest tokens.")
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
    _raise_pinterest_error(resp)
    new_tokens = resp.json()
    # Pinterest doesn't always return a new refresh_token — keep the old one if so
    new_tokens.setdefault("refresh_token", tokens["refresh_token"])
    new_tokens["fetched_at"] = time.time()
    _save_tokens_for_environment(new_tokens)
    return new_tokens["access_token"]


def get_access_token():
    tokens = _tokens_for_environment(_load_tokens())
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


def _raise_pinterest_error(response):
    if response.ok:
        return

    try:
        error_body = response.json()
    except ValueError:
        error_body = response.text.strip()

    if isinstance(error_body, dict):
        error_message = error_body.get("message") or error_body.get("error")
        error_code = error_body.get("code")
        details = error_body.get("details")
        parts = [f"Pinterest HTTP {response.status_code}"]
        if error_code is not None:
            parts.append(f"code={error_code}")
        if error_message:
            parts.append(str(error_message))
        if details:
            parts.append(f"details={details}")
        detail = "; ".join(parts)
    else:
        detail = f"Pinterest HTTP {response.status_code}: {error_body or 'No response body'}"

    request_id = response.headers.get("x-request-id") or response.headers.get("x-pinterest-request-id")
    if request_id:
        detail += f"; request_id={request_id}"

    raise requests.HTTPError(detail, response=response)


def list_boards():
    """Return all boards, following Pinterest pagination."""
    boards = []
    bookmark = None
    while True:
        params = {"page_size": 100}
        if bookmark:
            params["bookmark"] = bookmark
        resp = requests.get(f"{API_BASE}/boards", headers=_headers(), params=params)
        _raise_pinterest_error(resp)
        payload = resp.json()
        boards.extend(payload.get("items", []))
        bookmark = payload.get("bookmark")
        if not bookmark:
            return boards


def list_sections(board_id):
    """Returns the sections within one board, so you can grab a section id
    once and hardcode it alongside the board_id."""
    resp = requests.get(f"{API_BASE}/boards/{board_id}/sections", headers=_headers())
    _raise_pinterest_error(resp)
    return resp.json()["items"]


def get_board_by_name(name):
    for board in list_boards():
        if board.get("name", "").strip().lower() == name.strip().lower():
            return board
    return None


def create_board(name, description=None):
    payload = {"name": name}
    if description is not None:
        payload["description"] = description
    resp = requests.post(f"{API_BASE}/boards", headers=_headers(), json=payload)
    _raise_pinterest_error(resp)
    return resp.json()


def ensure_board(name, description=None):
    board = get_board_by_name(name)
    if board:
        return board, False
    configured_board_id = resolve_board_name(name)
    if configured_board_id:
        return {"id": configured_board_id, "name": name}, False
    try:
        return create_board(name, description=description), True
    except requests.HTTPError as exc:
        if "code=58" not in str(exc):
            raise
        board = get_board_by_name(name)
        if board:
            return board, False
        raise


def get_section_by_name(board_id, section_name):
    for section in list_sections(board_id):
        if section.get("name", "").strip().lower() == section_name.strip().lower():
            return section
    return None


def create_section(board_id, name):
    resp = requests.post(f"{API_BASE}/boards/{board_id}/sections", headers=_headers(), json={"name": name})
    _raise_pinterest_error(resp)
    return resp.json()


def ensure_section(board_id, section_name):
    section = get_section_by_name(board_id, section_name)
    if section:
        return section, False
    return create_section(board_id, section_name), True


def upload_image_bytes(image_bytes, filename="pin.png", content_type="image/png"):
    """Uploads image bytes to Pinterest and returns a Pinterest media_id."""
    create_resp = requests.post(
        f"{API_BASE}/media",
        headers=_headers(),
        json={"media_type": "image"},
    )
    _raise_pinterest_error(create_resp)
    upload_data = create_resp.json()

    upload_url = upload_data.get("upload_url")
    if not upload_url:
        raise ValueError(f"Pinterest media upload did not return an upload_url: {upload_data}")

    put_resp = requests.put(
        upload_url,
        headers={"Content-Type": content_type},
        data=image_bytes,
    )
    _raise_pinterest_error(put_resp)

    media_id = upload_data.get("media_id")
    if not media_id:
        raise ValueError(f"Pinterest media upload did not return a media_id: {upload_data}")
    return media_id


def create_pin(board_id, title, description, link, image_url=None, alt_text=None, board_section_id=None, image_bytes=None, filename="pin.png", content_type="image/png"):
    """
    board_id:          from list_boards()
    board_section_id:  optional, from list_sections(board_id) — omit to pin
                        straight to the board with no section
    image_url:         public URL Pinterest can fetch, or omit when using image_bytes
    image_bytes:       raw image bytes to upload directly to Pinterest
    """
    if image_bytes is not None:
        media_id = upload_image_bytes(image_bytes, filename=filename, content_type=content_type)
        media_source = {
            "source_type": "media_id",
            "media_id": media_id,
        }
    else:
        if not image_url:
            raise ValueError("Either image_url or image_bytes must be provided.")
        media_source = {
            "source_type": "image_url",
            "url": image_url,
        }

    payload = {
        "board_id": board_id,
        "title": title,
        "description": description,
        "link": link,
        "media_source": media_source,
    }
    if alt_text:
        payload["alt_text"] = alt_text
    if board_section_id:
        payload["board_section_id"] = board_section_id

    resp = requests.post(f"{API_BASE}/pins", headers=_headers(), json=payload)
    _raise_pinterest_error(resp)
    return resp.json()


if __name__ == "__main__":
    # quick manual test
    boards = list_boards()
    for b in boards:
        print(b["id"], "-", b["name"])
