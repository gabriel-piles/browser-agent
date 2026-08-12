"""Form-control helpers for emitted scripts.

Encapsulates the robust pattern for iterating many values through one
``<select>`` (rule 4a-bis of the system prompt), so the prompt states the
strategy and this module carries the implementation. Depends on
``script_tools.page_wait`` for the post-selection settle wait.
"""

from __future__ import annotations

import json

from script_tools.page_wait import wait_for_anchors, wait_for_page_ready


async def select_filter_value(tab, selector: str, value) -> bool:
    """Select ``value`` in the ``<select>`` at ``selector`` and settle the page.

    Robust against the five stacked failure modes of iterating many values
    through one ``<select>`` (rule 4a-bis):

      (i) re-reads the LIVE option list each call -- the page may re-render
          the ``<select>`` with a different subset after a previous selection,
          so a list captured before the loop goes stale. If the expected value
          is absent from the live list, that absence IS the answer -- return
          False so the caller logs "skip" and continues (do NOT report an error).
      (ii) waits for the post-selection page to settle via ``wait_for_page_ready``
           before returning, so the next iteration sees a hydrated DOM instead
           of a mid-navigation/mid-render ``s = null``.
      (iii) verifies the selection actually took effect by re-reading the
            ``<select>``'s ``.value`` / selected option text; a change event
            that was silently ignored would otherwise make the loop re-extract
            the previous page's rows.
      (iv) coerces ``value`` to a JSON-quoted string (``json.dumps(str(value))``)
           so the JS strict-equality match against ``option.value`` (always a
           string) works for both ``str`` and ``int`` inputs.

    Returns True iff ``value`` was selected AND the page is ready; False if
    the dropdown is absent, the option is missing from the live list, the
    selection did not take effect, or the settle wait timed out. The caller
    logs "skip (option absent or page not ready)" and continues on False.

    For ``<select>``s whose handler navigates to a replicable URL, prefer a
    direct ``await tab.get(f"{base}?{param}={value}")`` over driving the
    dropdown at all -- detect this once in exploration (click one option and
    check whether the URL changed); it eliminates the re-render race entirely.
    """
    value_str = str(value)
    try:
        if not await _await_dropdown(tab, selector):
            return False
        options = await _read_options(tab, selector)
        if not options or value_str not in options:
            return False
        if not await _do_select(tab, selector, json.dumps(value_str)):
            return False
        await tab.sleep(0.5)
        await wait_for_page_ready(tab)
        await tab.sleep(0.3)
        return await _confirm_value(tab, selector, value_str)
    except Exception as e:
        print(f"  [select_filter_value] {selector}={value_str} failed: {type(e).__name__}: {e}")
        return False


async def _await_dropdown(tab, selector: str) -> bool:
    """Wait for the ``<select>`` to be present; False on timeout (absent)."""
    try:
        await wait_for_anchors(tab, selector, timeout=8.0)
        return True
    except TimeoutError:
        return False


async def _read_options(tab, selector: str) -> list[str]:
    """Return the live ``<option>`` value/text strings of the ``<select>``."""
    js = (
        "(() => {const s = document.querySelector(" + json.dumps(selector) + ");"
        "return s ? Array.from(s.options).map(o => String(o.value || o.text).trim()) : [];})()"
    )
    try:
        opts = await tab.evaluate(js)
    except Exception:
        return []
    return [str(o) for o in opts] if opts else []


async def _do_select(tab, selector: str, safe_value: str) -> bool:
    """Set the matching option's value on the ``<select>`` and dispatch change."""
    js = (
        "(() => {const s = document.querySelector(" + json.dumps(selector) + ");"
        "if (!s) return false;"
        "const opt = Array.from(s.options).find(o => String(o.value || o.text).trim() === " + safe_value + ");"
        "if (!opt) return false;"
        "s.value = opt.value !== '' ? opt.value : opt.text;"
        "s.dispatchEvent(new Event('change', {bubbles: true}));"
        "if (s.form && typeof s.form.submit === 'function') {try {s.form.submit();} catch(e) {}}"
        "return true;})()"
    )
    try:
        return bool(await tab.evaluate(js))
    except Exception:
        return False


async def _confirm_value(tab, selector: str, value_str: str) -> bool:
    """Re-read the ``<select>``'s current value; True iff it matches ``value_str``."""
    js = (
        "(() => {const s = document.querySelector(" + json.dumps(selector) + ");"
        "if (!s) return null;"
        "const v = String(s.value || '').trim();"
        "if (v) return v;"
        "const idx = s.selectedIndex;"
        "return idx >= 0 ? String(s.options[idx].text || '').trim() : null;})()"
    )
    try:
        current = await tab.evaluate(js)
    except Exception:
        return False
    return str(current) == value_str
