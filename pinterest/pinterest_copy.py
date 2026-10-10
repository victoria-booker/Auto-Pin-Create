"""
Generates a Pinterest title + description for a listing using Claude.

pip install anthropic
Needs an API key from console.anthropic.com — set it as an
environment variable rather than hardcoding it:
    export ANTHROPIC_API_KEY=sk-ant-...
"""
import os

import anthropic

DEFAULT_BRAND_CONTEXT = (
    "Nostalgic, warm, a little playful — pop culture and pre-2010s "
    "childhood/millennial references. Written for someone scrolling "
    "Pinterest looking for a gift idea or something that hits them "
    "with a wave of nostalgia."
)


def generate_pin_copy(product_title, theme, brand_context=DEFAULT_BRAND_CONTEXT, extra_instructions=""):
    """
    product_title: what the listing is actually called
    theme:         short phrase describing the angle, e.g. "90s nostalgia gift for millennial moms"
    Returns: {"title": str, "description": str}
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Write a Pinterest pin title and description for this product.

Product: {product_title}
Theme/angle: {theme}
Brand voice: {brand_context}
{extra_instructions}

Rules:
- Title: under 100 characters, punchy, the kind of phrase someone would search for
- Description: 1-2 sentences, natural, not stuffed with hashtags
- No emojis in the title
- Reply in EXACTLY this format, nothing else:
TITLE: <title here>
DESCRIPTION: <description here>"""

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    title_line = next(line for line in text.splitlines() if line.startswith("TITLE:"))
    desc_line = next(line for line in text.splitlines() if line.startswith("DESCRIPTION:"))

    return {
        "title": title_line.removeprefix("TITLE:").strip(),
        "description": desc_line.removeprefix("DESCRIPTION:").strip(),
    }


if __name__ == "__main__":
    # quick manual test
    copy = generate_pin_copy(
        product_title="Word Search Puzzles for Millennial Moms",
        theme="90s nostalgia gift for a millennial mom",
    )
    print(copy)
