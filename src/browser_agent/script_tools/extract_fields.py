"""Declarative metadata + link extraction for emitted scripts.

The Processing Writer pastes the Explorer's ``FIELD_SPECS`` verbatim and
calls :func:`extract_fields` for a single-record page (or page-scope
fields) and :func:`extract_rows` for a multi-record page, instead of
hand-writing a metadata ``tab.evaluate`` IIFE. :func:`extract_links`
replaces hand-written href extraction. Each spec may carry an ordered
``transform`` list applied inside the single DOM read. Stdlib-only;
takes a zendriver ``tab`` duck-typed so no ``import zendriver`` is needed.
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


def _clean_js() -> str:
    """Return the shared JS ``clean(value, steps)`` source as one string."""
    return (
        "const clean = (v, steps) => {"
        "let out = v || '';"
        "for (const t of (steps || [])) {"
        "if (t === 'strip_parentheses') {"
        "let prev;"
        "do { prev = out; out = out.replace(/\\([^()]*\\)|（[^（）]*）/g, ' '); } while (out !== prev);"
        "out = out.replace(/\\s+/g, ' ');"
        "} else if (t === 'collapse_whitespace') {"
        "out = out.replace(/\\s+/g, ' ');"
        "}"
        "}"
        "return out.trim();"
        "};"
    )


async def extract_fields(tab, specs: list[dict]) -> dict[str, str | list[str]]:
    """Read every spec in ONE ``tab.evaluate`` and return ``{field: value}``.

    Scalar sources (``text``/``attr``/``href``) read the first match;
    ``list_text``/``list_attr`` read all matches as a list. Each spec may
    carry a ``transform`` list applied in order. Returns ``{}`` on any
    exception or empty result.
    """
    if not specs:
        return {}
    js = (
        "(() => {"
        "const specs = " + json.dumps(specs) + ";"
        "const out = {};" + _clean_js() + "for (const s of specs) {"
        "const els = Array.from(document.querySelectorAll(s.selector));"
        "const read = (el) => {"
        "const steps = s.transform || [];"
        "if (s.source === 'attr' || s.source === 'list_attr') return clean(el.getAttribute(s.attr) || '', steps);"
        "if (s.source === 'href') return clean(el.getAttribute('href') || '', steps);"
        "return clean(el.textContent || '', steps);"
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


async def extract_rows(tab, row_selector: str, cell_specs: list[dict], include_html: bool = False) -> list[dict]:
    """Return one dict per matching row.

    cell_specs: [{'field': str, 'selector': str, 'source':
    'text'|'attr'|'href'|'list_text'|'list_attr', 'attr': str,
    'transform': list[str]}]. Each cell selector is evaluated relative to
    its row element; list sources return every match within the row.
    Returns [] on error. When ``include_html`` is True, each record also
    carries ``core_source_html`` set to the row element's ``outerHTML``.
    """
    if not row_selector or not cell_specs:
        return []
    js = (
        "(() => {"
        "const rows = Array.from(document.querySelectorAll(" + json.dumps(row_selector) + "));"
        "const specs = " + json.dumps(cell_specs) + ";"
        "const includeHtml = " + json.dumps(bool(include_html)) + ";" + _clean_js() + "const out = [];"
        "for (const row of rows) {"
        "const rec = {};"
        "for (const s of specs) {"
        "const steps = s.transform || [];"
        "if (s.source === 'list_text' || s.source === 'list_attr') {"
        "rec[s.field] = Array.from(row.querySelectorAll(s.selector))"
        ".map(el => s.source === 'list_attr' ? clean(el.getAttribute(s.attr) || '', steps) : clean(el.textContent || '', steps))"
        ".filter(v => v);"
        "continue;"
        "}"
        "const el = row.querySelector(s.selector);"
        "if (!el) { rec[s.field] = ''; continue; }"
        "if (s.source === 'attr') rec[s.field] = clean(el.getAttribute(s.attr) || '', steps);"
        "else if (s.source === 'href') rec[s.field] = clean(el.getAttribute('href') || '', steps);"
        "else rec[s.field] = clean(el.textContent || '', steps);"
        "}"
        "if (includeHtml) rec['core_source_html'] = row.outerHTML;"
        "out.push(rec);"
        "}"
        "return JSON.stringify(out);"
        "})()"
    )
    try:
        raw = await tab.evaluate(js)
    except Exception:
        return []
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []
