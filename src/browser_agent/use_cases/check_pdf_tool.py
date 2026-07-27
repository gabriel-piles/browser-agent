"""The ``check_pdf`` tool bound to the verification agent.

Queries ``metadata.db`` for a row whose ``data`` JSON contains a
``pdf_url`` matching the candidate URL (normalized, with a suffix
fallback), then checks the filesystem for the downloaded file and
validates it is a real PDF (magic + %%EOF). A hard counter caps how
many checks one agent turn may perform.

Now a spot-check for *newly discovered* candidates: the exhaustive
inventory comes from the deterministic reconciler, so the limit is no
longer a correctness ceiling. URL matching and filename derivation are
shared with the reconciler via :class:`PdfUrlMatcher` so the two
cannot drift.
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
from browser_agent.use_cases.pdf_integrity_validator import PdfIntegrityValidator
from browser_agent.use_cases.pdf_url_matcher import PdfUrlMatcher
from browser_agent.use_cases.declare_paths_tool import remaining_paths_block
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps


async def check_pdf(ctx: RunContext[VerificationAgentDeps], request: PdfCheckRequest) -> str:
    """Validate a candidate PDF URL against the DB and filesystem.

    The agent calls this for each newly discovered PDF. The tool reports
    whether the URL is in ``metadata.db``, whether the file was
    downloaded, and whether the file is a valid PDF (magic + %%EOF).
    A hard counter caps how many checks one agent turn may perform.
    """
    deps = ctx.deps
    if deps.pdf_checks >= deps.pdf_check_limit:
        return _limit_reached(deps)
    async with traced_tool("check_pdf", summary=request.url):
        result = _run_check(deps, request)
        deps.pdf_results.append(result)
    return _format_result(result, deps)


def _run_check(deps: VerificationAgentDeps, request: PdfCheckRequest) -> PdfCheckResult:
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


def _query_db(db_path: Path, pdf_url: str) -> tuple[str, dict[str, Any], str] | None:
    """Return ``(source_url, data_dict, match_mode)`` for the matching row.

    Tries normalized equality first, then a path/basename suffix match,
    so a candidate recorded under a different URL form is reconciled
    rather than reported as a false ``missing_from_db``.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT source_url, data FROM metadata",
        ).fetchall()
    finally:
        conn.close()
    for source_url, data_json in rows:
        data = parse_row_data(data_json)
        stored = data.get("pdf_url", "") or ""
        match = PdfUrlMatcher.match(pdf_url, stored)
        if match.matched:
            return source_url, data, match.mode
    return None


def _check_file(
    deps: VerificationAgentDeps,
    request: PdfCheckRequest,
    row: tuple[str, dict[str, Any], str],
) -> PdfCheckResult:
    """Check the downloaded file for the DB row and return the result."""
    source_url, data, match_mode = row
    db_filename = data.get("pdf_filename", "") or ""
    norm_name, orig_name = PdfUrlMatcher.expected_filenames_for(request.url)
    file_path, used_name = _resolve_file(deps, norm_name, orig_name, db_filename)
    if file_path is None or not file_path.is_file():
        return PdfCheckResult(
            url=request.url,
            found_in_db=True,
            db_source_url=source_url,
            pdf_filename=db_filename,
            file_exists=False,
            verdict="file_not_downloaded",
            notes=request.notes,
        )
    integrity = PdfIntegrityValidator.validate(file_path)
    verdict = _verdict(integrity.is_valid, integrity.is_suspiciously_small)
    return PdfCheckResult(
        url=request.url,
        found_in_db=True,
        db_source_url=source_url,
        pdf_filename=used_name,
        file_exists=True,
        file_size_bytes=integrity.file_size,
        is_valid_pdf=integrity.is_valid,
        verdict=verdict,
        notes=integrity.notes + (f" [url-match: {match_mode}]" if match_mode else ""),
    )


def _resolve_file(
    deps: VerificationAgentDeps,
    norm: str,
    orig: str,
    db_filename: str,
) -> tuple[Path | None, str]:
    """Return the first existing candidate path + the name that matched."""
    candidates = [norm]
    if orig and orig != norm:
        candidates.append(orig)
    if db_filename and db_filename not in candidates:
        candidates.append(db_filename)
    for name in candidates:
        path = deps.downloads_path / name
        if path.is_file():
            return path, name
    return None, ""


def _verdict(is_valid: bool, suspiciously_small: bool) -> str:
    """Return the verdict string from the integrity checks."""
    if not is_valid:
        return "corrupt_file"
    if suspiciously_small:
        return "suspiciously_small"
    return "present"


def _limit_reached(deps: VerificationAgentDeps) -> str:
    return (
        f"# PDF check limit reached ({deps.pdf_check_limit}).\n"
        "You have checked the maximum number of PDFs. STOP calling this tool.\n"
        "The deterministic reconciler inventory already covers every DB row; "
        "emit the final VerificationReport now using the evidence you have gathered."
    )


def _format_result(result: PdfCheckResult, deps: VerificationAgentDeps) -> str:
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
    lines.append(remaining_paths_block(deps))
    return "\n".join(lines)
