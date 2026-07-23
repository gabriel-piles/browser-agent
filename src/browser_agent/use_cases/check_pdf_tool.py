"""The ``check_pdf`` tool bound to the validation agent.

Queries ``metadata.db`` for a row whose ``data`` JSON contains a
``pdf_url`` matching the candidate URL, then checks the filesystem
for the downloaded file and validates it is a real PDF.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.pdf_check_request import PdfCheckRequest
from browser_agent.domain.pdf_check_result import PdfCheckResult
from browser_agent.use_cases.metadata_db import parse_row_data
from browser_agent.use_cases.validation_agent_deps import ValidationAgentDeps

_PDF_MAGIC = b"%PDF"
_MIN_VALID_SIZE = 1024


async def check_pdf(ctx: RunContext[ValidationAgentDeps], request: PdfCheckRequest) -> str:
    """Validate a candidate PDF URL against the DB and filesystem.

    The agent calls this for each PDF it discovers during exploration.
    The tool reports whether the URL is in ``metadata.db``, whether
    the file was downloaded, and whether the file is a valid PDF.
    A hard counter caps how many checks one agent turn may perform.
    """
    deps = ctx.deps
    deps.pdf_checks += 1
    if deps.pdf_checks > deps.pdf_check_limit:
        return _limit_reached(deps)
    async with traced_tool("check_pdf", summary=request.url):
        result = _run_check(deps, request)
    return _format_result(result)


def _run_check(deps: ValidationAgentDeps, request: PdfCheckRequest) -> PdfCheckResult:
    """Query the DB and filesystem, returning a :class:`PdfCheckResult`."""
    row = _query_db(deps.db_path, request.url)
    if row is None:
        return PdfCheckResult(
            url=request.url,
            found_in_db=False,
            verdict="missing_from_db",
            notes=request.notes,
        )
    return _check_file(deps, request, row)


def _query_db(db_path: Path, pdf_url: str) -> tuple[str, dict[str, Any]] | None:
    """Return ``(source_url, data_dict)`` for the row matching ``pdf_url``."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT source_url, data FROM metadata WHERE json_extract(data, '$.pdf_url') = ?",
            (pdf_url,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return row[0], parse_row_data(row[1])


def _check_file(
    deps: ValidationAgentDeps,
    request: PdfCheckRequest,
    row: tuple[str, dict[str, Any]],
) -> PdfCheckResult:
    """Check the downloaded file for the DB row and return the result."""
    source_url, data = row
    pdf_filename = data.get("pdf_filename", "")
    file_path = deps.downloads_path / pdf_filename if pdf_filename else Path()
    file_exists = bool(pdf_filename) and file_path.is_file()
    file_size = file_path.stat().st_size if file_exists else 0
    is_valid = _is_valid_pdf(file_path) if file_exists else False
    verdict = _verdict(file_exists, is_valid, file_size)
    return PdfCheckResult(
        url=request.url,
        found_in_db=True,
        db_source_url=source_url,
        pdf_filename=pdf_filename,
        file_exists=file_exists,
        file_size_bytes=file_size,
        is_valid_pdf=is_valid,
        verdict=verdict,
        notes=request.notes,
    )


def _is_valid_pdf(file_path: Path) -> bool:
    """Return True if the file starts with ``%PDF`` and is larger than 1 KB."""
    if file_path.stat().st_size <= _MIN_VALID_SIZE:
        return False
    with file_path.open("rb") as fh:
        return fh.read(5).startswith(_PDF_MAGIC)


def _verdict(file_exists: bool, is_valid: bool, file_size: int) -> str:
    """Return the verdict string from the file checks."""
    if not file_exists:
        return "file_not_downloaded"
    if not is_valid or file_size <= _MIN_VALID_SIZE:
        return "corrupt_file"
    return "present"


def _limit_reached(deps: ValidationAgentDeps) -> str:
    return (
        f"# PDF check limit reached ({deps.pdf_check_limit}).\n"
        "You have checked the maximum number of PDFs. STOP calling this tool.\n"
        "Emit the final ValidationReport now using the results you have gathered."
    )


def _format_result(result: PdfCheckResult) -> str:
    lines = [f"# PDF Check: {result.verdict}"]
    lines.append(f"# URL: {result.url}")
    lines.append(f"# found_in_db: {result.found_in_db}")
    lines.append(f"# db_source_url: {result.db_source_url}")
    lines.append(f"# pdf_filename: {result.pdf_filename}")
    lines.append(f"# file_exists: {result.file_exists}")
    lines.append(f"# file_size: {result.file_size_bytes} bytes")
    lines.append(f"# is_valid_pdf: {result.is_valid_pdf}")
    if result.notes:
        lines.append(f"# notes: {result.notes}")
    return "\n".join(lines)
