"""Pure-Python PDF integrity checks used by the reconciler and ``check_pdf``.

No ``pypdf`` dependency (it is not in ``pyproject.toml``). Validates the
``%PDF`` magic, the ``%%EOF`` marker near the tail, and flags a
suspiciously-small file separately from a structurally invalid one.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.domain.pdf_integrity_result import PdfIntegrityResult

_PDF_MAGIC = b"%PDF"
_EOF_MARKER = b"%%EOF"
_EOF_TAIL_BYTES = 2048
_SUSPICIOUS_SIZE = 1024


class PdfIntegrityValidator:
    """Validate PDF files without third-party dependencies."""

    @staticmethod
    def validate(file_path: Path) -> PdfIntegrityResult:
        """Return the integrity verdict for ``file_path`` (missing => all-false)."""
        if not file_path.is_file():
            return PdfIntegrityResult(notes="file missing")
        size = file_path.stat().st_size
        magic = PdfIntegrityValidator._has_magic(file_path)
        eof = PdfIntegrityValidator._has_eof(file_path, size)
        valid = magic and eof
        suspicious = valid and size <= _SUSPICIOUS_SIZE
        return PdfIntegrityResult(
            file_size=size,
            has_pdf_magic=magic,
            has_eof_marker=eof,
            is_valid=valid,
            is_suspiciously_small=suspicious,
            notes=PdfIntegrityValidator._notes(magic, eof, size, valid),
        )

    @staticmethod
    def _has_magic(file_path: Path) -> bool:
        with file_path.open("rb") as fh:
            return fh.read(5).startswith(_PDF_MAGIC)

    @staticmethod
    def _has_eof(file_path: Path, size: int) -> bool:
        tail = min(_EOF_TAIL_BYTES, size)
        with file_path.open("rb") as fh:
            fh.seek(-tail, 2) if tail else fh.seek(0)
            return _EOF_MARKER in fh.read(tail)

    @staticmethod
    def _notes(magic: bool, eof: bool, size: int, valid: bool) -> str:
        if valid:
            flag = " (suspiciously small)" if size <= _SUSPICIOUS_SIZE else ""
            return f"valid{flag} ({size} bytes)"
        missing: list[str] = []
        if not magic:
            missing.append("no %PDF magic")
        if not eof:
            missing.append("no %%EOF marker")
        return f"invalid ({size} bytes): " + ", ".join(missing)


def find_identical_size_clusters(file_sizes: dict[Path, int]) -> list[list[Path]]:
    """Return groups of >=2 paths sharing the exact same non-zero byte size."""
    by_size: dict[int, list[Path]] = {}
    for path, size in file_sizes.items():
        if size > 0:
            by_size.setdefault(size, []).append(path)
    return [paths for paths in by_size.values() if len(paths) >= 2]
