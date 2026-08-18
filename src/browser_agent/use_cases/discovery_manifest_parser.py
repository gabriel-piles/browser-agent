"""Parse a discovery script's manifest and uniform stdout protocol.

Pure helpers used by both the self-check verifier and the independent
audit. ``extract_manifest`` reads the module-level ``DISCOVERY_MANIFEST``
dict literal from the script source; ``parse_discovery_stdout`` decodes
the ``DISCOVERY target=... found=... saved=...`` / ``DISCOVERY total_saved=...``
lines; ``enumerate_listing_targets`` walks a listing page to build the
target list (used by the audit's independent coverage check).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from pydantic import ValidationError

from browser_agent.domain.discovery_manifest import DiscoveryManifest
from browser_agent.domain.discovery_target import DiscoveryTarget
from browser_agent.domain.listing_targets import ListingTargets

_TARGET_RE = re.compile(r"^DISCOVERY target=(.*?) found=(\d+) saved=(\d+)$")
_TOTAL_RE = re.compile(r"^DISCOVERY total_saved=(\d+)$")


@dataclass
class ManifestExtractResult:
    """Outcome of parsing ``DISCOVERY_MANIFEST`` — carries the reason on failure."""

    manifest: DiscoveryManifest | None
    error: str | None


def extract_manifest(source: str) -> DiscoveryManifest | None:
    """Extract and validate ``DISCOVERY_MANIFEST`` from script source.

    Thin wrapper over :func:`extract_manifest_detailed` returning only the
    manifest (``None`` on any failure). Kept for callers that only need the
    value, e.g. :class:`DiscoveryAuditor`.
    """
    return extract_manifest_detailed(source).manifest


def extract_manifest_detailed(source: str) -> ManifestExtractResult:
    """Extract and validate ``DISCOVERY_MANIFEST``, returning the failure reason.

    Returns a :class:`ManifestExtractResult` whose ``error`` field carries an
    actionable message when the manifest is absent, not a dict literal, not
    literal-evaluable (e.g. references a module constant), or fails pydantic
    validation.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ManifestExtractResult(None, f"DISCOVERY_MANIFEST source is not parseable: {exc}")
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == "DISCOVERY_MANIFEST":
                if not isinstance(node.value, ast.Dict):
                    return ManifestExtractResult(
                        None,
                        f"DISCOVERY_MANIFEST is not a dict literal (got {type(node.value).__name__})",
                    )
                try:
                    data = ast.literal_eval(node.value)
                except (ValueError, SyntaxError) as exc:
                    name_node = next((n for n in ast.walk(node.value) if isinstance(n, ast.Name)), None)
                    if name_node is not None:
                        return ManifestExtractResult(
                            None,
                            "DISCOVERY_MANIFEST must be pure literals — ast.literal_eval cannot resolve names; "
                            f"found name reference '{name_node.id}' at line {name_node.lineno} "
                            f"(inline the value, do not reference module constants like {name_node.id})",
                        )
                    return ManifestExtractResult(
                        None,
                        f"DISCOVERY_MANIFEST is not literal-evaluable: {exc}",
                    )
                try:
                    manifest = DiscoveryManifest.model_validate(data)
                except ValidationError as exc:
                    return ManifestExtractResult(None, f"DISCOVERY_MANIFEST failed validation: {str(exc)[:300]}")
                except Exception as exc:  # pragma: no cover — defensive
                    return ManifestExtractResult(None, f"DISCOVERY_MANIFEST failed validation: {str(exc)[:300]}")
                return ManifestExtractResult(manifest, None)
    return ManifestExtractResult(None, "no DISCOVERY_MANIFEST in script")


def parse_discovery_stdout(stdout: str) -> tuple[dict[str, int], dict[str, int], int | None]:
    """Decode the uniform discovery stdout protocol.

    Returns ``(found_by_label, saved_by_label, total_saved)``.
    ``total_saved`` is ``None`` when the ``DISCOVERY total_saved=`` line
    is absent.
    """
    found: dict[str, int] = {}
    saved: dict[str, int] = {}
    total: int | None = None
    for line in stdout.splitlines():
        line = line.strip()
        m = _TARGET_RE.match(line)
        if m:
            label = m.group(1)
            found[label] = int(m.group(2))
            saved[label] = int(m.group(3))
            continue
        m = _TOTAL_RE.match(line)
        if m:
            total = int(m.group(1))
    return found, saved, total


def _parse_index(href: str, pattern: str) -> int | None:
    """Parse one integer group from ``href`` via ``pattern``."""
    m = re.search(pattern, href)
    if m and m.groups():
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            return None
    return None


def _apply_transform(href: str, targets: ListingTargets) -> str:
    """Apply the listing's ``target_url_transform`` to a raw href."""
    t = targets.target_url_transform
    if t is None or not t.old:
        return href
    return href.replace(t.old, t.new, 1) if t.old in href else href


def _build_label(targets: ListingTargets, index: int | None, ordinal: int, href: str) -> str:
    """Format the label via ``label_template`` with n/i/href keys."""
    return targets.label_template.format(
        n=index if index is not None else "",
        i=ordinal,
        href=href,
    )


async def _collect_listing_hrefs(tab, link_selector: str, listing_url: str) -> list[str]:
    """Return raw hrefs from the listing page via one evaluate."""
    js = (
        "JSON.stringify(Array.from(document.querySelectorAll("
        + json.dumps(link_selector)
        + ")).map(a=>a.getAttribute('href')||''))"
    )
    raw = await tab.evaluate(js)
    if not raw:
        return []
    try:
        hrefs = json.loads(raw)
    except (TypeError, ValueError):
        return []
    base = listing_url
    return [urljoin(base, h) for h in hrefs if h]


async def enumerate_listing_targets(tab, targets: ListingTargets) -> list[DiscoveryTarget]:
    """Navigate the listing page and build the target list.

    Walks ``targets.link_selector`` links, optionally parses an index,
    filters by ``index_range``, applies ``target_url_transform``, and
    builds labels via ``label_template``.
    """
    await tab.get(targets.listing_url)
    await tab.sleep(2.0)
    hrefs = await _collect_listing_hrefs(tab, targets.link_selector, targets.listing_url)
    out: list[DiscoveryTarget] = []
    ordinal = 0
    for href in hrefs:
        index = _parse_index(href, targets.index_from_href) if targets.index_from_href else None
        if targets.index_range is not None and index is not None:
            lo, hi = targets.index_range
            if index < lo or index > hi:
                continue
        ordinal += 1
        url = _apply_transform(href, targets)
        label = _build_label(targets, index, ordinal, href)
        out.append(DiscoveryTarget(label=label, url=url))
    return out
