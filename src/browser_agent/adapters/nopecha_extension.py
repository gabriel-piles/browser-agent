"""Provision the NopeCHA CAPTCHA-solver extension for clean Chromium launches.

NopeCHA is closed-source; only prebuilt zips ship on GitHub releases.
The ``chromium_automation.zip`` build has no popup and is pre-configured
by editing ``manifest.json`` (API key + which CAPTCHA types are
enabled) — the recommended build for automation per NopeCHA's
"Extension for Experts" guide.

This module downloads that build once, unzips it into a cached
directory under the project, patches ``manifest.json`` with the
operator's key and the Cloudflare-Turnstile auto-solve toggles, and
returns the directory so the launcher can pass it to Chromium via
``--load-extension``. When ``NOPECHA_ENABLED`` is unset the provider
is a no-op and returns ``None`` — the launch stays stealth-clean with
zero extension flags.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
import zipfile
from functools import cached_property
from pathlib import Path

from loguru import logger

from browser_agent.configuration import (
    NOPECHA_CACHE_DIR,
    NOPECHA_DOWNLOAD_URL,
    NOPECHA_ENABLED,
    NOPECHA_KEY,
)


class NopechaExtension:
    """Idempotent provider for the cached, pre-configured NopeCHA extension."""

    @cached_property
    def extension_path(self) -> Path | None:
        return self.ensure_ready()

    def ensure_ready(self) -> Path | None:
        if not NOPECHA_ENABLED:
            return None
        manifest = NOPECHA_CACHE_DIR / "manifest.json"
        if manifest.exists():
            return NOPECHA_CACHE_DIR
        return self._download_and_patch()

    def _download_and_patch(self) -> Path | None:
        try:
            self._download_extract()
            self._patch_manifest()
        except Exception as exc:
            logger.warning("nopecha: provisioning failed — {exc}", exc=exc)
            shutil.rmtree(NOPECHA_CACHE_DIR, ignore_errors=True)
            return None
        logger.info("nopecha: extension provisioned at {dir}", dir=NOPECHA_CACHE_DIR)
        return NOPECHA_CACHE_DIR

    def _download_extract(self) -> None:
        with urllib.request.urlopen(NOPECHA_DOWNLOAD_URL) as resp:  # noqa: S310
            with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
                shutil.copyfileobj(resp, tmp.file)
                tmp.flush()
                self._extract_flattened(tmp.name)

    def _extract_flattened(self, zip_path: str) -> None:
        shutil.rmtree(NOPECHA_CACHE_DIR, ignore_errors=True)
        NOPECHA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
            top = self._single_top_dir(members)
            for member in members:
                if member.endswith("/"):
                    continue
                rel = member.split(f"{top}/", 1)[1] if top else member
                target = NOPECHA_CACHE_DIR / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    @staticmethod
    def _single_top_dir(members: list[str]) -> str | None:
        tops = {m.split("/", 1)[0] for m in members if m}
        return next(iter(tops)) if len(tops) == 1 else None

    def _patch_manifest(self) -> None:
        manifest = NOPECHA_CACHE_DIR / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        nopecha = data.setdefault("nopecha", {})
        nopecha["key"] = NOPECHA_KEY
        nopecha["turnstile_auto_solve"] = True
        nopecha["turnstile_auto_open"] = True
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
