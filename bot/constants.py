PAGE_SIZE = 5

EMOJI_MESSAGE = (
    """
<tg-emoji emoji-id="5319016550248751722">👋</tg-emoji>
<tg-emoji emoji-id="547444633574">👍</tg-emoji>
""".strip()
)

EMOJI_TEXT = "👋"
CUSTOM_EMOJI_ID = "5319016550248751722"


def utf16_length(text: str) -> int:
    """Return the UTF-16 code unit length for Telegram entity offsets."""
    return len(text.encode("utf-16-le")) // 2
