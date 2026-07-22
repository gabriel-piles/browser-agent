from __future__ import annotations

from bs4 import BeautifulSoup, Comment, Tag
from pydantic import BaseModel

from browser_agent.configuration import SNAPSHOT_MAX_CHARS


class HtmlSnippet(BaseModel):
    """A token-optimised, structural view of a web page.

    Holds the original ``url``, the cleaned HTML body and a short
    human-readable ``summary`` line. The cleaning rules live on this
    object so callers never have to remember which tags to strip.
    """

    url: str
    cleaned_html: str
    summary: str = ""

    # Tags that carry no structural information about the page and
    # exist only to render or animate things the agent does not need
    # to see. The model needs DOM structure + text + attributes.
    _NON_STRUCTURAL_TAGS: tuple[str, ...] = (
        "script",
        "style",
        "svg",
        "path",
        "noscript",
        "iframe",
        "template",
        "link",
    )

    @classmethod
    def from_raw(cls, url: str, raw_html: str, max_chars: int = SNAPSHOT_MAX_CHARS) -> "HtmlSnippet":
        """Build a snippet from a raw HTML blob.

        Runs the stripping pipeline (drop non-structural tags, collapse
        whitespace, prune empty nodes) while **preserving DOM structure**
        — tags, classes and attributes — so callers can read real CSS
        selectors and pagination hrefs from the output. Truncates the
        result so a single tool call never blows the context window.
        """
        cleaned = cls._clean(raw_html)
        truncated, was_truncated = cls._truncate(cleaned, max_chars)
        suffix = "" if not was_truncated else f"\n... (truncated, total={len(cleaned)} chars)"
        summary = cls._summarise(url, raw_html, len(cleaned), max_chars, was_truncated)
        return cls(url=url, cleaned_html=truncated + suffix, summary=summary)

    @classmethod
    def _clean(cls, raw_html: str) -> str:
        soup = BeautifulSoup(raw_html or "", "html.parser")
        cls._strip_non_structural(soup)
        cls._strip_comments(soup)
        cls._normalise_whitespace(soup)
        cls._prune_empty(soup)
        body = soup.body or soup
        return body.decode_contents()

    @staticmethod
    def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
        """Cut at a tag boundary before ``max_chars``.

        A hard character cut can split a tag mid-attribute, leaving
        the agent with malformed HTML and broken selectors.  We try
        to cut after the last complete tag (``>``), or before the
        last incomplete tag (``<``) if no closing ``>`` is found.
        """
        if len(text) <= max_chars:
            return text, False
        cut = text.rfind(">", 0, max_chars)
        if cut > 0:
            return text[: cut + 1], True
        cut = text.rfind("<", 0, max_chars)
        if cut > 0:
            return text[:cut], True
        return text[:max_chars], True

    @classmethod
    def _strip_non_structural(cls, soup: BeautifulSoup) -> None:
        """Remove tags that exist only to render or animate."""
        for tag in soup(cls._NON_STRUCTURAL_TAGS):
            tag.decompose()

    @classmethod
    def _strip_comments(cls, soup: BeautifulSoup) -> None:
        """Drop HTML comments and processing instructions."""
        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()

    @classmethod
    def _normalise_whitespace(cls, soup: BeautifulSoup) -> None:
        """Collapse runs of whitespace inside every text node."""
        for text in soup.find_all(string=True):
            collapsed = " ".join(text.split())
            text.replace_with(collapsed)

    @classmethod
    def _prune_empty(cls, soup: BeautifulSoup) -> None:
        """Remove leaf tags with no text, no children and no attributes.

        Iterates bottom-up so a parent whose children all became empty
        is pruned in the same pass.
        """
        for tag in reversed(soup.find_all()):
            has_text = bool(tag.get_text(strip=True))
            has_children = any(isinstance(c, Tag) for c in tag.contents)
            has_attrs = bool(tag.attrs)
            if not has_text and not has_children and not has_attrs:
                tag.decompose()

    @staticmethod
    def _summarise(url: str, raw: str, cleaned_chars: int, max_chars: int, was_truncated: bool) -> str:
        raw_chars = len(raw or "")
        flags = "truncated" if was_truncated else "complete"
        return f"{url} — {cleaned_chars} cleaned chars (raw {raw_chars}, {flags}, cap {max_chars})"

    def non_empty(self) -> bool:
        return bool(self.cleaned_html and self.cleaned_html.strip())
