"""Parse raw HTML into a structured :class:`PageStructure` for the agent.

Extracted from :mod:`zendriver_browser_session` so the session class
stays focused on browser driving. The analyzer is pure: it takes raw
HTML + URL + title and returns a :class:`PageStructure` with links,
buttons, inputs, headings, tables, pagination, and filters.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

from browser_agent.configuration import (
    ANALYZE_MAX_BUTTONS,
    ANALYZE_MAX_HEADINGS,
    ANALYZE_MAX_INPUTS,
    ANALYZE_MAX_LINKS,
    ANALYZE_MAX_TABLES,
)
from browser_agent.domain.element_info import ElementInfo
from browser_agent.domain.page_structure import PageStructure

_PAGINATION_TEXTS = {"next", "prev", "previous", "page", "more", "»", "«", ">>", "<<"}


class PageAnalyzer:
    """Parse raw HTML into a :class:`PageStructure` for the explore_page tool."""

    def analyze(self, raw_html: str, url: str, title: str) -> PageStructure:
        """Parse ``raw_html`` and build a structured :class:`PageStructure`."""
        soup = BeautifulSoup(raw_html or "", "html.parser")
        links = self._extract_links(soup, ANALYZE_MAX_LINKS)
        buttons = self._extract_buttons(soup, ANALYZE_MAX_BUTTONS)
        inputs = self._extract_inputs(soup, ANALYZE_MAX_INPUTS)
        headings = self._extract_headings(soup, ANALYZE_MAX_HEADINGS)
        tables = self._extract_tables(soup, ANALYZE_MAX_TABLES)
        pagination, filters = self._classify_interactive(soup, links, buttons)
        return PageStructure(
            url=url,
            title=title,
            links=links,
            buttons=buttons,
            inputs=inputs,
            headings=headings,
            tables=tables,
            pagination=pagination,
            filters=filters,
        )

    def _classify_interactive(self, soup, links, buttons) -> tuple[list[ElementInfo], list[ElementInfo]]:
        """Classify links and buttons as pagination / filter elements."""
        pagination: list[ElementInfo] = []
        filters: list[ElementInfo] = []
        for info in links:
            a_tag = soup.find("a", href=info.href) if info.href else None
            if a_tag and self._is_pagination(a_tag):
                pagination.append(info)
        for info in buttons:
            btn = soup.find(info.tag, text=info.text) if info.tag != "input" else None
            if btn and (self._is_pagination(btn) or self._is_filter(btn)):
                (pagination if self._is_pagination(btn) else filters).append(info)
        for el in soup.find_all(["select", "input"])[:ANALYZE_MAX_INPUTS]:
            if self._is_filter(el):
                filters.append(self._filter_element(el))
        return pagination, filters

    def _filter_element(self, el) -> ElementInfo:
        """Build an :class:`ElementInfo` for a filter/select element."""
        return ElementInfo(
            tag=el.name or "select",
            text=self._get_text(el),
            selector=self._build_selector(el.name or "select", el.attrs),
            extra={"type": el.get("type", "") or "select", "name": el.get("name", "") or ""},
        )

    def _extract_links(self, soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
        """Extract <a href=...> elements, skipping anchors and javascript:."""
        results: list[ElementInfo] = []
        for a in soup.find_all("a", href=True)[:limit]:
            href = a["href"]
            if href.startswith("#") or href.startswith("javascript:"):
                continue
            results.append(
                ElementInfo(tag="a", text=self._get_text(a), href=href, selector=self._build_selector("a", a.attrs))
            )
        return results

    def _extract_buttons(self, soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
        """Extract <button> and input[type=submit/button] elements."""
        results: list[ElementInfo] = []
        for btn in soup.find_all(["button", "input"])[:limit]:
            tag = btn.name or "button"
            if tag == "input" and btn.get("type", "").lower() not in ("submit", "button", ""):
                continue
            text = self._get_text(btn) or (btn.get("value") or "")
            extra: dict[str, str] = {}
            if tag == "input":
                extra["type"] = str(btn.get("type") or "submit")
            results.append(ElementInfo(tag=tag, text=text, selector=self._build_selector(tag, btn.attrs), extra=extra))
        return results

    def _extract_inputs(self, soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
        """Extract <input>, <select>, <textarea> form elements."""
        results: list[ElementInfo] = []
        for el in soup.find_all(["input", "select", "textarea"])[:limit]:
            tag = el.name or "input"
            if tag == "input" and el.get("type", "").lower() in ("submit", "button", "hidden"):
                continue
            extra = self._input_extra(el)
            results.append(
                ElementInfo(tag=tag, text=self._get_text(el), selector=self._build_selector(tag, el.attrs), extra=extra)
            )
        return results

    @staticmethod
    def _input_extra(el) -> dict[str, str]:
        """Build the extra-attrs dict for an input element."""
        extra: dict[str, str] = {}
        el_type = el.get("type")
        if el_type:
            extra["type"] = str(el_type)
        el_name = el.get("name")
        if el_name:
            extra["name"] = str(el_name)
        return extra

    def _extract_headings(self, soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
        """Extract <h1> through <h6> elements."""
        results: list[ElementInfo] = []
        for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])[:limit]:
            results.append(
                ElementInfo(
                    tag=h.name or "h",
                    text=self._get_text(h),
                    selector=self._build_selector(h.name or "h", h.attrs),
                    extra={"level": (h.name or "h")[1]},
                )
            )
        return results

    def _extract_tables(self, soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
        """Extract <table> elements with row count and column headers."""
        results: list[ElementInfo] = []
        for tbl in soup.find_all("table")[:limit]:
            rows = len(tbl.find_all("tr"))
            headers = [th.get_text(strip=True) for th in tbl.find_all("th")[:10]]
            col_desc = ", ".join(headers) if headers else f"{rows} rows"
            results.append(
                ElementInfo(
                    tag="table",
                    text=col_desc,
                    selector=self._build_selector("table", tbl.attrs),
                    extra={"rows": str(rows), "columns": (", ".join(headers)) if headers else ""},
                )
            )
        return results

    @staticmethod
    def _build_selector(tag_name: str, attrs: dict[str, Any]) -> str:
        """Build a simple CSS selector from tag + attributes (BS4 attrs)."""
        raw_id = attrs.get("id") or ""
        el_id: str = raw_id if isinstance(raw_id, str) else ""
        if el_id:
            return f"{tag_name}#{el_id}"
        raw_class = attrs.get("class")
        if raw_class:
            classes = raw_class if isinstance(raw_class, list) else raw_class.split()
            if classes:
                return f"{tag_name}.{'.'.join(classes[:2])}"
        raw_name = attrs.get("name")
        el_name: str = raw_name if isinstance(raw_name, str) else ""
        if el_name:
            return f"{tag_name}[name={el_name}]"
        return tag_name

    @staticmethod
    def _get_text(el: Tag, max_len: int = 200) -> str:
        """Get stripped text from a BeautifulSoup tag, truncated."""
        return (el.get_text(strip=True) or "")[:max_len]

    @staticmethod
    def _is_pagination(el: Tag) -> bool:
        """Heuristic: text, class, or href contains pagination keywords."""
        text = (el.get_text(strip=True) or "").lower()
        if text in _PAGINATION_TEXTS or any(t in text for t in ("page", "next", "prev")):
            return True
        for attr in ("class", "id"):
            raw = el.get(attr)
            if raw is None:
                continue
            val = " ".join(raw) if isinstance(raw, list) else str(raw)
            if any(t in val.lower() for t in ("page", "pagination", "pager", "next", "prev")):
                return True
        href = el.get("href", "")
        if isinstance(href, str) and ("page=" in href or "?page" in href or "&page" in href):
            return True
        return False

    @staticmethod
    def _is_filter(el: Tag) -> bool:
        """Heuristic: select, checkbox, or class/text contains filter keywords."""
        if el.name == "select":
            return True
        if el.name == "input" and el.get("type", "").lower() in ("checkbox", "radio"):
            return True
        for attr in ("class", "id"):
            raw = el.get(attr)
            if raw is None:
                continue
            val = " ".join(raw) if isinstance(raw, list) else str(raw)
            if "filter" in val.lower() or "sort" in val.lower():
                return True
        text = (el.get_text(strip=True) or "").lower()
        return text in ("filter", "sort", "filter by", "sort by")
