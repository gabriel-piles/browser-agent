"""Declarative metadata + link extraction for emitted scripts.

The Processing Writer pastes the Explorer's ``FIELD_SPECS`` verbatim and
calls :func:`extract_fields` instead of hand-writing a metadata
``tab.evaluate`` IIFE. :func:`extract_links` replaces hand-written href
extraction. Stdlib-only; takes a zendriver ``tab`` duck-typed so no
``import zendriver`` is needed.
"""

from __future__ import annotations

import json
from urllib.parse import quote, urljoin


def _origin(url: str) -> str:
    """Return the scheme://host origin of ``url`` (empty on miss)."""
    if not url:
        return ""
    if "://" not in url:
        return ""
    try:
        scheme, rest = url.split("://", 1)
    except ValueError:
        return ""
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}" if host else ""


async def extract_fields(tab, specs: list[dict]) -> dict[str, str | list[str]]:
    """Read every spec in ONE ``tab.evaluate`` and return ``{field: value}``.

    Scalar sources (``text``/``attr``/``href``) read the first match;
    ``list_text``/``list_attr`` read all matches as a list. Returns ``{}``
    on any exception or empty result.
    """
    if not specs:
        return {}
    js = (
        "(() => {"
        "const specs = " + json.dumps(specs) + ";"
        "const out = {};"
        "for (const s of specs) {"
        "const els = Array.from(document.querySelectorAll(s.selector));"
        "const read = (el) => {"
        "if (s.source === 'attr' || s.source === 'list_attr') return (el.getAttribute(s.attr) || '').trim();"
        "if (s.source === 'href') return (el.getAttribute('href') || '').trim();"
        "return (el.textContent || '').trim();"
        "};"
        "if (s.source === 'list_text' || s.source === 'list_attr') {"
        "out[s.field] = els.map(read).filter(v => v);"
        "} else {"
        "out[s.field] = els.length ? read(els[0]) : '';"
        "}"
        "}"
        "return JSON.stringify(out);"
        "})()"
    )
    try:
        raw = await tab.evaluate(js)
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


async def extract_links(tab, selector: str, base_url: str = "") -> list[str]:
    """Return deduped absolute hrefs for ``selector`` via one evaluate."""
    js = (
        "JSON.stringify(Array.from(document.querySelectorAll("
        + json.dumps(selector)
        + ")).map(a=>a.getAttribute('href')||''))"
    )
    try:
        raw = await tab.evaluate(js)
    except Exception:
        return []
    if not raw:
        return []
    try:
        hrefs = json.loads(raw)
    except (TypeError, ValueError):
        return []
    base = base_url or _origin(getattr(tab, "url", "") or "")
    seen: set[str] = set()
    out: list[str] = []
    for href in hrefs:
        if not href:
            continue
        abs_url = urljoin(base, quote(href.strip(), safe="/%?=&"))
        if abs_url not in seen:
            seen.add(abs_url)
            out.append(abs_url)
    return out
