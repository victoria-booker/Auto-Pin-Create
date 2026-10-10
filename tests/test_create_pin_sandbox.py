"""Manual sandbox pin smoke test; never runs as part of test discovery."""
import os

import requests

API_BASE = "https://api-sandbox.pinterest.com/v5"

PAYLOAD = {
    "board_id": "905786612495800306",
    "title": "Test Pin - Nostalgic Word Search Book",
    "description": "Just testing the API integration, ignore this one.",
    "link": "https://wittynostalgia.com",
    "media_source": {
        "source_type": "image_url",
        "url": "https://wittynostalgia.com/cdn/shop/files/witty-nostalgia-hero-banner.png",
    },
}


def main():
    token = os.environ.get("PINTEREST_SANDBOX_TOKEN")
    if not token:
        raise SystemExit("Set PINTEREST_SANDBOX_TOKEN to run this manual sandbox test.")

    response = requests.post(
        f"{API_BASE}/pins",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=PAYLOAD,
        timeout=20,
    )
    print(response.status_code)
    print(response.json())


if __name__ == "__main__":
    main()