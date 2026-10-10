"""
One-off test: creates a single sandboxed pin so you can see the whole
flow work before wiring it into anything real.

Run with: python3 tests/test_create_pin.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from pinterest.pinterest_pins import create_pin  # noqa: E402


def main():
    result = create_pin(
        board_id="905786612495800306",
        title="Test Pin - Witty Nostalgia",
        description="Just testing the API integration, ignore this one.",
        link="https://wittynostalgia.com",
        image_url="https://wittynostalgia.com/cdn/shop/files/4790109222082947972_2048.jpg?v=1788036576&width=1200",
    )

    print("Pin created:")
    print("  id:", result.get("id"))
    print("  full response:", result)


if __name__ == "__main__":
    main()
