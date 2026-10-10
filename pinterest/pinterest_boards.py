"""
Maps a content category to where its pins should go.

Add a new entry any time you create a new board (or a new section
inside an existing board) on Pinterest. Keys are whatever category
strings your pipeline uses — match them to how you already think
about your content (by brand, by product line, whatever's natural).

board_section_id is optional — leave it None to pin straight to
the board with no section.
"""

from pinterest.pinterest_config import PINTEREST_ENV


BOARD_MAP = {
    "90s-nostalgia": {
        "board_id": (
            "905786612495800936"
            if PINTEREST_ENV == "sandbox"
            else "905786612495800306"
        ),
        "board_section_id": None,
    },
    "products-tagged": {
        "board_id": (
            "905786612495800937"
            if PINTEREST_ENV == "sandbox"
            else "905786612495800338"
        ),
        "board_section_id": None,
    },
    # Add more as you create boards, e.g.:
    # "word-search-books": {"board_id": "...", "board_section_id": None},
}


def resolve_board_name(name):
    """Return a configured board ID for a human-friendly board name."""
    normalized = "".join(character for character in name.casefold() if character.isalnum())
    aliases = {
        "90snostalgia": "90s-nostalgia",
        "90snostalgiasandbox": "90s-nostalgia",
    }
    category = aliases.get(normalized)
    if not category:
        return None
    return BOARD_MAP[category]["board_id"]


def resolve_board(category):
    """Returns (board_id, board_section_id) for a category, or raises
    a clear error if the category hasn't been mapped yet."""
    entry = BOARD_MAP.get(category)
    if not entry:
        known = ", ".join(BOARD_MAP.keys())
        raise ValueError(
            f"No board mapped for category '{category}'. "
            f"Known categories: {known}. Add it to BOARD_MAP in pinterest_boards.py."
        )
    return entry["board_id"], entry["board_section_id"]
