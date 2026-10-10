"""
The actual "new listing -> pin" function. This is what you call
once per listing.
"""
from pinterest.pinterest_boards import resolve_board
from pinterest.pinterest_copy import generate_pin_copy
from pinterest.pinterest_pins import create_pin


def create_pin_for_listing(product_title, theme, category, link, image_url, title=None, description=None):
    """
    product_title: what the listing is called
    theme:         short phrase describing the angle (used for AI copy, ignored if title/description given)
    category:      must match a key in pinterest_boards.BOARD_MAP
    link:          the Etsy/Amazon/shop URL the pin should point to
    image_url:     must be a public URL Pinterest can fetch (e.g. your Shopify CDN
                   or a ChatGPT temp URL used immediately, not held onto)
    title/description: pass these directly to skip AI generation entirely
    """
    board_id, board_section_id = resolve_board(category)

    if title is None or description is None:
        copy = generate_pin_copy(product_title, theme)
        title = title or copy["title"]
        description = description or copy["description"]

    return create_pin(
        board_id=board_id,
        title=title,
        description=description,
        link=link,
        image_url=image_url,
        board_section_id=board_section_id,
    )


if __name__ == "__main__":
    # quick manual test — remember pins are still sandboxed until Standard access is approved
    result = create_pin_for_listing(
        product_title="Word Search Puzzles for Millennial Moms",
        theme="90s nostalgia gift for a millennial mom",
        category="90s-nostalgia",
        link="https://wittynostalgia.com",
        image_url="https://wittynostalgia.com/cdn/shop/files/witty-nostalgia-hero-banner.png",
    )
    print("Pin created:", result.get("id"))
