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
_ALLOWED_URI_PREFIXES = ("http://", "https://", "tg://", "mailto:", "tel:")

_RICH_SIMPLE_TAGS = {
    "mark", "sub", "sup", "p", "footer", "cite", "aside", "summary",
    "figure", "figcaption", "ul", "ol", "li", "details",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "tg-collage", "tg-slideshow", "tg-math",
}
_RICH_VOID_TAGS = {"br", "hr", "img", "video", "audio", "tg-map", "input"}
_RICH_TAG_ATTRS = {
    "ol": ("start", "type", "reversed"),
    "li": ("value", "type"),
    "details": ("open",),
    "img": ("src", "alt", "tg-spoiler"),
    "video": ("src", "tg-spoiler"),
    "audio": ("src",),
    "tg-map": ("lat", "long", "zoom"),
    "tg-time": ("unix", "format"),
    "tg-reference": ("name",),
    "input": ("type", "checked"),
}
_RICH_SRC_PREFIXES = ("http://", "https://", "tg://")


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


class _RichHTMLSanitizer(_TelegramHTMLSanitizer):

    def _rich_attrs(self, tag: str, attrs_map: dict) -> str:
        out = []
        for name in _RICH_TAG_ATTRS.get(tag, ()):
            if name not in attrs_map:
                continue
            value = attrs_map[name]
            if name == "src" and not str(value or "").startswith(_RICH_SRC_PREFIXES):
                return ""
            if value is None:
                out.append(f" {name}")
            else:
                out.append(f' {name}="{html.escape(str(value), quote=True)}"')
        return "".join(out)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {k.lower(): v for k, v in attrs}

        if tag in _RICH_VOID_TAGS:
            rendered = self._rich_attrs(tag, attrs_map)
            if tag in ("img", "video", "audio") and "src" not in attrs_map:
                return
            if tag in ("img", "video", "audio") and not rendered:
                return
            self.parts.append(f"<{tag}{rendered}/>")
            return

        if tag in _RICH_SIMPLE_TAGS:
            self.parts.append(f"<{tag}{self._rich_attrs(tag, attrs_map)}>")
            self.stack.append(tag)
            return

        if tag in ("tg-reference", "tg-time"):
            self.parts.append(f"<{tag}{self._rich_attrs(tag, attrs_map)}>")
            self.stack.append(tag)
            return

        if tag == "a" and "name" in attrs_map and "href" not in attrs_map:
            self.parts.append(f'<a name="{html.escape(str(attrs_map["name"]), quote=True)}">')
            self.stack.append(tag)
            return

        super().handle_starttag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _RICH_VOID_TAGS:
            self.handle_starttag(tag, attrs)
            return
        super().handle_startendtag(tag, attrs)


def rich_html(value: object) -> str:
    text = html.unescape(_norm(value))
    if not text:
        return ""
    parser = _RichHTMLSanitizer()
    parser.feed(text)
    parser.close_open_tags()
    return "".join(parser.parts).strip()


def telegram_html(value: object) -> str:
    text = html.unescape(_norm(value))
    if not text:
        return ""
    parser = _TelegramHTMLSanitizer()
    parser.feed(text)
    parser.close_open_tags()
    return "".join(parser.parts).strip()


def strip_blockquote_tags(value: str) -> str:
    return re.sub(r"</?blockquote\b[^>]*>", "\n", value, flags=re.IGNORECASE).strip()


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


_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9-]+)((?:\s[^>]*)?)/?>")
_VOID_TAGS = {"br", "hr", "img", "video", "audio", "tg-map", "input"}


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _open_tag_source(name: str, attrs: str) -> str:
    return f"<{name}{attrs}>"


def split_html(text: str, limit: int) -> list[str]:
    text = str(text or "")
    if utf16_length(text) <= limit:
        return [text] if text else []

    parts: list[str] = []
    stack: list[tuple[str, str]] = []
    chunk: list[str] = []
    chunk_len = 0
    break_at: int | None = None
    break_stack: list[tuple[str, str]] = []

    def opened_prefix(items: list[tuple[str, str]]) -> str:
        return "".join(_open_tag_source(n, a) for n, a in items)

    def closing_suffix(items: list[tuple[str, str]]) -> str:
        return "".join(f"</{n}>" for n, _ in reversed(items))

    def flush() -> None:
        nonlocal chunk, chunk_len, break_at, break_stack
        body = "".join(chunk)
        if break_at is not None and 0 < break_at < len(body):
            head, tail, at_cut = body[:break_at], body[break_at:], break_stack
        else:
            head, tail, at_cut = body, "", stack
        parts.append(head + closing_suffix(at_cut))
        carry = opened_prefix(at_cut) + tail.lstrip("\n")
        chunk = [carry]
        chunk_len = utf16_length(re.sub(r"<[^>]+>", "", carry))
        break_at = None
        break_stack = []

    index = 0
    while index < len(text):
        match = _TAG_RE.match(text, index)
        token = match.group(0) if match else text[index]
        token_len = 0 if match else utf16_length(token)

        if chunk_len + token_len > limit and chunk:
            flush()

        chunk.append(token)
        chunk_len += token_len

        if match:
            closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3)
            if not closing and name not in _VOID_TAGS:
                stack.append((name, attrs))
            elif closing:
                for position in range(len(stack) - 1, -1, -1):
                    if stack[position][0] == name:
                        del stack[position:]
                        break
            index = match.end()
            continue

        if token in "\n ":
            break_at = sum(len(piece) for piece in chunk)
            break_stack = list(stack)
        index += 1

    tail = "".join(chunk)
    if re.sub(r"<[^>]+>", "", tail).strip():
        parts.append(tail + closing_suffix(stack))
    return parts
