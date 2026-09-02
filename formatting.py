from html import escape
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML


class TelegramHTMLRenderer(RendererHTML):
    """Render CommonMark using only tags accepted by Telegram Bot API."""

    def text(self, tokens: list[Any], idx: int, options: dict, env: dict) -> str:
        return escape(tokens[idx].content, quote=False)

    def paragraph_open(self, *args: Any) -> str:
        return ""

    def paragraph_close(self, *args: Any) -> str:
        return "\n\n"

    def heading_open(self, *args: Any) -> str:
        return "<b>"

    def heading_close(self, *args: Any) -> str:
        return "</b>\n"

    def strong_open(self, *args: Any) -> str:
        return "<b>"

    def strong_close(self, *args: Any) -> str:
        return "</b>"

    def em_open(self, *args: Any) -> str:
        return "<i>"

    def em_close(self, *args: Any) -> str:
        return "</i>"

    def s_open(self, *args: Any) -> str:
        return "<s>"

    def s_close(self, *args: Any) -> str:
        return "</s>"

    def blockquote_open(self, *args: Any) -> str:
        return "<blockquote expandable>"

    def blockquote_close(self, *args: Any) -> str:
        return "</blockquote>\n"

    def bullet_list_open(self, *args: Any) -> str:
        return ""

    def bullet_list_close(self, *args: Any) -> str:
        return "\n"

    def ordered_list_open(self, *args: Any) -> str:
        return ""

    def ordered_list_close(self, *args: Any) -> str:
        return "\n"

    def list_item_open(self, *args: Any) -> str:
        return "• "

    def list_item_close(self, *args: Any) -> str:
        return "\n"

    def code_inline(
        self, tokens: list[Any], idx: int, options: dict, env: dict
    ) -> str:
        return f"<code>{escape(tokens[idx].content, quote=False)}</code>"

    def fence(
        self, tokens: list[Any], idx: int, options: dict, env: dict
    ) -> str:
        token = tokens[idx]
        language = token.info.strip().split(maxsplit=1)[0] if token.info else ""
        class_name = f' class="language-{escape(language)}"' if language else ""
        return f"<pre><code{class_name}>{escape(token.content, quote=False)}</code></pre>\n"

    def code_block(
        self, tokens: list[Any], idx: int, options: dict, env: dict
    ) -> str:
        return f"<pre>{escape(tokens[idx].content, quote=False)}</pre>\n"

    def image(
        self, tokens: list[Any], idx: int, options: dict, env: dict
    ) -> str:
        token = tokens[idx]
        url = escape(token.attrGet("src") or "", quote=True)
        alt = escape(token.content or "изображение", quote=False)
        return f'<a href="{url}">🖼 {alt}</a>'

    def link_open(
        self, tokens: list[Any], idx: int, options: dict, env: dict
    ) -> str:
        url = escape(tokens[idx].attrGet("href") or "", quote=True)
        return f'<a href="{url}">'

    def softbreak(self, *args: Any) -> str:
        return "\n"

    def hardbreak(self, *args: Any) -> str:
        return "\n"

    def hr(self, *args: Any) -> str:
        return "────────\n"

    def html_inline(
        self, tokens: list[Any], idx: int, options: dict, env: dict
    ) -> str:
        return escape(tokens[idx].content, quote=False)

    def html_block(
        self, tokens: list[Any], idx: int, options: dict, env: dict
    ) -> str:
        return escape(tokens[idx].content, quote=False)


_markdown = MarkdownIt("commonmark", renderer_cls=TelegramHTMLRenderer)


def render_markdown(text: str) -> str:
    return _markdown.render(text).strip()


def split_markdown(text: str, limit: int = 3500) -> list[str]:
    """Split primarily between lines; keep fenced code valid across chunks."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    fence = ""

    for line in text.splitlines(keepends=True):
        only_open_fence = (
            fence and len(current) == 1 and current[0].lstrip().startswith("```")
        )
        if size + len(line) > limit and current and not only_open_fence:
            if fence:
                current.append("```\n")
            chunks.append("".join(current))
            current = [f"```{fence}\n"] if fence else []
            size = sum(map(len, current))

        while size + len(line) > limit:
            closing = 4 if fence else 0
            room = max(1, limit - size - closing)
            current.append(line[:room])
            if fence:
                current.append("```\n")
            chunks.append("".join(current))
            current = [f"```{fence}\n"] if fence else []
            size = sum(map(len, current))
            line = line[room:]

        current.append(line)
        size += len(line)
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fence = "" if fence else stripped[3:].strip()

    if current:
        chunks.append("".join(current))
    return chunks
