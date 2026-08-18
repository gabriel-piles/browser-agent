"""Shape-agnostic self-check verifier for discovery scripts.

Replaces the old ``UNDER-COLLECTED`` string sniff: verifies a discovery
script's run through the uniform stdout protocol + a fresh scratch DB
row count. Returns a list of hard failure lines (empty = pass).
"""

from __future__ import annotations

from loguru import logger

from browser_agent.domain.discovery_manifest import DiscoveryManifest
from browser_agent.use_cases.discovery_manifest_parser import parse_discovery_stdout


class DiscoverySelfCheckVerifier:
    """Verify a discovery script's stdout against its manifest + DB rows."""

    def verify(self, manifest: DiscoveryManifest, stdout: str, db_rows: int) -> list[str]:
        """Return hard failure lines (empty list = pass)."""
        found, saved, total = parse_discovery_stdout(stdout)
        failures: list[str] = []
        failures.extend(self._check_total(total))
        if failures:
            return failures
        failures.extend(self._check_zero(total))
        failures.extend(self._check_db(total, db_rows))
        failures.extend(self._check_min(found, manifest))
        failures.extend(self._check_max(saved, found, manifest))
        self._log_dedup(total, db_rows)
        return failures

    def _check_total(self, total: int | None) -> list[str]:
        if total is None:
            return ["missing DISCOVERY total_saved line"]
        return []

    def _check_zero(self, total: int | None) -> list[str]:
        if total == 0:
            return ["discovery collected zero links (total_saved=0)"]
        return []

    def _check_db(self, total: int | None, db_rows: int) -> list[str]:
        if total and db_rows == 0:
            return [
                f"script reported total_saved={total} but discovered_links table is empty — save_discovered_link not writing"
            ]
        return []

    def _check_min(self, found: dict[str, int], manifest: DiscoveryManifest) -> list[str]:
        if manifest.min_per_target <= 0:
            return []
        out: list[str] = []
        for label, n in found.items():
            if n < manifest.min_per_target:
                out.append(f"target {label} under-collected: found={n} < min={manifest.min_per_target}")
        return out

    def _check_max(self, saved: dict[str, int], found: dict[str, int], manifest: DiscoveryManifest) -> list[str]:
        out: list[str] = []
        for label, s in saved.items():
            f = found.get(label, 0)
            if f > 0 and s > f * manifest.max_links_per_item:
                out.append(f"target {label} saved={s} exceeds found={f} * max_links_per_item={manifest.max_links_per_item}")
        return out

    def _log_dedup(self, total: int | None, db_rows: int) -> None:
        if total is not None and db_rows < total:
            logger.info(
                "discovery self-check note: db_rows={db} < total_saved={t} (possible cross-target URL dedup — not a failure)",
                db=db_rows,
                t=total,
            )
