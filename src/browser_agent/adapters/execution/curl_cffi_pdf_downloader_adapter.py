from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from loguru import logger

from browser_agent.configuration import PROJECT_ROOT
from browser_agent.domain.download_result import DownloadResult
from browser_agent.ports.pdf_downloader_port import PdfDownloaderPort

_DEFAULT_DOWNLOADS_PATH = PROJECT_ROOT / "data" / "downloads"

_IMPERSONATE = "chrome"
_TIMEOUT_S = 60.0
_MAX_SIZE_BYTES = 100 * 1024 * 1024


class CurlCffiPdfDownloaderAdapter(PdfDownloaderPort):
    """Downloads PDFs via curl_cffi with Chrome TLS fingerprint impersonation.

    This is one of two PDF download strategies. It uses curl_cffi's
    ``AsyncSession`` with ``impersonate="chrome"`` to send HTTP
    requests with Chrome's TLS fingerprint. When browser cookies are
    provided, they are attached to the request so authenticated /
    cookie-gated downloads work.

    Works for sites that do not block non-browser HTTP clients. For
    sites behind Cloudflare/Akamai JS-challenge WAF, use the
    browser-fetch strategy (``download_pdf_browser``) instead.
    """

    def __init__(self, downloads_path: Path = _DEFAULT_DOWNLOADS_PATH) -> None:
        self._downloads_path = downloads_path
        self._downloads_path.mkdir(parents=True, exist_ok=True)

    async def download(
        self,
        url: str,
        cookies: list[dict[str, str]] | None = None,
        save_path: str | None = None,
    ) -> DownloadResult:
        """Download the PDF at ``url``, return metadata (not file content).

        Idempotent: if the target file already exists and is non-empty,
        the download is skipped and a ``DownloadResult`` with
        ``success=True, skipped=True`` is returned.  Writes are atomic
        (temp + rename) so a crash mid-write never leaves a partial
        file at the target path.
        """
        from curl_cffi import AsyncSession

        path = self._resolve_save_path(save_path, url)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        existing = self._existing_size(path)
        if existing > 0:
            return DownloadResult(
                success=True,
                saved_path=str(path),
                url=url,
                content_type="",
                file_size_bytes=existing,
                skipped=True,
                reason="already_downloaded",
            )

        cookie_dict = self._build_cookie_dict(cookies)
        try:
            async with AsyncSession() as s:
                r = await s.get(url, impersonate=_IMPERSONATE, cookies=cookie_dict, timeout=_TIMEOUT_S)
        except Exception as e:
            return DownloadResult(
                success=False, saved_path=str(path), url=url, content_type="", file_size_bytes=0, error=str(e)
            )
        error = self._validate_response(r, len(r.content) if r.content else 0)
        if error:
            return DownloadResult(
                success=False, saved_path=str(path), url=url, content_type="", file_size_bytes=0, error=error
            )
        self._write_atomic(path, r.content)
        return DownloadResult(
            success=True,
            saved_path=str(path),
            url=url,
            content_type=r.headers.get("content-type", ""),
            file_size_bytes=len(r.content),
            skipped=False,
            reason="downloaded",
        )

    @staticmethod
    def _build_cookie_dict(cookies: list[dict[str, str]] | None) -> dict[str, str]:
        """Convert the browser cookie list to a simple {name: value} dict."""
        if not cookies:
            return {}
        return {c["name"]: c["value"] for c in cookies if c.get("name") and c.get("value")}

    def _resolve_save_path(self, save_path: str | None, url: str) -> Path:
        """Derive a safe filename, stripping any directory components."""
        if save_path:
            return self._downloads_path / Path(save_path).name
        tail = url.rstrip("/").split("/")[-1]
        if "." in tail and not tail.startswith("?"):
            return self._downloads_path / Path(tail).name
        return self._downloads_path / f"download_{int(time.time())}.pdf"

    @staticmethod
    def _existing_size(path: Path) -> int:
        """Return existing on-disk size in bytes, or 0 when missing/empty."""
        try:
            st = path.stat()
        except (FileNotFoundError, OSError):
            return 0
        return st.st_size if st.st_size > 0 else 0

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        """Write ``data`` to ``path`` atomically (temp + rename)."""
        import os as _os

        part = path.with_name(path.name + ".part")
        try:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass
            with open(part, "wb") as f:
                f.write(data)
                f.flush()
                _os.fsync(f.fileno())
            _os.replace(part, path)
        except Exception:
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _validate_response(r: Any, body_len: int) -> str:
        """Return an error string (empty on success)."""
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        if body_len == 0:
            return "empty response body"
        if body_len > _MAX_SIZE_BYTES:
            return f"file exceeds {_MAX_SIZE_BYTES // (1024 * 1024)} MB limit"
        ct = r.headers.get("content-type", "")
        if "pdf" not in ct.lower():
            logger.warning("download_pdf: content-type is {!r}, saving anyway", ct)
        return ""
