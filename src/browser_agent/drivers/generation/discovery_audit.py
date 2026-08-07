"""Independent post-run audit of a discovery script's link collection.

After the discovery self-check finishes, :class:`DiscoveryAuditor`
re-walks a SAMPLE of filter values using a correct-by-construction JS
selector and compares the independent count against the script's
collected count (parsed from self-check stdout) and the
``discovered_links`` rows in ``metadata.db``. Discrepancies trigger a
repair turn.

The audit is an INDEPENDENT ORACLE — it does not trust the script's
own ``UNDER-COLLECTED`` signal. Its selector is immune to both the
comma-scoping bug (``querySelectorAll`` is called on the scope element,
so all comma-parts are scoped) and the case-sensitivity bug (the CSS
``i`` flag). If the script's selector is buggy, the audit detects the
mismatch.

Best-effort: if the script's structure cannot be parsed (the agent
used a different shape than ``SUBCATEGORIES`` + ``DOC_SELECTOR``), the
audit logs a warning and skips — the lint rules still catch the bug
deterministically. The audit does NOT need an LLM; it is pure
deterministic code, making it fast and reliable.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

from browser_agent.ports.browser_session_port import BrowserSessionPort

# Correct-by-construction: case-insensitive, scoped via the element
# returned by querySelector(scope) so all comma-parts apply to the scope.
_AUDIT_JS = """
(() => {{
  const scope = document.querySelector({scope});
  if (!scope) return JSON.stringify({{error: 'scope not found', count: 0, hrefs: []}});
  const hrefs = Array.from(
    scope.querySelectorAll("a[href$='.pdf' i], a[href$='.doc' i], a[href$='.docx' i], a[href$='.rtf' i], a[href$='.xls' i], a[href$='.xlsx' i], a[href$='.ppt' i], a[href$='.pptx' i]")
  ).map(a => a.href);
  return JSON.stringify({{count: hrefs.length, hrefs: hrefs}});
}})()
"""

_SELECT_OPTIONS_JS = """
(() => {{
  const sel = document.querySelector('select');
  if (!sel) return [];
  return Array.from(sel.options).map(o => String(o.value || o.text).trim());
}})()
"""

_MIN_SAMPLE = 3
_MAX_SAMPLE = 10


def _sample_indices(n: int) -> list[int]:
    """Even-index selection including first and last; min 3, max 10."""
    if n <= 0:
        return []
    cap = min(max(_MIN_SAMPLE, n // 10), _MAX_SAMPLE, n)
    if cap <= 1:
        return [0]
    return [round(i * (n - 1) / (cap - 1)) for i in range(cap)]


class DiscoveryAuditor:
    """Re-walk a sample of filter values and compare against the script's counts."""

    def __init__(self, session: BrowserSessionPort, db_path: Path) -> None:
        self._session = session
        self._db_path = db_path

    async def audit(self, discovery_path: Path, self_check_stdout: str) -> str:
        """Return a discrepancy report (empty string if none found)."""
        source = discovery_path.read_text(encoding="utf-8")
        parsed = _parse_discovery_script(source)
        if parsed is None:
            logger.warning("discovery audit: could not parse script structure — skipping audit")
            return ""
        scope, doc_selector, subcategories, filter_param, select_selector = parsed
        if not subcategories:
            logger.warning("discovery audit: no subcategories parsed — skipping audit")
            return ""
        script_counts = _parse_script_counts(self_check_stdout)
        db_links = _load_db_links(self._db_path)
        report_lines: list[str] = []
        for base_url in subcategories:
            tab = await self._session.new_tab()
            try:
                tab_report = await self._audit_subcategory(
                    tab,
                    base_url,
                    scope,
                    doc_selector,
                    filter_param,
                    select_selector,
                    script_counts,
                    db_links,
                )
                if tab_report:
                    report_lines.append(tab_report)
            except Exception:
                logger.exception("discovery audit: subcategory {} failed", base_url)
            finally:
                await _close_tab_silently(tab)
        return "\n".join(report_lines)

    async def _audit_subcategory(
        self,
        tab: Any,
        base_url: str,
        scope: str,
        doc_selector: str,
        filter_param: str | None,
        select_selector: str,
        script_counts: dict[str, int],
        db_links: dict[str, set[str]],
    ) -> str:
        """Audit one subcategory; return a report block (empty if clean)."""
        await tab.get(base_url)
        await tab.sleep(1.0)
        filter_values = await _read_select_options(tab, select_selector)
        if not filter_values:
            logger.warning("discovery audit: no <select> options at {} — skipping", base_url)
            return ""
        sample = _sample_indices(len(filter_values))
        blocks: list[str] = []
        for idx in sample:
            value = filter_values[idx]
            label = _filter_label(base_url, value)
            url = _filter_url(base_url, value, filter_param)
            await tab.get(url)
            await tab.sleep(1.0)
            audit_hrefs, audit_count = await _independent_count(tab, scope)
            script_count = script_counts.get(label)
            db_count = len(db_links.get(label, set())) if label in db_links else None
            block = _compare(label, audit_count, audit_hrefs, script_count, db_count, db_links.get(label, set()))
            if block:
                blocks.append(block)
        if not blocks:
            return ""
        return f"script doc_selector: {doc_selector}\n" + "\n".join(blocks)


