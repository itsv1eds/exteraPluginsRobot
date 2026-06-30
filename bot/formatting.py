from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Iterable


_ALLOWED_SIMPLE_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "pre",
    "tg-spoiler",
}
_ALLOWED_URI_PREFIXES = ("http://", "https://", "tg://", "mailto:")


class _TelegramHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {k.lower(): v for k, v in attrs}

        if tag in _ALLOWED_SIMPLE_TAGS:
            if tag == "code":
                class_name = attrs_map.get("class") or ""
                if class_name.startswith("language-"):
                    self.parts.append(f'<code class="{html.escape(class_name, quote=True)}">')
                else:
                    self.parts.append("<code>")
            else:
                self.parts.append(f"<{tag}>")
            self.stack.append(tag)
            return

        if tag == "span" and attrs_map.get("class") == "tg-spoiler":
            self.parts.append('<span class="tg-spoiler">')
            self.stack.append(tag)
            return

        if tag == "a":
            href = (attrs_map.get("href") or "").strip()
            if href.startswith(_ALLOWED_URI_PREFIXES):
                self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
                self.stack.append(tag)
            return

        if tag == "blockquote":
            expandable = " expandable" if "expandable" in attrs_map else ""
            self.parts.append(f"<blockquote{expandable}>")
            self.stack.append(tag)
            return

        if tag == "tg-emoji":
            emoji_id = (attrs_map.get("emoji-id") or "").strip()
            if emoji_id.isdigit():
                self.parts.append(f'<tg-emoji emoji-id="{emoji_id}">')
                self.stack.append(tag)
            return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag not in self.stack:
            return
        while self.stack:
            opened = self.stack.pop()
            self.parts.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")

    def close_open_tags(self) -> None:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")


def _norm(value: object) -> str:
    return str(value or "").replace("\\n", "\n").strip()


def plain_html(value: object) -> str:
    return html.escape(_norm(value), quote=False)


def telegram_html(value: object) -> str:
    text = html.unescape(_norm(value))
    if not text:
        return ""
    parser = _TelegramHTMLSanitizer()
    parser.feed(text)
    parser.close_open_tags()
    return "".join(parser.parts).strip()


def strip_blockquote_tags(value: str) -> str:
    return re.sub(r"</?blockquote(?:\s+expandable)?>", "\n", value, flags=re.IGNORECASE).strip()


def quote_html(value: object, *, expandable: bool = False) -> str:
    body = strip_blockquote_tags(telegram_html(value))
    attr = " expandable" if expandable else ""
    return f"<blockquote{attr}>{body or '—'}</blockquote>"


def code_html(value: object) -> str:
    return f"<code>{plain_html(value)}</code>"


def user_mention(user_id: object, username: object = None) -> str:
    uid = str(user_id or "").strip()
    handle = str(username or "").strip().lstrip("@")
    label = f"@{plain_html(handle)}" if handle else (plain_html(uid) or "—")
    if uid.lstrip("-").isdigit():
        return f'<a href="tg://user?id={uid}">{label}</a>'
    return label


def join_plain(values: Iterable[object], sep: str = " | ") -> str:
    return sep.join(plain_html(value) for value in values if str(value or "").strip())
