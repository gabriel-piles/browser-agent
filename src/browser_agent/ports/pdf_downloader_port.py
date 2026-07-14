from __future__ import annotations

from abc import ABC, abstractmethod

from browser_agent.domain.download_result import DownloadResult


class PdfDownloaderPort(ABC):
    """Downloads a PDF from a URL, optionally using browser cookies."""

    @abstractmethod
    async def download(
        self,
        url: str,
        cookies: list[dict[str, str]] | None = None,
        save_path: str | None = None,
    ) -> DownloadResult:
        """Download the PDF at ``url`` and return metadata.

        Implementations MUST:
        - attach ``cookies`` (from the browser session) when provided;
        - save the file to the downloads directory;
        - return a DownloadResult with success=False and error on failure.

        The curl_cffi implementation uses Chrome TLS impersonation and
        works for sites without JS-challenge anti-bot. For sites behind
        Cloudflare/Akamai WAF, use the browser-fetch strategy
        (``download_pdf_browser`` in the emitted helper) instead.
        """