def _parse_discovery_script(source: str) -> tuple[str, str, list[str], str | None, str] | None:
    """Extract (scope, doc_selector, subcategories, filter_param, select_selector).

    Returns None when the structure cannot be parsed (best-effort skip).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    subcategories = _extract_subcategories(tree)
    scope, doc_selector = _extract_selectors(source, tree)
    filter_param = _extract_filter_param(source)
    select_selector = _extract_select_selector(tree) or "select"
    if scope is None or doc_selector is None:
        return None
    return scope, doc_selector, subcategories, filter_param, select_selector


def _extract_subcategories(tree: ast.Module) -> list[str]:
    """Find a module-level ``SUBCATEGORIES = [...]`` list of string literals."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "SUBCATEGORIES" and isinstance(node.value, ast.List):
                    urls: list[str] = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            urls.append(elt.value)
                    if urls:
                        return urls
    return []


def _extract_selectors(source: str, tree: ast.Module) -> tuple[str | None, str | None]:
    """Find the scope selector and doc selector from the source.

    Scope: a module-level constant assigned an id-like string (``#tabToday``,
    ``#rightmaincol``), named ``SCOPE`` or ``SCOPE_SELECTOR``.
    Doc selector: any string literal containing ``href$=``.
    """
    scope: str | None = None
    doc_selector: str | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            name = ""
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    name = tgt.id
            val = node.value.value
            if name in ("SCOPE", "SCOPE_SELECTOR", "SCOPE_SELECTOR_") and val.startswith("#"):
                scope = val
            if "href$=" in val and doc_selector is None:
                doc_selector = val
    if scope is None:
        scope_match = re.search(r'SCOPE(?:_SELECTOR)?\s*=\s*["\']#[A-Za-z0-9_-]+["\']', source)
        if scope_match:
            inner = re.search(r"#[A-Za-z0-9_-]+", scope_match.group(0))
            scope = inner.group(0) if inner else None
    return scope, doc_selector


def _extract_filter_param(source: str) -> str | None:
    """Find a ``?Year=`` / ``?year=`` / ``?param=`` pattern in the source."""
    match = re.search(r"[?&](\w+)=", source)
    return match.group(1) if match else None


