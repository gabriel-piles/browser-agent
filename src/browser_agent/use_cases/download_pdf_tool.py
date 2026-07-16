from __future__ import annotations

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.download_pdf_request import DownloadPdfRequest
from browser_agent.domain.download_result import DownloadResult
from browser_agent.use_cases.agent_deps import AgentDeps


async def download_pdf(ctx: RunContext[AgentDeps], request: DownloadPdfRequest) -> str:
    """Test-download a PDF from ``request.url`` using curl_cffi (Chrome TLS impersonation).

    This is a PROBE: it tells you whether the site's PDFs can be
    downloaded with ``curl_cffi`` (fast, no browser needed in the
    final script) or whether the site blocks non-browser HTTP
    clients (Cloudflare/Akamai WAF), in which case the final script
    must use ``download_pdf_browser(tab, url, save_path)`` instead.

    Shares cookies from the active browser session. Returns metadata
    (saved path, file size, content type) — NOT the file content.

    Decision rule:
      - SUCCESS → set ``pdf_download_strategy="curl_cffi"``; the
        final script uses ``download_pdf_curl_cffi(url, save_path,
        tab)``.
      - FAILED (HTTP 403/401/empty) → set
        ``pdf_download_strategy="browser_fetch"``; the final script
        uses ``download_pdf_browser(tab, url, save_path)``.

    Parameters:
      url       — direct URL to the PDF file.
      save_path — optional filename (no directory). If omitted, derived
                  from the URL.
    """
    cookies = await ctx.deps.browser_session.get_cookies()
    async with traced_tool("download_pdf"):
        result: DownloadResult = await ctx.deps.pdf_downloader.download(
            url=request.url,
            cookies=cookies,
            save_path=request.save_path,
        )
    return _format_result(result)


def _format_result(result: DownloadResult) -> str:
    if result.skipped:
        status = "SKIPPED"
    elif result.success:
        status = "SUCCESS"
    else:
        status = "FAILED"
    lines = [f"# Download: {status}"]
    lines.append(f"# URL: {result.url}")
    lines.append(f"# saved_path: {result.saved_path}")
    lines.append(f"# file_size: {result.file_size_bytes} bytes")
    lines.append(f"# content_type: {result.content_type}")
    if result.skipped:
        lines.append(f"# reason: {result.reason or 'already_downloaded'}")
    if result.error:
        lines.append(f"# ERROR: {result.error}")
    return "\n".join(lines)
