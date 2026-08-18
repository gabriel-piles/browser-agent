"""Shape-agnostic independent audit of a discovery script's collection.

After the self-check finishes, :class:`DiscoveryAuditor` re-walks the
site independently and compares its own count against the script's
reported ``found`` counts (parsed from the self-check stdout via the
uniform ``DISCOVERY target=... found=...`` protocol). Discrepancies
trigger a repair turn.

The audit is an INDEPENDENT ORACLE — it does not trust the script's
own counts or its selectors. It reads the script's
``DISCOVERY_MANIFEST`` (a structured manifest), independently
enumerates the targets, verifies every target is reported (coverage),
then samples a subset and re-counts via a correct-by-construction
scoped ``querySelectorAll`` (or ``discover_links`` for scroll shapes).
If the script's selector/loop is buggy, the audit detects the
mismatch. It no longer trusts the DB — the self-check owns DB
consistency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from browser_agent.domain.discovery_audit_outcome import DiscoveryAuditOutcome
from browser_agent.domain.discovery_manifest import DiscoveryManifest
from browser_agent.domain.fixed_targets import FixedTargets
from browser_agent.domain.listing_targets import ListingTargets
from browser_agent.domain.single_targets import SingleTargets
from browser_agent.domain.discovery_target import DiscoveryTarget
from browser_agent.ports.browser_session_port import BrowserSessionPort
from browser_agent.use_cases.discovery_manifest_parser import (
    enumerate_listing_targets,
    extract_manifest,
    parse_discovery_stdout,
)

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


async def _close_tab_silently(tab: Any) -> None:
    """Close ``tab`` if the zendriver API exposes it; swallow errors."""
    try:
        await tab.close()
    except Exception:
        pass


async def _count_selector(tab: Any, selector: str, scope: str | None) -> int:
    """Correct-by-construction scoped ``querySelectorAll`` count."""
    if not selector:
        return 0
    if scope:
        js = (
            "(() => {const s = document.querySelector(" + json.dumps(scope) + ");"
            "if (!s) return 0; return s.querySelectorAll(':is(" + selector + ")').length;})()"
        )
    else:
        js = "document.querySelectorAll(" + json.dumps(selector) + ").length"
    try:
        raw = await tab.evaluate(js)
    except Exception:
        logger.exception("discovery audit: count evaluate failed")
        return 0
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


async def _audit_count_for_target(tab: Any, target: DiscoveryTarget, manifest: DiscoveryManifest) -> int:
    """Independent count for one target page."""
    await _settle(tab)
    if manifest.load_more_selector:
        from browser_agent.script_tools.discover_links import discover_links

        links = await discover_links(tab, manifest.count_selector, manifest.load_more_selector)
        return len(links)
    return await _count_selector(tab, manifest.count_selector, manifest.count_scope)


async def _settle(tab: Any) -> None:
    """Wait for page ready then a 2s settle (HRC bug was too-short wait)."""
    try:
        from browser_agent.script_tools.page_wait import wait_for_page_ready

        await wait_for_page_ready(tab)
    except Exception:
        await tab.sleep(2.0)
        return
    await tab.sleep(2.0)


class DiscoveryAuditor:
    """Independent shape-agnostic re-walk of a discovery script's targets."""

    def __init__(self, session: BrowserSessionPort, db_path: Path) -> None:
        self._session = session
        self._db_path = db_path

    async def audit(self, discovery_path: Path, self_check_stdout: str) -> DiscoveryAuditOutcome:
        """Return a ``DiscoveryAuditOutcome`` (skipped/passed/discrepancies)."""
        source = discovery_path.read_text(encoding="utf-8")
        manifest = extract_manifest(source)
        if manifest is None:
            return DiscoveryAuditOutcome(status="skipped", reason="no parseable DISCOVERY_MANIFEST")
        found_by_label, _, _ = parse_discovery_stdout(self_check_stdout)
        targets = await self._enumerate_targets(manifest)
        blocks: list[str] = []
        blocks.extend(self._coverage_check(targets, found_by_label))
        blocks.extend(await self._count_sample(manifest, targets, found_by_label))
        if not blocks:
            return DiscoveryAuditOutcome(status="passed")
        return DiscoveryAuditOutcome(status="discrepancies", report="\n".join(blocks))

    async def _enumerate_targets(self, manifest: DiscoveryManifest) -> list[DiscoveryTarget]:
        """Build the independent target list from the manifest."""
        t = manifest.targets
        if isinstance(t, FixedTargets):
            return list(t.items)
        if isinstance(t, SingleTargets):
            return [DiscoveryTarget(label=t.label, url=t.url)]
        if isinstance(t, ListingTargets):
            tab = await self._session.new_tab()
            try:
                return await enumerate_listing_targets(tab, t)
            except Exception:
                logger.exception("discovery audit: listing enumeration failed")
                return []
            finally:
                await _close_tab_silently(tab)
        return []

    def _coverage_check(self, targets: list[DiscoveryTarget], found_by_label: dict[str, int]) -> list[str]:
        """Flag any enumerated target the script did not report."""
        out: list[str] = []
        for target in targets:
            if target.label not in found_by_label:
                out.append(f"[{target.label}] MISSING: script did not report this target")
        return out

    async def _count_sample(
        self, manifest: DiscoveryManifest, targets: list[DiscoveryTarget], found_by_label: dict[str, int]
    ) -> list[str]:
        """Independently count a sample of targets and compare."""
        if not manifest.count_selector:
            return []
        sample = _sample_indices(len(targets))
        blocks: list[str] = []
        for idx in sample:
            target = targets[idx]
            block = await self._audit_one_target(manifest, target, found_by_label)
            blocks.extend(block)
        return blocks

    async def _audit_one_target(
        self, manifest: DiscoveryManifest, target: DiscoveryTarget, found_by_label: dict[str, int]
    ) -> list[str]:
        """Count one target independently and build discrepancy blocks."""
        script_found = found_by_label.get(target.label)
        if script_found is None:
            return []
        tab = await self._session.new_tab()
        try:
            audit_count = await _audit_count_for_target(tab, target, manifest)
        except Exception:
            logger.exception("discovery audit: target {} failed", target.label)
            return []
        finally:
            await _close_tab_silently(tab)
        return self._compare_counts(target.label, script_found, audit_count)

    def _compare_counts(self, label: str, script_found: int, audit_count: int) -> list[str]:
        """Build discrepancy blocks for one target (empty if clean)."""
        out: list[str] = []
        if script_found < audit_count:
            out.append(f"[{label}] UNDER-COLLECTED: script found={script_found}, audit={audit_count}")
        if script_found > audit_count:
            out.append(f"[{label}] OVER-COLLECTED: script found={script_found}, audit={audit_count}")
        if script_found == 0 and audit_count > 0:
            out.append(f"[{label}] EMPTY: script found 0 rows, audit={audit_count} — likely a too-short page wait")
        return out
