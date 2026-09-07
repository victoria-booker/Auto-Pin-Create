"""
One-off test: creates a single sandboxed pin so you can see the whole
flow work before wiring it into anything real.

Run with: python3 test_create_pin.py
"""
from pinterest_pins import create_pin

result = create_pin(
    board_id="905786612495800306",  # 90's Nostalgia board
    title="Test Pin - Nostalgic Word Search Book",
    description="Just testing the API integration, ignore this one.",
    link="https://wittynostalgia.com",
    image_url="https://wittynostalgia.com/cdn/shop/files/witty-nostalgia-hero-banner.png",
)

print("Pin created:")
print("  id:", result.get("id"))
print("  full response:", result)
