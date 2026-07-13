from __future__ import annotations

from pydantic import BaseModel, Field


class DownloadPdfRequest(BaseModel):
    """Parameters for the download_pdf agent tool.

    - ``url``  — direct URL to the PDF file.
    - ``save_path`` — optional filename (no path). If omitted, derived
      from the URL's last path segment or a timestamp fallback.
    """

    url: str = Field(description="Direct URL to the PDF file to download.")
    save_path: str | None = Field(
        default=None,
        description="Optional filename (no directory). If omitted, derived from the URL.",
    )
