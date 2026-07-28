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
    scrolls the element into view, computes its on-screen center via a
    null-guarded IIFE, and fires ``tab.mouse_click(cx, cy)`` (trusted CDP
    mouse events). Returns ``True`` on success, ``False`` if the element
    is absent or the click raised.
    """
    try:
        el = await tab.query_selector(selector)
        if el is None:
            return False
        await el.scroll_into_view()
        await tab.sleep(0.5)
        js = (
            "(()=>{const r=document.querySelector(" + json.dumps(selector) + ").getBoundingClientRect();"
            "return [r.left+r.width/2, r.top+r.height/2];})()"
        )
        cx, cy = await tab.evaluate(js)
        await tab.mouse_click(cx, cy)
    except Exception:
        return False
    return True
