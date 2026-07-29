"""Safe DOM read/click helpers for emitted scripts.

Moved out of the system prompt so the prompt declares only signatures
and contracts, not full implementations (rule 0/2/4b). Stdlib-only —
the functions take a zendriver ``tab`` and element handle duck-typed,
so no ``import zendriver`` is needed.
"""

from __future__ import annotations

import asyncio
import json


async def get_text(el, tab=None) -> str:
    """Return the safe visible text for a zendriver element handle.

    Priority: (a) an authoritative attribute (``title`` / ``aria-label``),
    (b) full subtree ``textContent`` via ``el.apply`` (CDP), (c) the
    first text node via ``el.text`` (simple leaves only). Returns ``""``
    on any miss or exception.
    """
    if el is None:
        return ""
    for attr in ("title", "aria-label"):
        attrs = getattr(el, "attrs", None)
        if attrs and attrs.get(attr):
            return (attrs[attr] or "").strip()
    if tab is not None:
        try:
            val = await el.apply("(el) => (el.textContent || '').trim()")
            if isinstance(val, str) and val:
                return val.strip()
        except Exception:
            pass
    value = getattr(el, "text", None)
    if asyncio.iscoroutine(value):
        value = await value
    return (value or "").strip()


async def get_attr(el, name: str) -> str:
    """Return a stripped attribute value from a zendriver element handle.

    Tries the sync ``el.attrs`` dict first, then the ``el.get_attribute``
    method (which may be sync or async). Returns ``""`` on any miss.
    """
    if el is None:
        return ""
    attrs = getattr(el, "attrs", None)
    if attrs and name in attrs:
        return (attrs[name] or "").strip()
    getter = getattr(el, "get_attribute", None)
    if getter is not None:
        value = getter(name)
        if value is None:
            return ""
        if asyncio.iscoroutine(value):
            value = await value
        return (value or "").strip()
    return ""


async def trusted_click(tab, selector: str) -> bool:
    """Click ``selector`` via a trusted CDP mouse event at its center.

    ``element.click()`` dispatches an untrusted JS event (``isTrusted:
    false``) that some sites ignore on load-more controls. This helper
    finds the element, scrolls it into view, and reads its on-screen
    center — all in a SINGLE ``tab.evaluate`` call so the element
    cannot go stale between the find and the coordinate read. It then
    fires ``tab.mouse_click(cx, cy)`` (trusted CDP mouse events).
    Returns ``True`` on success, ``False`` if the element is absent,
    hidden (zero-size rect), or the click raised.
    """
    try:
        js = (
            "(() => {"
            "const el = document.querySelector(" + json.dumps(selector) + ");"
            "if (!el) return null;"
            "el.scrollIntoView({block: 'center'});"
            "const r = el.getBoundingClientRect();"
            "if (r.width === 0 || r.height === 0) return null;"
            "return [r.left + r.width/2, r.top + r.height/2];"
            "})()"
        )
        result = await tab.evaluate(js)
        if result is None:
            return False
        cx, cy = result
        await tab.mouse_click(cx, cy)
    except Exception as e:
        print(f"  [trusted_click] {selector} failed: {type(e).__name__}: {e}")
        return False
    return True
