"""
Lists the sections inside one board, with their IDs.

Run with: python3 list_sections_test.py
"""
from pinterest_pins import list_sections

BOARD_ID = "905786612495800306"  # 90's Nostalgia board

for s in list_sections(BOARD_ID):
    print(s["id"], "-", s["name"])