def _extract_select_selector(tree: ast.Module) -> str | None:
    """Find a ``select`` selector constant (``SELECT_SELECTOR`` etc.)."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and "SELECT" in tgt.id and "select" in node.value.value.lower():
                    return node.value.value
    return None


def _parse_script_counts(stdout: str) -> dict[str, int]:
    """Parse ``{label} {year}: collected {N}`` lines from self-check stdout."""
    out: dict[str, int] = {}
    for match in re.finditer(r"([^\n:]*?):\s*collected\s+(\d+)", stdout):
        out[match.group(1).strip()] = int(match.group(2))
    return out


def _load_db_links(db_path: Path) -> dict[str, set[str]]:
    """Return ``{filter_label: set(url)}`` from the ``discovered_links`` table."""
    if not db_path.exists():
        return {}
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute("SELECT url, filter_label FROM discovered_links").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    out: dict[str, set[str]] = {}
    for url, label in rows:
        out.setdefault(label, set()).add(url)
    return out


async def _read_select_options(tab: Any, selector: str) -> list[str]:
    """Return the live ``<option>`` value/text strings from the DOM."""
    js = f"(() => {{const s = document.querySelector({json.dumps(selector)}); return s ? Array.from(s.options).map(o => String(o.value || o.text).trim()) : [];}})()"
    try:
        opts = await tab.evaluate(js)
    except Exception:
        return []
    return [str(o) for o in opts] if opts else []


async def _independent_count(tab: Any, scope: str) -> tuple[list[str], int]:
    """Run the correct-by-construction JS count; return (hrefs, count)."""
    js = _AUDIT_JS.format(scope=json.dumps(scope))
    try:
        raw = await tab.evaluate(js)
    except Exception:
        logger.exception("discovery audit: evaluate failed")
        return [], 0
    if not raw:
        return [], 0
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return [], 0
    if not isinstance(data, dict):
        return [], 0
    if data.get("error"):
        logger.warning("discovery audit: {}", data["error"])
        return [], 0
    hrefs = data.get("hrefs", [])
    return list(hrefs), int(data.get("count", 0))


def _filter_label(base_url: str, value: str) -> str:
    """Construct the filter label the script likely used."""
    return f"{base_url} {value}"


def _filter_url(base_url: str, value: str, filter_param: str | None) -> str:
    """Build the filter URL: ``base?{param}={value}`` (best-effort)."""
    sep = "&" if "?" in base_url else "?"
    param = filter_param or "Year"
    return f"{base_url}{sep}{param}={value}"


def _compare(
    label: str,
    audit_count: int,
    audit_hrefs: list[str],
    script_count: int | None,
    db_count: int | None,
    db_hrefs: set[str],
) -> str:
    """Return a discrepancy block for one filter value (empty if clean)."""
    audit_set = set(audit_hrefs)
    issues: list[str] = []
    if script_count is not None and script_count > audit_count:
        issues.append(f"OVER-COLLECTED: script collected {script_count}, audit found {audit_count}")
    if script_count is not None and script_count < audit_count:
        issues.append(f"UNDER-COLLECTED: script collected {script_count}, audit found {audit_count}")
    if db_count is not None and db_count > audit_count:
        issues.append(f"DB over-collection: {db_count} rows, audit found {audit_count}")
    if db_count is not None and db_count < audit_count:
        issues.append(f"DB under-collection: {db_count} rows, audit found {audit_count}")
    missing = audit_set - db_hrefs
    if missing and db_count is not None:
        issues.append(f"MISSING from DB ({len(missing)}): {sorted(missing)[:5]}")
    extras = db_hrefs - audit_set
    if extras and db_count is not None:
        issues.append(f"FALSE POSITIVES in DB ({len(extras)}): {sorted(extras)[:5]}")
    if not issues:
        return ""
    header = f"[{label}] audit_count={audit_count} script_count={script_count} db_count={db_count}"
    return header + "\n  " + "\n  ".join(issues)


async def _close_tab_silently(tab: Any) -> None:
    """Close ``tab`` if the zendriver API exposes it; swallow errors."""
    try:
        await tab.close()
    except Exception:
        pass
