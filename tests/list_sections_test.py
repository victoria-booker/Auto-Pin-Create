"""
Lists the sections inside one board, with their IDs.

Run with: python3 tests/list_sections_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from pinterest.pinterest_pins import list_sections  # noqa: E402

BOARD_ID = "905786612495800306"  # 90's Nostalgia board

for s in list_sections(BOARD_ID):
    print(s["id"], "-", s["name"])
