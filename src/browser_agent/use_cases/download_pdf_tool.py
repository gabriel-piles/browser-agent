from __future__ import annotations

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.download_pdf_request import DownloadPdfRequest
from browser_agent.domain.download_result import DownloadResult
from browser_agent.use_cases.agent_deps import AgentDeps


async def download_pdf(ctx: RunContext[AgentDeps], request: DownloadPdfRequest) -> str:
    """Download a PDF file from ``request.url`` using the browser's cookies.

    Uses curl_cffi with Chrome TLS fingerprint impersonation and shares
    cookies from the active browser session, so it can fetch PDFs behind
    login walls or anti-bot protection. Returns metadata (saved path,
    file size, content type) — NOT the file content.

    Use this when the task involves downloading PDF documents. The
    browser session's cookies are shared automatically, so the download
    works for authenticated or anti-bot-protected URLs.

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
    lines = [f"# Download: {'SUCCESS' if result.success else 'FAILED'}"]
    lines.append(f"# URL: {result.url}")
    lines.append(f"# saved_path: {result.saved_path}")
    lines.append(f"# file_size: {result.file_size_bytes} bytes")
    lines.append(f"# content_type: {result.content_type}")
    if result.error:
        lines.append(f"# ERROR: {result.error}")
    return "\n".join(lines)
